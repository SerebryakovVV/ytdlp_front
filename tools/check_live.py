"""Opt-in real integration checks; private accounts are never used by these tests."""
from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shelf.auth import ChromeSession, write_netscape
from shelf.config import Settings
from shelf.downloader import DownloadWorker, History, Track


def check_chrome():
    home = ROOT / ".test-data" / "chrome-smoke"
    settings = Settings.defaults()
    session = ChromeSession(home, settings.chrome_path)
    try:
        session.ensure_running("about:blank", threading.Event(), headless=True)
        session.command("Storage.setCookies", {"cookies": [dict(name="music_shelf_test", value="example",
                                                                domain=".youtube.com", path="/", secure=True,
                                                                expires=time.time() + 600),
                                                           dict(name="SID", value="synthetic-test-not-a-real-login",
                                                                domain=".youtube.com", path="/", secure=True,
                                                                expires=time.time() + 600)]})
        cookies = session.cookies()
        assert any(cookie["name"] == "music_shelf_test" for cookie in cookies)
        home.mkdir(parents=True, exist_ok=True)
        path = home / "test-cookies.txt"
        try:
            assert write_netscape(cookies, path, "youtube") >= 1
        finally:
            path.unlink(missing_ok=True)
        print("Chrome: dedicated profile, local connection, and cookie export passed.")
    finally:
        session.close()
    assert session.endpoint() is None
    print("Chrome: dedicated browser closed successfully.")
    cookies = session.cookie_snapshot("youtube", threading.Event())
    assert any(cookie["name"] == "music_shelf_test" for cookie in cookies)
    assert session.endpoint() is None
    print("Chrome: saved test cookies survived restart and were collected at about:blank.")


def check_soundcloud():
    settings = Settings.defaults()
    destination = ROOT / ".test-data" / "live-downloads"
    settings.destination = str(destination)
    events = queue.Queue()
    track = Track("soundcloud:1881899190", "1881899190", "Oddworld X Makko - Nein Sagen",
                  "https://soundcloud.com/lenny-480969496/oddworld-x-makko-nein-sagen", "soundcloud", 1)
    worker = DownloadWorker(settings, "soundcloud", ROOT / ".test-data" / "live-app", None, events, retry_tracks=[track])
    worker.run()
    while not events.empty():
        kind, data = events.get_nowait()
        if kind == "fatal":
            raise AssertionError(data["text"])
        if kind == "log" and "ERROR:" in data["text"]:
            print(data["text"])
    assert track.status == "Downloaded", track.detail
    metadata = json.loads(subprocess.check_output([str(Path(settings.ffmpeg_dir) / "ffprobe.exe"), "-v", "error",
                                                   "-show_streams", "-show_format", "-of", "json", track.path], encoding="utf-8"))
    assert any(stream["codec_name"] == "mp3" for stream in metadata["streams"])
    assert any(stream.get("disposition", {}).get("attached_pic") for stream in metadata["streams"])
    assert metadata["format"].get("tags", {}).get("title")
    print("SoundCloud: final MP3, embedded artwork, title metadata, and history passed.")
    worker.download_one(track, [], destination, History(destination))
    assert track.detail == "Already in this folder"
    print("SoundCloud: repeat download skipped successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["chrome", "soundcloud"])
    args = parser.parse_args()
    check_chrome() if args.mode == "chrome" else check_soundcloud()
