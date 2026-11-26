import unittest
from datetime import datetime
from types import SimpleNamespace

from bot import KYIV, resident_plan


class ResidentPlanTest(unittest.TestCase):
    now = datetime(2026, 11, 26, 6, 36, tzinfo=KYIV)

    def at(self, hour, minute=0):
        return self.now.replace(hour=hour, minute=minute)

    def plan(self, prior, ranges, window=(None, None), now=None):
        labels = {str(i): span for i, span in enumerate(ranges or [])}
        slots = {key: "no" for key in labels}
        if not labels:
            labels, slots = {"0": "00-24"}, {"0": "yes"}
        rows = {str(int(self.at(0).timestamp())): {"q": slots}}
        source = SimpleNamespace(fact=SimpleNamespace(days=rows),
                                 preset=SimpleNamespace(time_zone=labels))
        return resident_plan({"board:daily": prior}, source if ranges is not None else None,
                             "q", window, now or self.now, False)

    def test_address_changes_end_and_text_matches_example(self):
        ranges = ["05:30-09:00", "16-20"]
        old, _, _ = self.plan({}, ranges)
        window = self.at(6, 17), self.at(10, 12)
        current, text, changed = self.plan(old, ranges, window)
        self.assertTrue(changed)
        self.assertEqual(text, "26.11.2026\n06:17–10:12\n16:00–20:00")
        self.assertEqual(self.plan(current, ranges, window), (current, text, False))

    def test_past_start_or_finished_interval_is_silent(self):
        old, text, _ = self.plan({}, ["05-09", "16-20"])
        self.assertEqual(self.plan(old, ["06-09", "16-20"]), (old, text, False))
        self.assertEqual(self.plan(old, ["06-10", "16-20"], now=self.at(12)), (old, text, False))

    def test_new_current_outage_and_future_cancellation(self):
        old, _, _ = self.plan({}, ["16-20"])
        active, _, changed = self.plan(old, ["06-09", "16-20"])
        self.assertTrue(changed)
        new, text, changed = self.plan(active, ["06-09"])
        self.assertTrue(changed)
        self.assertEqual(text, "26.11.2026\n06:00–09:00")
        self.assertEqual(self.plan(new, None), (new, text, False))

    def test_missing_is_distinct_from_confirmed_empty(self):
        old, text, _ = self.plan({}, ["16-20"])
        self.assertEqual(self.plan(old, None), (old, text, False))
        self.assertTrue(self.plan(old, [])[2])
        self.assertFalse(self.plan({}, [])[2])
