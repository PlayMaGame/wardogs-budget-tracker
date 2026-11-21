import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from cash_tracker import ConsoleUI, Tracker
from history_store import HistoryStore


class FakeBreadcrumbs:
    def __init__(self, map_name="Ozeti", map_raw="Europe", spawns=None):
        self.map_name = map_name
        self.map_raw = map_raw
        self.spawns = list(spawns or [])

    def poll(self):
        return []

    def current_map(self):
        return self.map_name

    def spawn_events(self):
        return sorted(self.spawns)

    def spawns_since(self, started_at):
        if started_at is None:
            return []
        return [spawn for spawn in self.spawns if spawn >= started_at]


class TrackerMetaTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(
            lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True)
        )
        self.store = HistoryStore(os.path.join(self.dir, "history.json"))
        self.tracker = Tracker(
            None,
            ui=ConsoleUI(plain=True),
            write_csv=False,
            history=self.store,
        )
        self.tracker.breadcrumbs = FakeBreadcrumbs()
        self.tracker.start_session(pid=123)
        self.tracker.saw_menu = True

    def test_match_metadata_is_saved(self):
        with patch("cash_tracker.read_role_xp", return_value={"Wardog": 100}):
            self.tracker.start_match("bravo", profit=None)

        self.assertEqual(self.tracker.match_map, "Ozeti")
        self.assertEqual(self.tracker.match_role_xp_start, {"Wardog": 100})

        now = datetime.now(timezone.utc)
        self.tracker.match_started_utc = now - timedelta(seconds=30)
        self.tracker.breadcrumbs = FakeBreadcrumbs(
            spawns=[
                now - timedelta(seconds=20),
                now - timedelta(seconds=10),
                now - timedelta(seconds=5),
            ]
        )
        with patch("cash_tracker.read_role_xp", return_value={"Wardog": 107}):
            self.tracker.end_match()

        last = self.store.last_match
        self.assertEqual(last["map"], "Ozeti")
        self.assertEqual(last["map_raw"], "Europe")
        self.assertEqual(last["lives"], 3)
        self.assertEqual(last["spawns"], 2)
        self.assertEqual(last["role_xp"], {"Wardog": 7})
        self.assertEqual(last["role_xp_total"], {"Wardog": 107})
        self.assertFalse(last["role_xp_partial"])

    def test_partial_match_marks_role_xp_partial(self):
        self.tracker.saw_menu = False
        with patch("cash_tracker.read_role_xp", return_value={"Wardog": 50}):
            self.tracker.start_match("bravo", profit=25)

        with patch("cash_tracker.read_role_xp", return_value={"Wardog": 52}):
            self.tracker.end_match()

        last = self.store.last_match
        self.assertTrue(last["partial"])
        self.assertTrue(last["role_xp_partial"])
        self.assertEqual(last["role_xp"], {"Wardog": 2})

    def test_header_and_summary_show_map_and_lives(self):
        with patch("cash_tracker.read_role_xp", return_value={"Wardog": 100}):
            self.tracker.start_match("bravo", profit=None)

        now = datetime.now(timezone.utc)
        self.tracker.match_started_utc = now - timedelta(seconds=30)
        self.tracker.breadcrumbs = FakeBreadcrumbs(
            spawns=[
                now - timedelta(seconds=20),
                now - timedelta(seconds=10),
                now - timedelta(seconds=5),
            ]
        )
        with patch("cash_tracker.read_role_xp", return_value={"Wardog": 107}):
            self.tracker.end_match()

        header = "".join(
            text
            for line in self.tracker.header_lines()
            for text, _ in line
        )
        summary = " ".join(self.tracker.summary_lines())
        self.assertIn("Ozeti", header)
        self.assertIn("3 lives", header)
        self.assertIn("Ozeti", summary)
        self.assertIn("3 lives", summary)


if __name__ == "__main__":
    unittest.main()
