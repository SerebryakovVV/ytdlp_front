from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

import websocket


class Cancelled(Exception):
    pass


class AuthenticationError(Exception):
    pass


class SignInRequired(AuthenticationError):
    pass


def service_cookies(cookies: list[dict], source: str) -> list[dict]:
    domains = ("youtube.com", "google.com") if source == "youtube" else ("soundcloud.com",)
    return [cookie for cookie in cookies if any(
        cookie.get("domain", "").lstrip(".").lower() == domain
        or cookie.get("domain", "").lstrip(".").lower().endswith("." + domain)
        for domain in domains)]


def is_signed_in(cookies: list[dict], source: str) -> bool:
    names = {cookie.get("name") for cookie in service_cookies(cookies, source)}
    if source == "youtube":
        # A visitor cookie does not imply authentication.
        return bool(names & {"SID", "__Secure-1PSID", "__Secure-3PSID", "LOGIN_INFO"})
    return bool(names & {"oauth_token", "access_token"})


def write_netscape(cookies: list[dict], path: Path, source: str) -> int:
    lines = ["# Netscape HTTP Cookie File", "# Temporary Music Shelf session; deleted after the run."]
    selected = service_cookies(cookies, source)
    for cookie in selected:
        domain = cookie["domain"]
        if cookie.get("httpOnly"):
            domain = "#HttpOnly_" + domain
        expires = int(cookie.get("expires", 0))
        # An empty expiry field is a Netscape session cookie. Zero would be expired.
        expiry = "" if expires <= 0 or cookie.get("session") else str(expires)
        values = [domain, "TRUE" if cookie["domain"].startswith(".") else "FALSE",
                  cookie.get("path", "/"), "TRUE" if cookie.get("secure") else "FALSE",
                  expiry, cookie["name"], cookie["value"]]
        if any("\t" in value or "\n" in value or "\r" in value for value in values):
            continue
        lines.append("\t".join(values))
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")
    return len(lines) - 2


class ChromeSession:
    """Use only the app's separate Chrome profile, never the normal Chrome profile."""

    def __init__(self, home: Path, executable: str):
        self.profile = (home / "chrome-profile").resolve()
        self.executable = executable
        self._lock = threading.RLock()
        self._opener = build_opener(ProxyHandler({}))
        self._login_process: subprocess.Popen | None = None

    def endpoint(self) -> str | None:
        try:
            lines = (self.profile / "DevToolsActivePort").read_text().splitlines()
            port = int(lines[0])
            if not 1 <= port <= 65535 or not lines[1].startswith("/devtools/browser/"):
                return None
            with self._opener.open(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
                data = json.load(response)
            expected = f"ws://127.0.0.1:{port}{lines[1]}"
            if data.get("webSocketDebuggerUrl") != expected:
                return None
            return expected
        except (OSError, ValueError, IndexError, KeyError):
            return None

    def command(self, method: str, params: dict | None = None) -> dict:
        with self._lock:
            endpoint = self.endpoint()
            if not endpoint:
                raise AuthenticationError("The app's Chrome window is closed. Click Connect to open it again.")
            connection = websocket.create_connection(endpoint, timeout=8, suppress_origin=True,
                                                       http_no_proxy=["127.0.0.1", "localhost"])
            try:
                connection.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
                while True:
                    message = json.loads(connection.recv())
                    if message.get("id") != 1:
                        continue
                    if "error" in message:
                        raise AuthenticationError("Chrome could not provide the session. Reconnect and try again.")
                    return message.get("result", {})
            finally:
                connection.close()

    def ensure_running(self, url: str, cancel: threading.Event, *, headless: bool = False) -> None:
        with self._lock:
            if self.endpoint():
                self.command("Target.createTarget", {"url": url})
                return
            if not self.executable or not Path(self.executable).is_file():
                raise AuthenticationError("Chrome was not found. Select chrome.exe in Settings.")
            self.profile.mkdir(parents=True, exist_ok=True)
            (self.profile / "DevToolsActivePort").unlink(missing_ok=True)
            args = [self.executable, f"--user-data-dir={self.profile}", "--remote-debugging-port=0",
                    "--remote-debugging-address=127.0.0.1", "--no-first-run",
                    "--no-default-browser-check", "--disable-background-mode", "--new-window", url]
            if headless:
                args.insert(1, "--headless=new")
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if cancel.is_set():
                    raise Cancelled()
                if self.endpoint():
                    return
                cancel.wait(0.2)
            raise AuthenticationError("Chrome could not read the saved sign-in. Close the Music Shelf Chrome window (your regular Chrome can stay open), then click Connect again.")

    def cookies(self) -> list[dict]:
        return self.command("Storage.getCookies").get("cookies", [])

    def open_login(self, url: str) -> None:
        """The human sign-in window must never have automation/debugging enabled."""
        with self._lock:
            self.close()
            if not self.executable or not Path(self.executable).is_file():
                raise AuthenticationError("Chrome was not found. Select chrome.exe in Settings.")
            self.profile.mkdir(parents=True, exist_ok=True)
            (self.profile / "DevToolsActivePort").unlink(missing_ok=True)
            args = [self.executable, f"--user-data-dir={self.profile}", "--no-first-run",
                    "--no-default-browser-check", "--disable-background-mode", "--new-window", url]
            self._login_process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)

    def cookie_snapshot(self, source: str, cancel: threading.Event) -> list[dict]:
        """Read a previously established session without visiting any sign-in page."""
        try:
            self.ensure_running("about:blank", cancel, headless=True)
            deadline = time.monotonic() + 4
            while True:
                if cancel.is_set():
                    raise Cancelled()
                cookies = self.cookies()
                if is_signed_in(cookies, source):
                    return service_cookies(cookies, source)
                if time.monotonic() >= deadline:
                    raise SignInRequired("The saved Chrome profile is not signed in. Click Connect to sign in normally.")
                cancel.wait(0.25)
        finally:
            self.close()

    def sign_in(self, source: str, url: str, cancel: threading.Event, request_confirmation,
                timeout: float = 600) -> list[dict]:
        self.open_login(url)
        confirmed = threading.Event()
        request_confirmation(source, confirmed, cancel)
        deadline = time.monotonic() + timeout
        while not confirmed.is_set():
            if cancel.wait(0.2):
                raise Cancelled()
            if time.monotonic() >= deadline:
                raise AuthenticationError("Sign-in timed out. Close the Music Shelf Chrome window and click Connect again.")
        if cancel.is_set():
            raise Cancelled()
        # Let a normal Chrome shutdown finish saving its cookies before reading them.
        deadline = time.monotonic() + 8
        while self._login_process and self._login_process.poll() is None:
            if cancel.wait(0.2):
                raise Cancelled()
            if time.monotonic() >= deadline:
                raise AuthenticationError("Close the Music Shelf Chrome window after signing in, then click Connect again. Your regular Chrome can stay open.")
        return self.cookie_snapshot(source, cancel)

    def close(self) -> None:
        if self.endpoint():
            try:
                self.command("Browser.close")
            except Exception:
                pass  # Browser.close may end the socket before responding.
            deadline = time.monotonic() + 3
            while self.endpoint() and time.monotonic() < deadline:
                time.sleep(0.1)


def clean_stale_sessions(home: Path) -> None:
    """Delete only this app's leftover cookie files after an interrupted run."""
    if not home.exists():
        return
    for directory in home.glob("session-*"):
        if directory.is_dir() and not directory.is_symlink():
            try:
                (directory / "cookies.txt").unlink(missing_ok=True)
                directory.rmdir()  # Refuse to remove a directory containing anything else.
            except OSError:
                pass
