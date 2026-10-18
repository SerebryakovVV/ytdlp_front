import tempfile
import threading
import unittest
from pathlib import Path

from shelf.downloader import Track
from shelf.ui import MusicShelf

TEST_ROOT = Path(__file__).resolve().parent.parent / ".test-data"
TEST_ROOT.mkdir(exist_ok=True)
tempfile.tempdir = str(TEST_ROOT)


class WindowTests(unittest.TestCase):
    def test_sign_in_dialog_continues_after_user_confirmation(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = MusicShelf(Path(temporary))
            try:
                confirmed, cancel = threading.Event(), threading.Event()
                app._show_login("youtube", confirmed, cancel)
                app.update()
                self.assertFalse(confirmed.is_set())
                buttons = app.login_window.winfo_children()[-1].winfo_children()
                button = next(button for button in buttons if button.cget("text") == "Continue")
                self.assertTrue(button.winfo_ismapped())
                self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), app.login_window.winfo_rooty() + app.login_window.winfo_height())
                button.invoke()
                self.assertTrue(confirmed.is_set())
                self.assertFalse(cancel.is_set())
                self.assertIsNone(app.login_window)
            finally:
                app.destroy()

    def test_window_controls_and_queue_updates(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = MusicShelf(Path(temporary))
            try:
                app.update()
                self.assertEqual(len(app.source_buttons), 2)
                self.assertLessEqual(app.stop_button.winfo_rooty() + app.stop_button.winfo_height(), app.winfo_rooty() + app.winfo_height())
                app.geometry("940x720")
                app.update()
                self.assertLessEqual(app.stop_button.winfo_rooty() + app.stop_button.winfo_height(), app.winfo_rooty() + app.winfo_height())
                track = Track("youtube:abc", "abc", "Тестовая песня", "https://youtube.com/watch?v=abc", "youtube", 1)
                app._track_update(track)
                self.assertEqual(app.tree.item(track.key)["values"][1], track.title)
                track.status = "Error"
                app._track_update(track)
                app._set_busy(False)
                self.assertEqual(str(app.retry_button.cget("state")), "normal")
                app._set_busy(True)
                self.assertEqual(str(app.browse_button.cget("state")), "disabled")
                self.assertEqual(str(app.stop_button.cget("state")), "normal")
                app._set_busy(False)
                app.show_settings()
                app.update()
                settings = next(child for child in app.winfo_children() if child.winfo_class() == "Toplevel")
                controls = []

                def visit(widget):
                    controls.append(widget)
                    for child in widget.winfo_children():
                        visit(child)

                visit(settings)
                save = next(control for control in controls if control.winfo_class() == "TButton" and control.cget("text") == "Save settings")
                self.assertLessEqual(save.winfo_rooty() + save.winfo_height(), settings.winfo_rooty() + settings.winfo_height())
                settings.destroy()
            finally:
                app.destroy()


if __name__ == "__main__":
    unittest.main()
