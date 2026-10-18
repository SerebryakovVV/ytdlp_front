from __future__ import annotations

import json
import os
import queue
import subprocess
import tempfile
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .auth import AuthenticationError, Cancelled, ChromeSession, SignInRequired, write_netscape
from .config import Settings, needs_youtube_login, validate_tools, validate_url
from .process_tree import ProcessTree


@dataclass
class Track:
    key: str
    id: str
    title: str
    url: str
    source: str
    index: int
    status: str = "Queued"
    progress: str = "—"
    detail: str = "Waiting"
    path: str = ""

    @classmethod
    def from_info(cls, info: dict, source: str, index: int) -> Track:
        identifier = str(info.get("id") or "")
        if not identifier:
            raise ValueError("Playlist entry has no track ID.")
        url = info.get("webpage_url") or info.get("url") or ""
        if source == "youtube" and not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={identifier}"
        host = (urlparse(url).hostname or "").lower()
        allowed = ("youtube.com", "youtu.be") if source == "youtube" else ("soundcloud.com",)
        if not any(host == domain or host.endswith("." + domain) for domain in allowed):
            raise ValueError("Playlist entry has no supported track URL.")
        return cls(f"{source}:{identifier}", identifier, info.get("title") or f"Track {identifier}",
                   url, source, index)


class History:
    def __init__(self, destination: Path):
        self.destination = destination.resolve()
        self.path = self.destination / ".music-shelf" / "history.json"
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(self.data, dict):
                    raise ValueError()
            except (ValueError, OSError) as error:
                raise ValueError(f"Download history cannot be read: {self.path}. Rename it to keep a backup, then retry.") from error
        else:
            self.data = {}

    def existing(self, key: str) -> Path | None:
        relative = self.data.get(key)
        if not isinstance(relative, str):
            return None
        path = (self.destination / relative).resolve()
        # History can only refer to MP3s under this destination.
        if path.is_relative_to(self.destination) and path.suffix.lower() == ".mp3" and path.is_file():
            return path
        return None

    def record(self, key: str, path: Path) -> None:
        self.data[key] = str(path.resolve().relative_to(self.destination))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)


def parse_event(line: str) -> tuple[str, dict] | None:
    for prefix, kind in (("SHELF_PROGRESS ", "progress"), ("SHELF_POST ", "post"),
                         ("SHELF_DONE ", "done"), ("SHELF_TITLE ", "title")):
        if line.startswith(prefix):
            try:
                payload = json.loads(line[len(prefix):])
                return (kind, payload) if isinstance(payload, dict) else None
            except ValueError:
                return None
    return None


def progress_label(payload: dict) -> tuple[str, str]:
    downloaded = payload.get("downloaded_bytes") or 0
    total = payload.get("total_bytes") or payload.get("total_bytes_estimate")
    percentage = f"{min(100, downloaded / total * 100):.0f}%" if isinstance(total, (int, float)) and total > 0 else "…"
    pieces = []
    speed = payload.get("speed")
    if isinstance(speed, (int, float)) and speed > 0:
        pieces.append(f"{speed / 1024 / 1024:.1f} MB/s" if speed >= 1024 * 1024 else f"{speed / 1024:.0f} KB/s")
    eta = payload.get("eta")
    if isinstance(eta, (int, float)) and eta >= 0:
        seconds = int(eta)
        pieces.append(f"{seconds // 60}:{seconds % 60:02d} left")
    return percentage, " · ".join(pieces) or "Downloading audio"


class DownloadWorker:
    def __init__(self, settings: Settings, source: str, home: Path, session: ChromeSession,
                 events: queue.Queue, *, retry_tracks: list[Track] | None = None):
        self.settings = replace(settings)
        self.source = source
        self.home = home
        self.session = session
        self.events = events
        self.cancel = threading.Event()
        self._process_lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._process_tree: ProcessTree | None = None
        self.tracks = retry_tracks or []
        self.retrying = retry_tracks is not None

    def emit(self, kind: str, **data) -> None:
        self.events.put((kind, data))

    def update(self, track: Track, **data) -> None:
        for name, value in data.items():
            setattr(track, name, value)
        self.emit("track", track=replace(track))

    def stop(self) -> None:
        self.cancel.set()
        with self._process_lock:
            process = self._process
            if process and process.poll() is None:
                if self._process_tree and self._process_tree.terminate():
                    return
                if os.name == "nt":
                    try:
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                if process.poll() is None:
                    process.kill()

    def base_command(self, cookie_args: list[str]) -> list[str]:
        result = [self.settings.ytdlp_path, "--ignore-config", "--encoding", "utf-8",
                  "--no-colors", "--no-abort-on-error", "--socket-timeout", "25",
                  "--retries", "3", "--extractor-retries", "3", *cookie_args]
        if self.source == "youtube":
            result += ["--js-runtimes", f"node:{self.settings.node_path}"]
        return result

    def run_process(self, args: list[str], on_line: Callable[[str], None]) -> tuple[int, list[str]]:
        errors: list[str] = []
        with self._process_lock:
            if self.cancel.is_set():
                raise Cancelled()
            self._process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                             stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
                                             bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            process = self._process
            self._process_tree = ProcessTree(process)
        try:
            assert process.stdout is not None
            for line in process.stdout:
                line = line.rstrip("\r\n")
                if self.cancel.is_set():
                    raise Cancelled()
                if line.startswith("ERROR:"):
                    errors.append(line[6:].strip())
                on_line(line)
            code = process.wait()
            if self.cancel.is_set():
                raise Cancelled()
            return code, errors
        finally:
            if process.poll() is None:
                self.stop()
                process.wait(timeout=15)
            if process.stdout:
                process.stdout.close()
            with self._process_lock:
                if self._process_tree:
                    self._process_tree.close()
                    self._process_tree = None
                self._process = None

    def authenticate(self, url: str, cookie_path: Path) -> list[str]:
        if self.source == "soundcloud" and not self.settings.soundcloud_auth:
            return []
        mode = self.settings.youtube_auth if self.source == "youtube" else "Automatic Chrome"
        if mode == "No login (public playlist)":
            if needs_youtube_login(url):
                raise AuthenticationError("Liked videos are private. Select Automatic Chrome in Settings.")
            return []
        if mode == "Firefox":
            return ["--cookies-from-browser", "firefox"]
        if mode == "Cookies file":
            path = Path(self.settings.cookie_file)
            if not path.is_file():
                raise AuthenticationError("Select an existing Netscape cookies file in Settings.")
            # yt-dlp can rewrite its cookie jar; keep the user's original untouched.
            cookie_path.write_bytes(path.read_bytes())
            return ["--cookies", str(cookie_path)]
        self.emit("phase", text="Refreshing your Chrome session…")
        try:
            cookies = self.session.cookie_snapshot(self.source, self.cancel)
        except SignInRequired:
            self.emit("phase", text="Sign in in Chrome, close that window, then click Continue in Music Shelf.")
            cookies = self.session.sign_in(self.source, url, self.cancel,
                                           lambda source, confirmed, cancel: self.emit("login_required", source=source,
                                                                                       confirmed=confirmed, cancel=cancel))
        if not write_netscape(cookies, cookie_path, self.source):
            raise AuthenticationError("No cookies were available. Reconnect your account.")
        self.emit("connected", source=self.source)
        return ["--cookies", str(cookie_path)]

    def scan(self, url: str, base: list[str]) -> bool:
        self.emit("phase", text="Reading your playlist… Songs will appear as they are found.")
        seen: set[str] = set()

        def line_received(line: str) -> None:
            if line.startswith("{"):
                try:
                    info = json.loads(line)
                    track = Track.from_info(info, self.source, len(self.tracks) + 1)
                    if track.key not in seen:
                        seen.add(track.key)
                        self.tracks.append(track)
                        self.update(track)
                except (ValueError, TypeError, AttributeError) as error:
                    self.emit("log", text=f"Playlist entry could not be read: {error}")
            elif line:
                self.emit("log", text=line)

        code, errors = self.run_process(base + ["--flat-playlist", "--lazy-playlist", "--dump-json",
                                                "--skip-download", "--", url], line_received)
        if code != 0:
            message = errors[-1] if errors else f"yt-dlp could not read the playlist (exit {code})."
            if not self.tracks:
                raise RuntimeError(message)
            self.emit("log", text=f"Playlist was only partly read: {message}")
            self.emit("warning", text="Some playlist entries could not be read. See Activity; run again to check the rest.")
        if not self.tracks:
            raise RuntimeError("No songs were found. Check the playlist URL and signed-in account in Settings.")
        return code == 0

    def download_one(self, track: Track, base: list[str], destination: Path, history: History) -> None:
        if self.cancel.is_set():
            raise Cancelled()
        existing = history.existing(track.key)
        if existing:
            self.update(track, status="Downloaded", progress="100%", detail="Already in this folder", path=str(existing))
            return
        self.update(track, status="Downloading", progress="0%", detail="Getting audio…", path="")
        final_path = ""

        def line_received(line: str) -> None:
            nonlocal final_path
            event = parse_event(line)
            if event:
                kind, payload = event
                if kind == "progress":
                    if payload.get("status") == "finished":
                        self.update(track, status="Processing", progress="100%", detail="Converting to MP3 and adding cover art")
                    else:
                        percentage, detail = progress_label(payload)
                        self.update(track, status="Downloading", progress=percentage, detail=detail)
                elif kind == "post":
                    self.update(track, status="Processing", progress="100%", detail="Converting to MP3 and adding cover art")
                elif kind == "title" and payload.get("title"):
                    self.update(track, title=payload["title"])
                elif kind == "done":
                    final_path = payload.get("filepath") or ""
            elif line:
                self.emit("log", text=f"{track.title}: {line}")

        command = base + ["--no-simulate", "--newline", "--progress", "--progress-delta", "0.4",
                          "--format", "bestaudio/best", "--extract-audio", "--audio-format", "mp3",
                          "--audio-quality", "0", "--embed-thumbnail", "--embed-metadata",
                          "--convert-thumbnails", "jpg", "--ffmpeg-location", self.settings.ffmpeg_dir,
                          "--windows-filenames", "--no-overwrites", "--no-post-overwrites",
                          "--paths", str(destination), "--output", "%(title).150B [%(id)s].%(ext)s",
                          "--print", 'before_dl:SHELF_TITLE %(.{title})j',
                          "--print", 'after_move:SHELF_DONE %(.{filepath})j',
                          "--progress-template", 'download:SHELF_PROGRESS %(progress.{status,downloaded_bytes,total_bytes,total_bytes_estimate,speed,eta})j',
                          "--progress-template", 'postprocess:SHELF_POST %(progress.{status,postprocessor})j',
                          "--", track.url]
        code, errors = self.run_process(command, line_received)
        path = Path(final_path) if final_path else None
        if code != 0 or not path or not path.is_file() or path.suffix.lower() != ".mp3":
            message = errors[-1] if errors else "MP3 conversion or metadata processing did not finish. See Activity."
            self.update(track, status="Error", detail=message)
            return
        self.update(track, status="Downloaded", progress="100%", detail="MP3 saved with tags and cover art", path=str(path))
        try:
            history.record(track.key, path)
        except OSError as error:
            self.emit("log", text=f"MP3 saved, but history could not be saved: {error}")
            self.emit("warning", text="A song was saved, but download history could not be updated.")

    def run(self) -> None:
        complete_scan = True
        try:
            url = validate_url(self.source, getattr(self.settings, f"{self.source}_url"))
            validate_tools(self.settings, self.source)
            destination = Path(self.settings.destination).expanduser().resolve()
            destination.mkdir(parents=True, exist_ok=True)
            history = History(destination)
            self.home.mkdir(parents=True, exist_ok=True)
            # An empty per-run folder exists only while the worker is active.
            with tempfile.TemporaryDirectory(prefix="session-", dir=self.home) as temporary:
                cookie_args = self.authenticate(url, Path(temporary) / "cookies.txt")
                base = self.base_command(cookie_args)
                if not self.retrying:
                    complete_scan = self.scan(url, base)
                self.emit("phase", text=f"Downloading {len(self.tracks)} songs…")
                for track in self.tracks:
                    if self.cancel.is_set():
                        raise Cancelled()
                    self.download_one(track, base, destination, history)
            self.emit("finished", cancelled=False, partial=not complete_scan)
        except Cancelled:
            for track in self.tracks:
                if track.status in {"Queued", "Downloading", "Processing"}:
                    self.update(track, status="Cancelled", detail="Stopped — run again to resume")
            self.emit("finished", cancelled=True, partial=not complete_scan)
        except Exception as error:
            for track in self.tracks:
                if track.status in {"Downloading", "Processing"}:
                    self.update(track, status="Error", detail=str(error))
                elif track.status == "Queued":
                    self.update(track, status="Cancelled", detail="Run stopped before this song")
            self.emit("fatal", text=str(error))
            self.emit("finished", cancelled=False, failed=True, partial=not complete_scan)
