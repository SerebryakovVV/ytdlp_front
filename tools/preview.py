"""Capture only this app's own window for layout review (Windows)."""
import ctypes
import struct
import sys
import zlib
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from shelf.ui import MusicShelf


def capture_window(app, path):
    user = ctypes.WinDLL("user32")
    gdi = ctypes.WinDLL("gdi32")
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetWindowDC.argtypes = [wintypes.HWND]
    user.GetWindowDC.restype = wintypes.HDC
    user.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi.CreateCompatibleDC.restype = wintypes.HDC
    gdi.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi.SelectObject.restype = wintypes.HGDIOBJ
    gdi.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi.DeleteDC.argtypes = [wintypes.HDC]
    hwnd = user.GetAncestor(app.winfo_id(), 2)
    rect = wintypes.RECT()
    user.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    source = user.GetWindowDC(hwnd)
    target = gdi.CreateCompatibleDC(source)
    bitmap = gdi.CreateCompatibleBitmap(source, width, height)
    original = gdi.SelectObject(target, bitmap)
    try:
        if not user.PrintWindow(hwnd, target, 2):
            raise RuntimeError("Could not capture app window")
        gdi.SelectObject(target, original)
        header = ctypes.create_string_buffer(struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0) + bytes(16))
        buffer = ctypes.create_string_buffer(width * height * 4)
        if not gdi.GetDIBits(target, bitmap, 0, height, buffer, header, 0):
            raise RuntimeError("Could not read app image")
        raw = buffer.raw
        scanlines = bytearray()
        for y in range(height):
            scanlines.append(0)
            for x in range(width):
                offset = (y * width + x) * 4
                scanlines.extend((raw[offset + 2], raw[offset + 1], raw[offset]))

        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b"")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
    finally:
        gdi.SelectObject(target, original)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(target)
        user.ReleaseDC(hwnd, source)


if __name__ == "__main__":
    app = MusicShelf(ROOT / ".test-data" / "preview-app")
    app.geometry("1120x800+40+40")

    def save():
        try:
            capture_window(app, ROOT / ".test-data" / "preview.png")
            print("Preview saved.")
        finally:
            app.close_app()

    app.after(800, save)
    app.mainloop()
