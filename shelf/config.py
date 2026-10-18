from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from urllib.parse import parse_qs, urlparse


YOUTUBE_URL = "https://www.youtube.com/playlist?list=LL"
SOUNDCLOUD_URL = "https://soundcloud.com/valentin-serebryakov/likes"
KNOWN_TOOLS = Path(r"C:\Users\sereb\main_files\coding\scripts")


def app_home() -> Path:
    override = os.environ.get("MUSIC_SHELF_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MusicShelf"


def find_tool(name: str) -> str:
    known = KNOWN_TOOLS / name
    return str(known) if known.is_file() else (shutil.which(name) or "")


def find_chrome() -> str:
    for root in (os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                 os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                 os.environ.get("LOCALAPPDATA", "")):
        candidate = Path(root) / "Google/Chrome/Application/chrome.exe"
        if candidate.is_file():
            return str(candidate)
    return ""


@dataclass
class Settings:
    destination: str = ""
    youtube_url: str = YOUTUBE_URL
    soundcloud_url: str = SOUNDCLOUD_URL
    ytdlp_path: str = ""
    ffmpeg_dir: str = ""
    node_path: str = ""
    chrome_path: str = ""
    youtube_auth: str = "Automatic Chrome"
    cookie_file: str = ""
    soundcloud_auth: bool = False

    @classmethod
    def defaults(cls) -> Settings:
        ffmpeg = find_tool("ffmpeg.exe")
        return cls(destination=str(Path.home() / "Music" / "Music Shelf"),
                   ytdlp_path=find_tool("yt-dlp.exe"),
                   ffmpeg_dir=str(Path(ffmpeg).parent) if ffmpeg else "",
                   node_path=shutil.which("node.exe") or "",
                   chrome_path=find_chrome())

    @classmethod
    def load(cls, home: Path) -> Settings:
        settings = cls.defaults()
        path = home / "settings.json"
        if not path.exists():
            return settings
        # A damaged file is kept for inspection; the UI reports it instead of overwriting it.
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Settings file must contain a JSON object.")
        for field in fields(cls):
            value = data.get(field.name)
            if value is not None and isinstance(value, type(getattr(settings, field.name))):
                setattr(settings, field.name, value)
        return settings

    def save(self, home: Path) -> None:
        home.mkdir(parents=True, exist_ok=True)
        temporary = home / "settings.tmp"
        temporary.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(home / "settings.json")


def validate_url(source: str, url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("Enter a full https:// playlist URL.")
    if source == "youtube":
        if host not in {"youtube.com", "www.youtube.com", "music.youtube.com"}:
            raise ValueError("Enter a YouTube playlist URL.")
        if parsed.path.rstrip("/") != "/playlist" or not parse_qs(parsed.query).get("list"):
            raise ValueError("Enter a YouTube playlist URL, including ?list=... .")
    elif source == "soundcloud":
        if host not in {"soundcloud.com", "www.soundcloud.com"} or not parsed.path.strip("/"):
            raise ValueError("Enter a SoundCloud likes or playlist URL.")
    else:
        raise ValueError("Unknown music service.")
    return url.strip()


def needs_youtube_login(url: str) -> bool:
    return parse_qs(urlparse(url).query).get("list", [""])[0] in {"LL", "WL"}


def validate_tools(settings: Settings, source: str) -> None:
    if not Path(settings.ytdlp_path).is_file():
        raise ValueError("yt-dlp.exe was not found. Set its path in Settings.")
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        if not (Path(settings.ffmpeg_dir) / name).is_file():
            raise ValueError(f"{name} was not found. Select its folder in Settings.")
    if source == "youtube" and not Path(settings.node_path).is_file():
        raise ValueError("Node.js was not found. Set node.exe in Settings.")
