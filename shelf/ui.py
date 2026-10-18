from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import webbrowser
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .auth import Cancelled, ChromeSession
from .config import Settings, YOUTUBE_URL, app_home, validate_url
from .downloader import DownloadWorker, Track


BG = "#f3f5fa"
WHITE = "#ffffff"
INK = "#18233b"
MUTED = "#66748b"
BLUE = "#365bdd"
BORDER = "#e1e6ef"


class MusicShelf(tk.Tk):
    def __init__(self, home: Path | None = None):
        super().__init__()
        self.home = home or app_home()
        settings_error = ""
        try:
            self.settings = Settings.load(self.home)
        except (OSError, ValueError) as error:
            self.settings = Settings.defaults()
            settings_error = f"Settings could not be read; using defaults. {error}"
        self.session = ChromeSession(self.home, self.settings.chrome_path)
        self.events: queue.Queue = queue.Queue()
        self.worker: DownloadWorker | None = None
        self.thread: threading.Thread | None = None
        self.connect_cancel: threading.Event | None = None
        self.tracks: dict[str, Track] = {}
        self.activity: list[str] = []
        self.activity_window: tk.Toplevel | None = None
        self.activity_text: tk.Text | None = None
        self.login_window: tk.Toplevel | None = None
        self.busy = False
        self.closing = False
        self.last_source = ""
        self.warning = ""
        self.connected = {"youtube": False, "soundcloud": False}
        self.title("Music Shelf")
        self.geometry(f"1120x{min(860, self.winfo_screenheight() - 100)}")
        self.minsize(940, 720)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self.close_app)
        icon = Path(__file__).resolve().parent.parent / "assets" / "music-shelf.ico"
        if icon.exists():
            self.iconbitmap(str(icon))
        self._styles()
        self._build()
        self._refresh_sources()
        self._poll_after_id = self.after(80, self._poll)
        if settings_error:
            self.after(150, lambda: self._error(settings_error))

    def _styles(self) -> None:
        self.option_add("*Font", "{Segoe UI} 10")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=WHITE)
        style.configure("TLabel", background=BG, foreground=INK, font=("Segoe UI", 10))
        style.configure("TEntry", padding=9, fieldbackground=WHITE, foreground=INK,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        style.configure("TCombobox", padding=7, fieldbackground=WHITE, foreground=INK)
        style.configure("TCheckbutton", background=BG, foreground=INK)
        style.configure("TButton", background=WHITE, foreground=INK, padding=(16, 9),
                        borderwidth=1, bordercolor=BORDER, font=("Segoe UI", 10))
        style.map("TButton", background=[("active", "#edf1fa"), ("disabled", "#e9edf5")],
                  foreground=[("disabled", "#94a0b4")])
        for name, color, active in (("Blue", BLUE, "#294ac1"), ("YouTube", "#dc3a48", "#bf2a36"),
                                    ("SoundCloud", "#e36a2f", "#c9541d")):
            style.configure(f"{name}.TButton", background=color, foreground=WHITE,
                            bordercolor=color, font=("Segoe UI", 10, "bold"), padding=(20, 11))
            style.map(f"{name}.TButton", background=[("disabled", "#dce2ef"), ("active", active)],
                      foreground=[("disabled", "#8795ad"), ("!disabled", WHITE)])
        style.configure("Treeview", background=WHITE, fieldbackground=WHITE, foreground=INK,
                        rowheight=42, borderwidth=0, font=("Segoe UI", 10))
        style.map("Treeview", background=[("selected", "#e9eeff")], foreground=[("selected", INK)])
        style.configure("Treeview.Heading", background="#f7f9fc", foreground=MUTED,
                        font=("Segoe UI", 9, "bold"), padding=(10, 12), relief="flat")
        style.map("Treeview.Heading", background=[("active", "#f0f3f9")])
        style.configure("Shelf.Horizontal.TProgressbar", background=BLUE, troughcolor="#e6ebf5",
                        borderwidth=0, lightcolor=BLUE, darkcolor=BLUE, thickness=5)

    @staticmethod
    def label(parent, text="", *, bg=WHITE, fg=INK, size=10, bold=False, **kwargs):
        return tk.Label(parent, text=text, bg=bg, fg=fg,
                        font=("Segoe UI", size, "bold" if bold else "normal"), **kwargs)

    def _build(self) -> None:
        header = tk.Frame(self, bg=INK, padx=30, pady=22)
        header.pack(fill="x")
        logo = tk.Canvas(header, width=48, height=48, bg=INK, highlightthickness=0)
        logo.pack(side="left", padx=(0, 14))
        logo.create_oval(2, 2, 46, 46, fill=BLUE, outline=BLUE)
        logo.create_line(27, 12, 27, 32, fill=WHITE, width=3)
        logo.create_line(27, 12, 35, 15, fill=WHITE, width=3)
        logo.create_oval(17, 27, 29, 36, fill=WHITE, outline=WHITE)
        titles = tk.Frame(header, bg=INK)
        titles.pack(side="left")
        self.label(titles, "Music Shelf", bg=INK, fg=WHITE, size=24, bold=True).pack(anchor="w")
        self.label(titles, "Your likes, saved locally.", bg=INK, fg="#afbcd3", size=10).pack(anchor="w", pady=(3, 0))
        self.settings_button = ttk.Button(header, text="Settings", command=self.show_settings)
        self.settings_button.pack(side="right")
        self.label(header, "MP3   /   BEST QUALITY   /   COVER ART", bg=INK, fg="#afbcd3", size=9).pack(side="right", padx=24)

        body = tk.Frame(self, bg=BG, padx=28, pady=22)
        body.pack(fill="both", expand=True)
        self.label(body, "YOUR COLLECTIONS", bg=BG, fg=MUTED, size=9, bold=True).pack(anchor="w", pady=(0, 11))
        sources = tk.Frame(body, bg=BG)
        sources.pack(fill="x")
        sources.columnconfigure(0, weight=1, uniform="cards")
        sources.columnconfigure(1, weight=1, uniform="cards")
        self.source_buttons = {}
        self.connect_buttons = {}
        self.source_descriptions = {}
        self.source_auth = {}
        for column, source, title, color, badge in (
                (0, "youtube", "YouTube", "#dc3a48", "▶"),
                (1, "soundcloud", "SoundCloud", "#e36a2f", "≋")):
            card = tk.Frame(sources, bg=WHITE, highlightbackground=BORDER, highlightthickness=1, padx=20, pady=17)
            card.grid(row=0, column=column, sticky="nsew", padx=(0, 10) if column == 0 else (10, 0))
            top = tk.Frame(card, bg=WHITE)
            top.pack(fill="x")
            self.label(top, badge, fg=color, size=22, bold=True).pack(side="left", padx=(0, 10))
            self.label(top, title, size=16, bold=True).pack(side="left")
            auth_label = self.label(top, "", fg=MUTED, size=9)
            auth_label.pack(side="right")
            self.source_auth[source] = auth_label
            description = self.label(card, "", fg=MUTED, size=10, anchor="w")
            description.pack(fill="x", pady=(6, 16))
            self.source_descriptions[source] = description
            actions = tk.Frame(card, bg=WHITE)
            actions.pack(fill="x")
            button = ttk.Button(actions, text=f"Download from {title}", style=f"{title}.TButton",
                                command=lambda service=source: self.start_download(service))
            button.pack(side="left")
            self.source_buttons[source] = button
            connect = ttk.Button(actions, text="Connect", command=lambda service=source: self.connect(service))
            self.connect_buttons[source] = connect
            if source == "youtube":
                connect.pack(side="right")

        destination = tk.Frame(body, bg=BG)
        destination.pack(fill="x", pady=(20, 22))
        self.label(destination, "SAVE TO", bg=BG, fg=MUTED, size=9, bold=True).pack(anchor="w", pady=(0, 7))
        row = tk.Frame(destination, bg=BG)
        row.pack(fill="x")
        self.destination = tk.StringVar(value=self.settings.destination)
        self.destination_entry = ttk.Entry(row, textvariable=self.destination)
        self.destination_entry.pack(side="left", fill="x", expand=True)
        self.browse_button = ttk.Button(row, text="Choose folder…", command=self.choose_folder)
        self.browse_button.pack(side="left", padx=(10, 8))
        ttk.Button(row, text="Open folder", command=self.open_destination).pack(side="left")

        queue_header = tk.Frame(body, bg=BG)
        queue_header.pack(fill="x", pady=(0, 10))
        self.label(queue_header, "Download queue", bg=BG, size=17, bold=True).pack(side="left")
        self.summary = tk.StringVar(value="Choose a collection to get started")
        self.label(queue_header, textvariable=self.summary, bg=BG, fg=MUTED, size=10).pack(side="right")

        table = tk.Frame(body, bg=WHITE, highlightbackground=BORDER, highlightthickness=1)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=("number", "song", "status", "progress", "detail"), show="headings", selectmode="browse")
        for name, title, width, anchor, stretch in (
                ("number", "#", 42, "center", False), ("song", "SONG", 370, "w", True),
                ("status", "STATUS", 120, "w", False), ("progress", "PROGRESS", 85, "center", False),
                ("detail", "DETAILS", 270, "w", True)):
            self.tree.heading(name, text=title, anchor=anchor)
            self.tree.column(name, width=width, minwidth=width if not stretch else 130, anchor=anchor, stretch=stretch)
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(fill="both", expand=True)
        for status, color in (("Downloaded", "#228052"), ("Downloading", BLUE),
                              ("Processing", "#7e57bd"), ("Error", "#c33542"),
                              ("Cancelled", MUTED), ("Queued", INK)):
            self.tree.tag_configure(status, foreground=color)
        self.empty = tk.Frame(table, bg=WHITE)
        self.empty.place(relx=0.5, rely=0.57, anchor="center")
        self.empty_note = self.label(self.empty, "♪", fg="#afbddb", size=38)
        self.empty_note.pack()
        self.label(self.empty, "Bring your music home", size=13, bold=True).pack(pady=(4, 5))
        self.label(self.empty, "Your songs and their download status will appear here.", fg=MUTED).pack()
        def resize_empty(event):
            if event.height < 220:
                self.empty_note.pack_forget()
            elif not self.empty_note.winfo_manager():
                self.empty_note.pack(before=self.empty.winfo_children()[1])
        table.bind("<Configure>", resize_empty)
        self.tree.bind("<Double-1>", self.track_details)
        self.tree.bind("<Return>", self.track_details)

        self.progress = ttk.Progressbar(body, style="Shelf.Horizontal.TProgressbar", mode="determinate", maximum=100)
        footer = tk.Frame(body, bg=BG)
        # Reserve footer space before allocating the flexible queue, including at small sizes.
        footer.pack(side="bottom", fill="x", before=table)
        self.progress.pack(side="bottom", fill="x", pady=(14, 10), before=table)
        self.phase = tk.StringVar(value="Ready. Connect YouTube once, then download your likes.")
        self.phase_label = self.label(footer, textvariable=self.phase, bg=BG, fg=MUTED, size=10,
                                     anchor="w", justify="left", wraplength=670)
        self.phase_label.pack(side="left", fill="x", expand=True, padx=(0, 10))
        ttk.Button(footer, text="Activity", command=self.show_activity).pack(side="right")
        self.stop_button = ttk.Button(footer, text="Stop", command=self.stop_work, state="disabled")
        self.stop_button.pack(side="right", padx=8)
        self.retry_button = ttk.Button(footer, text="Retry errors", command=self.retry_errors, state="disabled")
        self.retry_button.pack(side="right")

    def _refresh_sources(self) -> None:
        youtube_description = "Liked videos · your signed-in YouTube account" if self.settings.youtube_url == YOUTUBE_URL else "Your selected YouTube playlist · change in Settings"
        self.source_descriptions["youtube"].configure(text=youtube_description)
        self.source_descriptions["soundcloud"].configure(text=self.settings.soundcloud_url.replace("https://", "").replace("http://", ""))
        mode = self.settings.youtube_auth
        self.source_auth["youtube"].configure(text="Connected" if self.connected["youtube"] else ("One-time sign-in" if mode == "Automatic Chrome" else mode), fg="#228052" if self.connected["youtube"] else MUTED)
        self.source_auth["soundcloud"].configure(text=("Connected" if self.connected["soundcloud"] else "Sign-in enabled") if self.settings.soundcloud_auth else "Public likes", fg=MUTED)
        if self.settings.soundcloud_auth:
            self.connect_buttons["soundcloud"].pack(side="right")
        else:
            self.connect_buttons["soundcloud"].pack_forget()
        self.connect_buttons["youtube"].configure(state="normal" if mode == "Automatic Chrome" and not self.busy else "disabled")

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for button in [*self.source_buttons.values(), *self.connect_buttons.values(), self.settings_button, self.browse_button]:
            button.configure(state=state)
        self.destination_entry.configure(state=state)
        self.stop_button.configure(state="normal" if busy else "disabled")
        self.retry_button.configure(state="normal" if not busy and any(t.status == "Error" for t in self.tracks.values()) else "disabled")
        self._refresh_sources()

    def choose_folder(self) -> None:
        destination = filedialog.askdirectory(parent=self, title="Choose where to save your music", initialdir=self.destination.get() if Path(self.destination.get()).is_dir() else str(Path.home()))
        if destination:
            self.destination.set(destination)
            self.settings.destination = destination
            self._save_settings()

    def _save_settings(self) -> bool:
        try:
            self.settings.save(self.home)
            return True
        except OSError as error:
            self._error(f"Settings could not be saved: {error}")
            return False

    def open_destination(self) -> None:
        path = Path(self.destination.get()).expanduser()
        if not path.is_dir():
            messagebox.showinfo("Save folder", "Choose a folder first. A new folder is created when you download.", parent=self)
            return
        self._open_path(path)

    def _open_path(self, path: Path) -> None:
        try:
            os.startfile(str(path))
        except OSError as error:
            self._error(str(error))

    def connect(self, source: str) -> None:
        if self.busy:
            return
        self.connect_cancel = threading.Event()
        self._set_busy(True)
        self.phase.set("Opening your normal Chrome sign-in window…")

        def run() -> None:
            try:
                self.session.sign_in(source, getattr(self.settings, f"{source}_url"), self.connect_cancel,
                                     lambda service, confirmed, cancel: self.events.put(("login_required", {
                                         "source": service, "confirmed": confirmed, "cancel": cancel})))
                self.events.put(("connected", {"source": source}))
                self.events.put(("phase", {"text": f"{source.title()} connected. You can download your collection now."}))
            except Cancelled:
                self.events.put(("phase", {"text": "Sign-in stopped. You can reconnect whenever you like."}))
            except Exception as error:
                self.events.put(("fatal", {"text": str(error)}))
            finally:
                self.session.close()
                self.events.put(("connection_finished", {}))

        self.thread = threading.Thread(target=run, name="connect", daemon=True)
        self.thread.start()

    def _show_login(self, source: str, confirmed: threading.Event, cancel: threading.Event) -> None:
        if self.closing or cancel.is_set():
            cancel.set()
            return
        title = "YouTube" if source == "youtube" else "SoundCloud"
        self.phase.set("Sign in in Chrome, close that window, then click Continue in Music Shelf.")
        window = tk.Toplevel(self)
        self.login_window = window
        window.title(f"Music Shelf · Connect {title}")
        window.geometry("600x340")
        window.configure(bg=BG)
        window.transient(self)
        self.label(window, f"Connect {title}", bg=BG, size=20, bold=True).pack(anchor="w", padx=26, pady=(22, 14))
        steps = (f"1. Sign in to {title} in the new Chrome window.\n\n"
                 "2. Close that Chrome window after you can see your account. Your usual Chrome windows can stay open.\n\n"
                 "3. Click Continue below to verify and save the connection.")
        self.label(window, steps, bg=BG, justify="left", wraplength=545, anchor="w").pack(fill="x", padx=26)
        self.label(window, "This sign-in window uses normal Chrome. Browser debugging is disabled during sign-in.",
                   bg=BG, fg=MUTED, size=9, justify="left", wraplength=545).pack(anchor="w", padx=26, pady=15)
        buttons = tk.Frame(window, bg=BG)
        buttons.pack(side="bottom", fill="x", padx=26, pady=18)

        def finish(accepted: bool) -> None:
            if accepted:
                confirmed.set()
                self.phase.set("Verifying the saved sign-in…")
            else:
                cancel.set()
            window.destroy()
            self.login_window = None

        window.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        ttk.Button(buttons, text="Continue", style="Blue.TButton", command=lambda: finish(True)).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=lambda: finish(False)).pack(side="right", padx=10)

    def start_download(self, source: str, retry_tracks: list[Track] | None = None) -> None:
        if self.busy:
            return
        if not self.destination.get().strip():
            self._error("Choose a destination folder before downloading.")
            return
        self.settings.destination = self.destination.get().strip()
        try:
            validate_url(source, getattr(self.settings, f"{source}_url"))
        except ValueError as error:
            self._error(str(error))
            return
        if not self._save_settings():
            return
        if retry_tracks is None:
            self.tracks.clear()
            self.tree.delete(*self.tree.get_children())
            self.empty.place(relx=0.5, rely=0.57, anchor="center")
        else:
            for track in retry_tracks:
                track.status = "Queued"
                track.progress = "—"
                track.detail = "Retrying"
                self._track_update(track)
        self.warning = ""
        self.last_source = source
        self.progress.configure(value=0)
        self._set_busy(True)
        self.phase.set("Preparing your download…")
        self._log(f"Starting {source.title()} {'retry' if retry_tracks is not None else 'collection'}.")
        self.worker = DownloadWorker(self.settings, source, self.home, self.session, self.events, retry_tracks=retry_tracks)
        self.thread = threading.Thread(target=self.worker.run, name="download", daemon=True)
        self.thread.start()

    def retry_errors(self) -> None:
        tracks = [replace(track) for track in self.tracks.values() if track.status == "Error"]
        if tracks:
            self.start_download(self.last_source, tracks)

    def stop_work(self) -> None:
        self.phase.set("Stopping… Partial audio is kept so the next run can resume.")
        self.stop_button.configure(state="disabled")
        if self.connect_cancel:
            self.connect_cancel.set()
        if self.worker:
            threading.Thread(target=self.worker.stop, daemon=True).start()
        if self.login_window is not None and self.login_window.winfo_exists():
            self.login_window.destroy()
            self.login_window = None

    def _track_update(self, track: Track) -> None:
        self.empty.place_forget()
        previous = self.tracks.get(track.key)
        self.tracks[track.key] = track
        values = (track.index, track.title, track.status, track.progress, track.detail)
        if self.tree.exists(track.key):
            self.tree.item(track.key, values=values, tags=(track.status,))
        else:
            self.tree.insert("", "end", iid=track.key, values=values, tags=(track.status,))
        if track.status == "Downloading" and (previous is None or previous.status != "Downloading"):
            self.tree.see(track.key)
        total = len(self.tracks)
        done = sum(t.status == "Downloaded" for t in self.tracks.values())
        errors = sum(t.status == "Error" for t in self.tracks.values())
        terminal = sum(t.status in {"Downloaded", "Error", "Cancelled"} for t in self.tracks.values())
        self.summary.set(f"{total} songs   ·   {done} downloaded   ·   {errors} errors")
        self.progress.configure(value=100 * terminal / total if total else 0)

    def _poll(self) -> None:
        for _ in range(250):
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "track":
                self._track_update(data["track"])
            elif kind == "phase":
                self.phase.set(data["text"])
            elif kind == "log":
                self._log(data["text"])
            elif kind == "warning":
                self.warning = data["text"]
                self._log(data["text"])
            elif kind == "connected":
                self.connected[data["source"]] = True
                self._refresh_sources()
            elif kind == "login_required":
                self._show_login(data["source"], data["confirmed"], data["cancel"])
            elif kind == "fatal":
                self.phase.set(data["text"])
                self._log("Error: " + data["text"])
                if not self.closing:
                    self._error(data["text"])
            elif kind in {"finished", "connection_finished"}:
                if self.login_window is not None and self.login_window.winfo_exists():
                    self.login_window.destroy()
                    self.login_window = None
                self.worker = None
                self.connect_cancel = None
                self._set_busy(False)
                if kind == "finished" and not data.get("failed"):
                    errors = sum(t.status == "Error" for t in self.tracks.values())
                    done = sum(t.status == "Downloaded" for t in self.tracks.values())
                    if data.get("cancelled"):
                        self.phase.set(f"Stopped. {done} songs saved. Run the collection again to resume.")
                    elif self.warning or data.get("partial"):
                        self.phase.set(self.warning or "Playlist was only partly read. See Activity and run again.")
                    elif errors:
                        self.phase.set(f"Finished with {errors} errors. Double-click a song for details, or retry errors.")
                    else:
                        self.phase.set(f"All done. {done} songs are in your save folder.")
                if self.closing:
                    self._destroy_when_idle()
                    return
        self._poll_after_id = self.after(80, self._poll)

    def _log(self, text: str) -> None:
        self.activity.append(text)
        self.activity = self.activity[-500:]
        if self.activity_text is not None and self.activity_text.winfo_exists():
            self.activity_text.configure(state="normal")
            self.activity_text.delete("1.0", "end")
            self.activity_text.insert("1.0", "\n".join(self.activity))
            self.activity_text.see("end")
            self.activity_text.configure(state="disabled")

    def _error(self, text: str) -> None:
        messagebox.showerror("Music Shelf", text, parent=self)

    def show_activity(self) -> None:
        if self.activity_window is not None and self.activity_window.winfo_exists():
            self.activity_window.lift()
            return
        window = tk.Toplevel(self)
        self.activity_window = window
        window.title("Music Shelf · Activity")
        window.geometry("850x480")
        window.configure(bg=BG)
        self.label(window, "Activity", bg=BG, size=17, bold=True).pack(anchor="w", padx=20, pady=15)
        frame = tk.Frame(window, bg=WHITE)
        frame.pack(fill="both", expand=True, padx=20, pady=(0, 15))
        text = tk.Text(frame, bg=WHITE, fg=INK, font=("Consolas", 10), wrap="word", relief="flat", padx=12, pady=12)
        scroll = ttk.Scrollbar(frame, command=text.yview)
        scroll.pack(side="right", fill="y")
        text.configure(yscrollcommand=scroll.set)
        text.pack(fill="both", expand=True)
        text.insert("1.0", "\n".join(self.activity) or "No downloads yet.")
        text.configure(state="disabled")
        self.activity_text = text

    def track_details(self, event=None) -> None:
        if event and getattr(event, "y", None) is not None and getattr(event, "type", None) == tk.EventType.ButtonPress:
            item = self.tree.identify_row(event.y)
        else:
            selected = self.tree.selection()
            item = selected[0] if selected else ""
        track = self.tracks.get(item)
        if not track:
            return
        window = tk.Toplevel(self)
        window.title("Music Shelf · Song details")
        window.geometry("640x270")
        window.configure(bg=BG)
        self.label(window, track.title, bg=BG, size=14, bold=True, wraplength=590, justify="left").pack(anchor="w", padx=24, pady=(24, 12))
        self.label(window, f"{track.status} · {track.progress}", bg=BG, fg=MUTED).pack(anchor="w", padx=24)
        text = tk.Text(window, height=4, bg=BG, fg=INK, relief="flat", wrap="word", font=("Segoe UI", 10))
        text.pack(fill="both", expand=True, padx=24, pady=12)
        text.insert("1.0", track.detail + ("\n\n" + track.path if track.path else ""))
        text.configure(state="disabled")
        buttons = tk.Frame(window, bg=BG)
        buttons.pack(fill="x", padx=24, pady=(0, 20))
        ttk.Button(buttons, text="Open on " + ("YouTube" if track.source == "youtube" else "SoundCloud"), command=lambda: webbrowser.open(track.url)).pack(side="left")
        if track.path and Path(track.path).is_file():
            ttk.Button(buttons, text="Play MP3", command=lambda: self._open_path(Path(track.path))).pack(side="left", padx=10)

    def show_settings(self) -> None:
        if self.busy:
            return
        window = tk.Toplevel(self)
        window.title("Music Shelf · Settings")
        window.geometry("850x720")
        window.resizable(False, False)
        window.configure(bg=BG)
        window.transient(self)
        window.grab_set()
        buttons = tk.Frame(window, bg=BG, padx=26, pady=16)
        buttons.pack(side="bottom", fill="x")
        content = tk.Frame(window, bg=BG)
        content.pack(fill="both", expand=True)
        canvas = tk.Canvas(content, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(content, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        frame = tk.Frame(canvas, bg=BG, padx=26, pady=22)
        frame_id = canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(frame_id, width=event.width))
        window.bind("<MouseWheel>", lambda event: canvas.yview_scroll(int(-event.delta / 120), "units"))
        frame.columnconfigure(1, weight=1)
        self.label(frame, "Settings", bg=BG, size=20, bold=True).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self.label(frame, "Your choices are remembered on this computer.", bg=BG, fg=MUTED).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 18))
        variables = {}
        specifications = [
            ("youtube_url", "YouTube playlist", "url"), ("soundcloud_url", "SoundCloud likes / playlist", "url"),
            ("youtube_auth", "YouTube sign-in", "mode"), ("cookie_file", "Cookies file (optional fallback)", "file"),
            ("ytdlp_path", "yt-dlp.exe", "file"), ("ffmpeg_dir", "ffmpeg + ffprobe folder", "folder"),
            ("node_path", "node.exe", "file"), ("chrome_path", "chrome.exe", "file")]
        for row, (key, title, kind) in enumerate(specifications, start=2):
            variables[key] = tk.StringVar(value=getattr(self.settings, key))
            self.label(frame, title, bg=BG, size=10).grid(row=row, column=0, sticky="w", pady=7, padx=(0, 15))
            if kind == "mode":
                entry = ttk.Combobox(frame, textvariable=variables[key], state="readonly", values=["Automatic Chrome", "Firefox", "Cookies file", "No login (public playlist)"])
            else:
                entry = ttk.Entry(frame, textvariable=variables[key])
            entry.grid(row=row, column=1, sticky="ew", pady=7)
            if kind in {"folder", "file"}:
                def browse(variable=variables[key], field_kind=kind, field_key=key):
                    if field_kind == "folder":
                        value = filedialog.askdirectory(parent=window)
                    else:
                        value = filedialog.askopenfilename(parent=window, filetypes=[("Cookies text", "*.txt"), ("All files", "*.*")] if field_key == "cookie_file" else [("Executables", "*.exe"), ("All files", "*.*")])
                    if value:
                        variable.set(value)
                ttk.Button(frame, text="Browse…", command=browse).grid(row=row, column=2, padx=(10, 0), pady=7)
        soundcloud_auth = tk.BooleanVar(value=self.settings.soundcloud_auth)
        ttk.Checkbutton(frame, text="Use the app's Chrome sign-in for SoundCloud (for private tracks)", variable=soundcloud_auth).grid(row=10, column=0, columnspan=3, sticky="w", pady=(15, 12))
        info = ("Automatic Chrome: sign in once in the normal Chrome window, close it, then click Continue in Music Shelf. Cookies refresh automatically for each run. "
                "Your usual Chrome session can stay open.\n\n"
                "Liked videos (list=LL) needs sign-in. You can paste a separate music playlist above instead. "
                "SoundCloud public likes works without sign-in.\n\n"
                "Downloads use MP3 quality 0, artwork, and metadata. Track IDs in filenames prevent songs with the same name from overwriting each other.")
        self.label(frame, info, bg=BG, fg=MUTED, wraplength=785, justify="left", anchor="w").grid(row=11, column=0, columnspan=3, sticky="ew", pady=(3, 18))
        def save() -> None:
            candidate = replace(self.settings, **{key: value.get().strip() for key, value in variables.items()}, soundcloud_auth=soundcloud_auth.get())
            try:
                validate_url("youtube", candidate.youtube_url)
                validate_url("soundcloud", candidate.soundcloud_url)
                candidate.save(self.home)
            except (ValueError, OSError) as error:
                messagebox.showerror("Settings", str(error), parent=window)
                return
            if candidate.chrome_path != self.settings.chrome_path:
                self.session.close()
                self.session = ChromeSession(self.home, candidate.chrome_path)
            self.settings = candidate
            self._refresh_sources()
            window.destroy()

        ttk.Button(buttons, text="Save settings", style="Blue.TButton", command=save).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=window.destroy).pack(side="right", padx=10)

    def close_app(self) -> None:
        if self.closing:
            return
        self.closing = True
        if self.busy:
            self.stop_work()
        else:
            self._destroy_when_idle()

    def _destroy_when_idle(self) -> None:
        if self.thread and self.thread.is_alive():
            self.after(100, self._destroy_when_idle)
            return
        # Closing this dedicated profile never closes the user's normal browser.
        self.session.close()
        self.destroy()

    def destroy(self) -> None:
        if hasattr(self, "_poll_after_id"):
            self.after_cancel(self._poll_after_id)
        super().destroy()
