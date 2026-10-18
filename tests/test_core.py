from __future__ import annotations

import http.cookiejar
import json
import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from shelf.auth import Cancelled, ChromeSession, SignInRequired, is_signed_in, service_cookies, write_netscape
from shelf.config import Settings, needs_youtube_login, validate_url
from shelf.downloader import DownloadWorker, History, Track, parse_event, progress_label

TEST_ROOT = Path(__file__).resolve().parent.parent / ".test-data"
TEST_ROOT.mkdir(exist_ok=True)
tempfile.tempdir = str(TEST_ROOT)


class CookieTests(unittest.TestCase):
    def test_only_service_domains_export_and_session_cookies_survive(self):
        cookies = [
            dict(domain=".youtube.com", name="SID", value="test-session", path="/", secure=True, httpOnly=True, expires=-1, session=True),
            dict(domain="accounts.google.com", name="account", value="test", path="/", secure=True, expires=2000000000),
            dict(domain=".example.com", name="secret", value="never-export", path="/", secure=True),
            dict(domain="notyoutube.com", name="SID", value="never-export", path="/", secure=True),
        ]
        self.assertEqual(len(service_cookies(cookies, "youtube")), 2)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cookies.txt"
            self.assertEqual(write_netscape(cookies, path, "youtube"), 2)
            jar = http.cookiejar.MozillaCookieJar(str(path))
            jar.load(ignore_discard=True, ignore_expires=True)
            self.assertEqual(len(jar), 2)
            session = next(cookie for cookie in jar if cookie.name == "SID")
            self.assertIsNone(session.expires)
            self.assertTrue(session.discard)
            self.assertNotIn("never-export", path.read_text())

    def test_visitor_is_not_a_login(self):
        self.assertFalse(is_signed_in([dict(domain=".youtube.com", name="VISITOR_INFO1_LIVE")], "youtube"))
        self.assertTrue(is_signed_in([dict(domain=".youtube.com", name="LOGIN_INFO")], "youtube"))
        self.assertFalse(is_signed_in([dict(domain=".example.com", name="SID")], "youtube"))


class ChromeSignInTests(unittest.TestCase):
    def test_human_login_has_no_debugging_or_automation_flags(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "chrome.exe"
            executable.touch()
            session = ChromeSession(Path(temporary), str(executable))
            session.close = Mock()
            with patch("shelf.auth.subprocess.Popen") as launch:
                session.open_login("https://www.youtube.com/playlist?list=LL")
            args = launch.call_args.args[0]
            self.assertIn("--new-window", args)
            self.assertTrue(any(arg.startswith("--user-data-dir=") for arg in args))
            self.assertFalse(any("debugging" in arg or "automation" in arg or "headless" in arg for arg in args))
            self.assertEqual(args[-1], "https://www.youtube.com/playlist?list=LL")

    def test_cookie_read_never_visits_sign_in_and_closes_browser(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = ChromeSession(Path(temporary), "chrome.exe")
            session.ensure_running = Mock()
            session.cookies = Mock(return_value=[dict(domain=".youtube.com", name="SID", value="test"),
                                                dict(domain=".unrelated.test", name="secret", value="other")])
            session.close = Mock()
            cancel = threading.Event()
            cookies = session.cookie_snapshot("youtube", cancel)
            session.ensure_running.assert_called_once_with("about:blank", cancel, headless=True)
            session.close.assert_called_once()
            self.assertEqual(len(cookies), 1)

    def test_missing_sign_in_reports_need_to_connect_and_closes_browser(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = ChromeSession(Path(temporary), "chrome.exe")
            session.ensure_running = Mock()
            session.cookies = Mock(return_value=[])
            session.close = Mock()
            with patch("shelf.auth.time.monotonic", side_effect=[0, 10]):
                with self.assertRaises(SignInRequired):
                    session.cookie_snapshot("youtube", threading.Event())
            session.close.assert_called_once()

    def test_sign_in_waits_for_confirmation_and_browser_shutdown(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = ChromeSession(Path(temporary), "chrome.exe")
            session.open_login = Mock()
            session._login_process = Mock()
            session._login_process.poll.return_value = 0
            session.cookie_snapshot = Mock(return_value=[{"name": "SID"}])
            cancel = threading.Event()

            def confirm(source, confirmed, passed_cancel):
                session.cookie_snapshot.assert_not_called()
                self.assertEqual(source, "youtube")
                self.assertIs(passed_cancel, cancel)
                confirmed.set()

            session.sign_in("youtube", "https://youtube.com/playlist?list=LL", cancel, confirm)
            session.cookie_snapshot.assert_called_once_with("youtube", cancel)

    def test_cancelled_sign_in_does_not_read_cookies(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = ChromeSession(Path(temporary), "chrome.exe")
            session.open_login = Mock()
            session.cookie_snapshot = Mock()
            cancel = threading.Event()
            with self.assertRaises(Cancelled):
                session.sign_in("youtube", "https://youtube.com/playlist?list=LL", cancel,
                                lambda source, confirmed, event: event.set())
            session.cookie_snapshot.assert_not_called()


class PlaylistTests(unittest.TestCase):
    def test_liked_videos_and_playlist_validation(self):
        self.assertTrue(needs_youtube_login(validate_url("youtube", "https://www.youtube.com/playlist?list=LL")))
        self.assertFalse(needs_youtube_login("https://www.youtube.com/playlist?list=PLmusic"))
        for url in ("https://youtube.com.evil.test/playlist?list=LL", "file:///playlist?list=LL", "https://youtube.com/watch?v=123"):
            with self.assertRaises(ValueError):
                validate_url("youtube", url)

    def test_flat_youtube_and_soundcloud_entries(self):
        track = Track.from_info({"id": "abc123", "title": "Песня", "url": "abc123"}, "youtube", 1)
        self.assertEqual(track.url, "https://www.youtube.com/watch?v=abc123")
        track = Track.from_info({"id": "42", "title": "A track", "url": "https://soundcloud.com/artist/song"}, "soundcloud", 2)
        self.assertEqual(track.key, "soundcloud:42")
        with self.assertRaises(ValueError):
            Track.from_info({"id": "42", "url": "https://evil.test/song"}, "soundcloud", 2)


class HistoryTests(unittest.TestCase):
    def test_only_existing_mp3s_skip_and_history_is_destination_specific(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "music"
            destination.mkdir()
            track = destination / "song [42].mp3"
            track.write_bytes(b"test")
            history = History(destination)
            history.record("soundcloud:42", track)
            self.assertEqual(History(destination).existing("soundcloud:42"), track)
            self.assertIsNone(History(Path(temporary) / "other").existing("soundcloud:42"))
            track.unlink()
            self.assertIsNone(history.existing("soundcloud:42"))
            history.data["bad"] = "../../outside.mp3"
            self.assertIsNone(history.existing("bad"))

    def test_corrupted_history_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".music-shelf/history.json"
            path.parent.mkdir()
            path.write_text("broken")
            with self.assertRaises(ValueError):
                History(Path(temporary))
            self.assertEqual(path.read_text(), "broken")


class ProgressTests(unittest.TestCase):
    def test_machine_events_and_estimated_progress(self):
        event = parse_event('SHELF_PROGRESS {"downloaded_bytes":512,"total_bytes_estimate":1024,"eta":65,"speed":1024}')
        self.assertEqual(event[0], "progress")
        percent, detail = progress_label(event[1])
        self.assertEqual(percent, "50%")
        self.assertIn("1:05 left", detail)
        self.assertEqual(progress_label({"total_bytes": "NA"})[0], "…")
        self.assertIsNone(parse_event("SHELF_DONE broken"))
        self.assertIsNone(parse_event('SHELF_DONE "wrong shape"'))


class WorkerTests(unittest.TestCase):
    def make_worker(self, destination, **kwargs):
        settings = Settings.defaults()
        settings.destination = str(destination)
        return DownloadWorker(settings, "soundcloud", destination.parent / "app", None, queue.Queue(), **kwargs)

    def test_conversion_error_never_enters_history_even_if_mp3_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "music"
            destination.mkdir()
            path = destination / "song [42].mp3"
            path.write_bytes(b"unfinished")
            worker = self.make_worker(destination)
            track = Track("soundcloud:42", "42", "Song", "https://soundcloud.com/artist/song", "soundcloud", 1)
            history = History(destination)

            def fake_process(args, callback):
                callback('SHELF_PROGRESS {"status":"finished"}')
                callback("SHELF_DONE " + json.dumps({"filepath": str(path)}))
                return 1, ["Embedding thumbnail failed"]

            worker.run_process = fake_process
            worker.download_one(track, [], destination, history)
            self.assertEqual(track.status, "Error")
            self.assertIsNone(history.existing(track.key))

    def test_success_records_mp3_and_second_run_skips_subprocess(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "music"
            destination.mkdir()
            path = destination / "song [42].mp3"
            path.write_bytes(b"complete")
            worker = self.make_worker(destination)
            track = Track("soundcloud:42", "42", "Song", "https://soundcloud.com/artist/song", "soundcloud", 1)
            history = History(destination)

            def fake_process(args, callback):
                self.assertIn("--no-simulate", args)
                self.assertNotIn("--ignore-errors", args)
                callback('SHELF_POST {"status":"finished"}')
                callback("SHELF_DONE " + json.dumps({"filepath": str(path)}))
                return 0, []

            worker.run_process = fake_process
            worker.download_one(track, [], destination, history)
            self.assertEqual(track.status, "Downloaded")
            worker.run_process = lambda *args: self.fail("already saved track must not download")
            worker.download_one(track, [], destination, history)
            self.assertEqual(track.detail, "Already in this folder")

    def test_playlist_partial_failure_keeps_discovered_songs_and_warns(self):
        with tempfile.TemporaryDirectory() as temporary:
            worker = self.make_worker(Path(temporary) / "music")

            def fake_process(args, callback):
                callback(json.dumps({"id": "1", "url": "https://soundcloud.com/a/b", "title": "Song"}))
                callback(json.dumps({"id": "1", "url": "https://soundcloud.com/a/b", "title": "Duplicate"}))
                return 1, ["Page 2 unavailable"]

            worker.run_process = fake_process
            self.assertFalse(worker.scan("https://soundcloud.com/a/likes", []))
            self.assertEqual(len(worker.tracks), 1)
            events = list(worker.events.queue)
            self.assertTrue(any(kind == "warning" for kind, _ in events))

    def test_cancel_terminates_process_and_marks_remaining_tracks(self):
        with tempfile.TemporaryDirectory() as temporary:
            worker = self.make_worker(Path(temporary) / "music")
            outcome = []

            def run():
                try:
                    worker.run_process([sys.executable, "-u", "-c", "import time; print('ready'); time.sleep(60)"], lambda line: outcome.append(line))
                except Cancelled:
                    outcome.append("cancelled")

            thread = threading.Thread(target=run)
            thread.start()
            deadline = time.monotonic() + 5
            while "ready" not in outcome and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertIn("ready", outcome)
            worker.stop()
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
            self.assertIn("cancelled", outcome)
            self.assertIsNone(worker._process)

    def test_cookie_temporary_directory_is_removed_on_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            worker = self.make_worker(Path(temporary) / "music")
            paths = []

            def fail_auth(url, cookie_path):
                paths.append(cookie_path)
                cookie_path.write_text("private session")
                raise RuntimeError("Expected failure")

            worker.authenticate = fail_auth
            with patch("shelf.downloader.validate_tools"):
                worker.run()
            self.assertEqual(len(paths), 1)
            self.assertFalse(paths[0].exists())
            self.assertTrue(any(kind == "fatal" for kind, _ in worker.events.queue))

    def test_new_account_uses_normal_sign_in_and_exports_cookie_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            worker = self.make_worker(Path(temporary) / "music")
            worker.source = "youtube"
            worker.session = Mock()
            worker.session.cookie_snapshot.side_effect = SignInRequired()
            worker.session.sign_in.return_value = [dict(domain=".youtube.com", name="SID", value="test",
                                                       path="/", secure=True, session=True, expires=-1)]
            path = Path(temporary) / "cookies.txt"
            args = worker.authenticate(worker.settings.youtube_url, path)
            self.assertEqual(args, ["--cookies", str(path)])
            self.assertIn("test", path.read_text())
            worker.session.sign_in.assert_called_once()

    def test_remembered_account_needs_no_sign_in_prompt(self):
        with tempfile.TemporaryDirectory() as temporary:
            worker = self.make_worker(Path(temporary) / "music")
            worker.source = "youtube"
            worker.session = Mock()
            worker.session.cookie_snapshot.return_value = [dict(domain=".youtube.com", name="SID", value="test",
                                                                path="/", secure=True, session=True, expires=-1)]
            worker.authenticate(worker.settings.youtube_url, Path(temporary) / "cookies.txt")
            worker.session.sign_in.assert_not_called()


class SettingsTests(unittest.TestCase):
    def test_settings_roundtrip_and_defaults_for_new_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            settings = Settings.defaults()
            settings.destination = "C:/Музыка"
            settings.save(home)
            self.assertEqual(Settings.load(home), settings)
            (home / "settings.json").write_text('{"youtube_url":"https://www.youtube.com/playlist?list=PL123"}', encoding="utf-8")
            self.assertEqual(Settings.load(home).youtube_auth, "Automatic Chrome")


if __name__ == "__main__":
    unittest.main()
