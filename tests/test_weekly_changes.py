from datetime import datetime, timedelta
from unittest import TestCase

from bot import KYIV, fingerprint, weekly_notice


class WeeklyChangeTests(TestCase):
    now = datetime(2026, 11, 25, 12, 0, tzinfo=KYIV)
    today = now.date()
    yesterday = today - timedelta(days=1)
    tomorrow = today + timedelta(days=1)

    def notice(self, entries, previous=None, activation="first", now=None):
        digest = fingerprint((activation, entries))
        return weekly_notice(entries, activation, digest, previous, now or self.now)

    def test_past_dates_and_slots_ending_now_are_silent(self):
        previous, _ = self.notice([(self.yesterday, [(0, 60)]), (self.today, [(600, 720)])])
        record, notify = self.notice([], previous)
        self.assertFalse(notify)
        self.assertEqual(record["days"], {})

    def test_only_active_end_matters(self):
        previous, _ = self.notice([(self.today, [(480, 900)])])
        for spans, notify in [([(600, 900)], False), ([(480, 930)], True),
                              ([(480, 840)], True), ([(750, 900)], True), ([], True)]:
            with self.subTest(spans=spans):
                self.assertEqual(self.notice([(self.today, spans)], previous)[1], notify)

    def test_future_addition_change_and_removal(self):
        record, _ = self.notice([])
        for spans in [[(0, 60)], [(30, 120)], []]:
            record, notify = self.notice([(self.tomorrow, spans)], record)
            self.assertTrue(notify)

    def test_midnight_and_time_passing_do_not_trigger_delivery(self):
        entries = [(self.today, [(720, 780)])]
        previous, _ = self.notice(entries)
        tomorrow = self.now.replace(hour=0) + timedelta(days=1)
        self.assertFalse(self.notice(entries, previous, now=tomorrow)[1])
        self.assertFalse(self.notice([], previous, now=tomorrow)[1])

    def test_legacy_hash_is_backfilled_and_unknown_change_is_announced(self):
        entries = [(self.today, [(480, 720)])]
        previous, _ = self.notice(entries)
        for legacy in [previous["digest"], {"digest": previous["digest"], "status": "outages"}]:
            record, notify = self.notice(entries, legacy)
            self.assertFalse(notify)
            self.assertFalse(self.notice([], record)[1])
            self.assertTrue(self.notice([], legacy)[1])

    def test_activation_announces_once_even_for_empty_week(self):
        previous, notify = self.notice([])
        self.assertTrue(notify)
        current, notify = self.notice([], previous, activation="second")
        self.assertTrue(notify)
        self.assertFalse(self.notice([], current, activation="second")[1])
