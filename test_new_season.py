import os
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from cash_tracker import ConsoleUI, Tracker
from history_store import HistoryStore
from seasons import InvalidSeasonName, SeasonExistsError, SeasonStore


class FakePresence:
    def read(self):
        return {"game_state": "menu", "faction": "bravo", "profit_loss": "+$0"}

    def shutdown(self):
        pass


class FakeBreadcrumbs:
    map_raw = "Europe"
    map_name = "Ozeti"

    def poll(self):
        return []

    def current_map(self):
        return self.map_name

    def spawn_events(self):
        return []


class NewSeasonTrackerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        self.history = HistoryStore(os.path.join(self.dir, "history.json"))
        self.seasons = SeasonStore(self.dir)
        self.tracker = Tracker(
            FakePresence(),
            ui=ConsoleUI(plain=True),
            write_csv=False,
            history=self.history,
            seasons=self.seasons,
            breadcrumbs=FakeBreadcrumbs(),
        )
        self.tracker.game_pid = None

    def complete_one_match(self):
        self.history.start_session(pid=1)
        self.history.start_match("bravo")
        self.history.update_match(200, 700, 500)
        self.history.end_match()
        self.history.end_session()

    def start_live_match(self):
        self.history.start_session(pid=1)
        self.history.start_match("bravo")
        self.tracker.season_active = True
        self.tracker.season_started = datetime.now()
        self.tracker.current_session_id = self.history.active_session["id"]
        self.tracker.match_active = True
        self.tracker.match_started = datetime.now()
        self.tracker.match_started_utc = datetime.now(timezone.utc)
        self.tracker.match_faction = "bravo"
        self.tracker.match_spent = 100
        self.tracker.match_gained = 400
        self.tracker.match_balance = 300
        self.tracker.current_match_uid = self.history.active_match["id"]

    def test_new_season_archives_current_data_and_resets_counters(self):
        self.complete_one_match()

        with patch("cash_tracker.read_role_xp", return_value={}):
            result = self.tracker.new_season("2")

        self.assertEqual(result["season"]["id"], "2")
        self.assertTrue(
            os.path.exists(os.path.join(self.dir, "seasons", "2", "meta.json"))
        )
        self.assertEqual(self.seasons.current_summary()["matches"], 0)
        self.assertEqual(self.history.data["lifetime"]["matches"], 0)
        self.assertEqual(self.tracker.session_balance, 0)
        self.assertFalse(self.tracker.season_active)

    def test_new_season_marks_a_live_match_interrupted(self):
        self.start_live_match()

        with patch("cash_tracker.read_role_xp", return_value={}):
            self.tracker.new_season("2")

        archived = HistoryStore(
            os.path.join(self.dir, "seasons", "2", "history.json")
        )
        self.assertTrue(archived.last_match["interrupted"])
        self.assertTrue(archived.last_session["interrupted"])
        self.assertFalse(self.tracker.match_active)

    def test_new_season_starts_a_fresh_session_when_game_is_running(self):
        self.complete_one_match()
        self.tracker.game_pid = 123

        with patch("cash_tracker.read_role_xp", return_value={}):
            self.tracker.new_season("2")

        self.assertTrue(self.tracker.season_active)
        self.assertIsNotNone(self.history.active_session)
        self.assertEqual(self.history.data["sessions"], [])
        self.assertEqual(self.history.active_session["matches"], 0)

    def test_invalid_name_does_not_end_the_live_session(self):
        self.start_live_match()

        with self.assertRaises(InvalidSeasonName):
            self.tracker.new_season("../escape")

        self.assertTrue(self.tracker.match_active)
        self.assertTrue(self.tracker.season_active)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "seasons")))

    def test_duplicate_name_does_not_end_the_live_session(self):
        self.complete_one_match()
        with patch("cash_tracker.read_role_xp", return_value={}):
            self.tracker.new_season("2")
        self.tracker.start_session(pid=None)
        self.start_live_match()

        with self.assertRaises(SeasonExistsError):
            self.tracker.new_season("2")

        self.assertTrue(self.tracker.match_active)

    def test_request_is_processed_by_the_tracker_loop(self):
        self.complete_one_match()
        result = {}

        def caller():
            result["value"] = self.tracker.request_new_season("2", timeout=5)

        thread = threading.Thread(target=caller)
        thread.start()
        for _ in range(200):
            with self.tracker._season_lock:
                if self.tracker._season_requests:
                    break
            time.sleep(0.01)
        with patch("cash_tracker.read_role_xp", return_value={}):
            self.tracker.process_new_season_requests()
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result["value"]["season"]["id"], "2")

    def test_timed_out_request_is_removed_from_the_queue(self):
        with self.assertRaises(TimeoutError):
            self.tracker.request_new_season("2", timeout=0.01)
        with self.tracker._season_lock:
            self.assertEqual(self.tracker._season_requests, [])


if __name__ == "__main__":
    unittest.main()
