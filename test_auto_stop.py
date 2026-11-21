import os
import tempfile
import unittest
from unittest.mock import patch

from cash_tracker import ConsoleUI, Tracker
from history_store import HistoryStore


class FakePresence:
    def __init__(self, state="playing"):
        self.state = state

    def read(self):
        return {
            "game_state": self.state,
            "faction": "bravo",
            "profit_loss": "+$150",
        }

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


class AutoStopTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(
            lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True)
        )
        self.store = HistoryStore(os.path.join(self.dir, "history.json"))

    def _run_until_game_closes(self, state):
        tracker = Tracker(
            FakePresence(state),
            ui=ConsoleUI(plain=True),
            write_csv=False,
            history=self.store,
            breadcrumbs=FakeBreadcrumbs(),
        )
        with (
            patch("cash_tracker.game_pid", side_effect=[123, None]),
            patch("cash_tracker.is_process_elevated", return_value=False),
            patch("cash_tracker.read_role_xp", return_value={}),
            patch("cash_tracker.PROCESS_CHECK_EVERY", 1),
            patch("cash_tracker.AUTO_STOP_GRACE", 0),
        ):
            tracker.run(0, stay=False)
        return tracker

    def test_closing_game_mid_match_marks_match_interrupted(self):
        self._run_until_game_closes("playing")

        self.assertTrue(self.store.last_match["interrupted"])
        self.assertTrue(self.store.data["sessions"][-1]["interrupted"])

    def test_closing_game_in_menu_does_not_mark_session_interrupted(self):
        self._run_until_game_closes("menu")

        self.assertFalse(self.store.data["sessions"][-1]["interrupted"])


if __name__ == "__main__":
    unittest.main()
