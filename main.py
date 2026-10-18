from __future__ import annotations

import ctypes
import os
import sys


def main() -> None:
    instance_handle = None
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        instance_handle = kernel.CreateMutexW(None, False, "Local\\MusicShelf.Desktop.Instance")
        if instance_handle and ctypes.get_last_error() == 183:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo("Music Shelf", "Music Shelf is already open. Switch to its existing window.", parent=root)
            root.destroy()
            kernel.CloseHandle(instance_handle)
            return
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MusicShelf.Desktop.1")
        except (AttributeError, OSError):
            pass
    from shelf.auth import clean_stale_sessions
    from shelf.config import app_home
    from shelf.ui import MusicShelf
    try:
        home = app_home()
        clean_stale_sessions(home)
        app = MusicShelf(home)
        if "--smoke-test" in sys.argv:
            app.after(700, app.close_app)
        app.mainloop()
    finally:
        if instance_handle:
            kernel.CloseHandle(instance_handle)


if __name__ == "__main__":
    main()
