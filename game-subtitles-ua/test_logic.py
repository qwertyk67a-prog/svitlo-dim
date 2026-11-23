"""Fast hardware-free regression tests for game subtitle capture UI."""
import queue
import threading
import unittest

from app import (Application, normalize_roi, text_similar, crop_subtitle_area,
                 is_nearly_black, parsed_ocr_words, subtitle_is_ready, is_fifine_card)
import numpy as np


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


class PS5LiveSubtitleTests(unittest.TestCase):
    def test_crop_1080p_live_frame(self):
        frame = np.full((1080, 1920, 3), 90, dtype=np.uint8)
        crop = crop_subtitle_area(frame, (0.1, 0.72, 0.9, 0.96))
        self.assertEqual(crop.shape, (259, 1536, 3))
        self.assertIsNone(crop_subtitle_area(frame, (0.5, 0.7, 0.5, 0.9)))

    def test_hdmi_black_frame_detection(self):
        self.assertTrue(is_nearly_black(np.zeros((720, 1280, 3), dtype=np.uint8)))
        self.assertFalse(is_nearly_black(
            np.full((720, 1280, 3), 110, dtype=np.uint8)))

    def test_ocr_word_confidence(self):
        text, confidence = parsed_ocr_words({
            "text": ["", "Hello", "hero", "noise"],
            "conf": ["-1", "92.5", "88.5", "3.2"],
        })
        self.assertEqual(text, "Hello hero")
        self.assertEqual(confidence, 90.5)

    def test_live_dialogue_low_latency(self):
        self.assertTrue(subtitle_is_ready("Come back!", 90, 1, True))
        self.assertFalse(subtitle_is_ready("Come back!", 90, 1, False))
        self.assertTrue(subtitle_is_ready("Come back!", 54, 2, True))
        self.assertFalse(subtitle_is_ready("...", 21, 1, True))


class FifineDeviceTests(unittest.TestCase):
    def test_device_names(self):
        self.assertTrue(is_fifine_card("Fifine Video Capture (#2)"))
        self.assertTrue(is_fifine_card("FIFINE AMPLIGAME V3"))
        self.assertTrue(is_fifine_card("AmpliGame V3 Capture Card"))
        self.assertFalse(is_fifine_card("Integrated Laptop Camera"))
        self.assertFalse(is_fifine_card("OBS Virtual Camera"))


if __name__ == "__main__":
    unittest.main()
