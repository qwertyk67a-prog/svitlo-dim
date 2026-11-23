"""Fast hardware-free regression tests for game subtitle capture UI."""
import queue
import threading
import unittest

from app import Application, normalize_roi, text_similar


class GeometryTests(unittest.TestCase):
    def test_normal_selection(self):
        self.assertEqual(normalize_roi((100, 200, 500, 300), 1000, 500),
                         [0.1, 0.4, 0.5, 0.6])

    def test_reverse_selection_and_clamping(self):
        self.assertEqual(normalize_roi((1200, 600, -100, -200), 1000, 500),
                         [0.0, 0.0, 1.0, 1.0])

    def test_invalid_selection(self):
        with self.assertRaises(ValueError):
            normalize_roi((12, 13, 12, 13), 100, 100)
        with self.assertRaises(ValueError):
            normalize_roi((0, 0, 20, 20), 0, 100)

    def test_duplicate_text(self):
        self.assertTrue(text_similar("Hello, World!", "hello world"))
        self.assertFalse(text_similar("hello", "goodbye"))


class QueueTests(unittest.TestCase):
    def test_latest_frame_replaces_previous(self):
        a = Application.__new__(Application)
        a.preview_lock = threading.Lock()
        a.latest_preview = None
        for i in range(3000):
            a._put_latest_preview(i, 1)
        self.assertEqual(a.latest_preview, (1, 2999))

    def test_ocr_queue_never_grows(self):
        a = Application.__new__(Application)
        q = queue.Queue(maxsize=1)
        for i in range(3000):
            a._submit_latest(q, i)
        self.assertEqual(q.qsize(), 1)
        self.assertEqual(q.get_nowait(), 2999)


if __name__ == "__main__":
    unittest.main()
