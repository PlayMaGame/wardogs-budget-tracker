import os
import tempfile
import unittest

from cash_tracker import Tracker
from history_store import HistoryStore


class FakeOverlay:
    def __init__(self):
        self.values = None

    def update(self, **kwargs):
        self.values = kwargs


class PreviousSessionLastMatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = os.path.join(self.tmp.name, "history.json")

        history = HistoryStore(path)
        history.start_session(pid=111)
        history.start_match("bravo")
        history.update_match(spent=100, gained=200, balance=300)
        self.old_match = history.end_match()
        history.end_session()

        history.start_session(pid=222)
        self.history = history

        self.overlay = FakeOverlay()
        self.tracker = Tracker(
            None,
            history=self.history,
            overlay=self.overlay,
        )
        self.tracker.last_match = dict(self.old_match)

    def test_previous_session_match_is_not_displayed(self):
        self.tracker.update_overlay()

        self.assertEqual(self.overlay.values["label"], "MATCH")
        self.assertIsNone(self.overlay.values["balance"])
        self.assertIsNone(self.overlay.values["spent"])
        self.assertIsNone(self.overlay.values["gained"])

    def test_same_session_match_is_still_displayed(self):
        self.history.end_session()
        session = self.history.start_session(pid=333)
        self.old_match["session_id"] = session["id"]
        self.tracker.last_match = dict(self.old_match)

        self.tracker.update_overlay()

        self.assertEqual(self.overlay.values["label"], "LAST")
        self.assertEqual(self.overlay.values["balance"], 300)


if __name__ == "__main__":
    unittest.main()
