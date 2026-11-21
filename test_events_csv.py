import csv
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import cash_tracker
from cash_tracker import (
    EVENTS_HEADER,
    Tracker,
    ensure_events_csv,
    match_curve_from_events,
    parse_local,
)
from history_store import HistoryStore


OLD_HEADER = [
    "time_utc",
    "match_id",
    "event",
    "amount",
    "match_balance",
    "session_balance",
    "faction",
]
NEW_HEADER = [
    "time_utc",
    "match_id",
    "session_id",
    "match_uid",
    "event",
    "amount",
    "match_balance",
    "session_balance",
    "faction",
]


class EventsCsvTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        self.path = os.path.join(self.dir, "cash_events.csv")

    def write_old(self):
        with open(self.path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(OLD_HEADER)
            writer.writerow(
                [
                    "2026-11-01T13:33:05+00:00",
                    "1",
                    "match_start",
                    "",
                    "0",
                    "0",
                    "bravo",
                ]
            )

    def test_old_header_is_rewritten_with_new_columns(self):
        self.write_old()
        self.assertTrue(ensure_events_csv(self.path))
        with open(self.path, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(rows[0], NEW_HEADER)
        self.assertEqual(
            rows[1],
            [
                "2026-11-01T13:33:05+00:00",
                "1",
                "",
                "",
                "match_start",
                "",
                "0",
                "0",
                "bravo",
            ],
        )
        self.assertTrue(os.path.exists(self.path + ".bak"))

    def test_new_header_is_left_alone(self):
        with open(self.path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(NEW_HEADER)
        self.assertFalse(ensure_events_csv(self.path))
        with open(self.path, newline="", encoding="utf-8") as fh:
            self.assertEqual(next(csv.reader(fh)), NEW_HEADER)

    def test_missing_file_is_not_created(self):
        self.assertFalse(ensure_events_csv(self.path))
        self.assertFalse(os.path.exists(self.path))


class MatchCurveTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        self.path = os.path.join(self.dir, "cash_events.csv")
        self.started = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)

    def write(self, rows):
        with open(self.path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(NEW_HEADER)
            writer.writerows(rows)

    def test_curve_uses_event_balances_and_skips_other_matches(self):
        self.write([
            ["2026-11-01T10:00:30+00:00", "1", "s1", "m1", "gained", "5000", "5000", "5000", "bravo"],
            ["2026-11-01T10:01:00+00:00", "1", "s1", "m2", "gained", "9999", "9999", "9999", "bravo"],
            ["2026-11-01T10:02:00+00:00", "1", "s1", "m1", "spent", "-2000", "3000", "3000", "bravo"],
            ["2026-11-01T10:03:00+00:00", "1", "s1", "m1", "match_end", "", "3000", "3000", "bravo"],
        ])
        self.assertEqual(
            match_curve_from_events("m1", self.started, self.path),
            [(0.0, 0.0), (30.0, 5000), (120.0, 3000)],
        )

    def test_blank_amount_uses_running_match_balance(self):
        self.write([
            ["2026-11-01T10:01:00+00:00", "1", "s1", "m1", "gained", "", "7000", "7000", "bravo"],
            ["2026-11-01T10:02:00+00:00", "1", "s1", "m1", "spent", "-1000", "6000", "6000", "bravo"],
        ])
        self.assertEqual(
            match_curve_from_events("m1", self.started, self.path),
            [(0.0, 0.0), (60.0, 7000), (120.0, 6000)],
        )

    def test_missing_or_empty_log_returns_empty(self):
        self.assertEqual(match_curve_from_events("m1", self.started, self.path), [])
        self.write([])
        self.assertEqual(match_curve_from_events("m1", self.started, self.path), [])


class ResumeMatchCurveTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        self.history = HistoryStore(os.path.join(self.dir, "history.json"))
        self.history.start_session(pid=1)
        self.history.start_match("bravo")
        self.uid = self.history.active_match["id"]
        started = parse_local(self.history.active_match["started_at"])
        self.started = started.astimezone(timezone.utc)
        self.events = os.path.join(self.dir, "cash_events.csv")
        original = cash_tracker.EVENTS_CSV
        cash_tracker.EVENTS_CSV = self.events
        self.addCleanup(setattr, cash_tracker, "EVENTS_CSV", original)

    def test_resume_backfills_the_event_curve(self):
        with open(self.events, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(EVENTS_HEADER)
            for seconds, balance in ((30, 0), (60, 5000), (120, 3000)):
                writer.writerow([
                    (self.started + timedelta(seconds=seconds)).isoformat(),
                    "1", "s1", self.uid, "gained", "1000",
                    str(balance), str(balance), "bravo",
                ])
        tracker = Tracker(None, history=self.history, write_csv=False)
        self.assertTrue(tracker.resume_match())
        self.assertEqual(
            tracker.match_curve,
            [(0.0, 0.0), (30.0, 0), (60.0, 5000), (120.0, 3000)],
        )


if __name__ == "__main__":
    unittest.main()
