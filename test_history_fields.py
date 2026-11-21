import json
import os
import tempfile
import unittest

from history_store import HistoryStore, VERSION


class HistoryFieldTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        self.path = os.path.join(self.dir, "history.json")

    def test_match_meta_round_trips(self):
        store = HistoryStore(self.path)
        store.start_session(pid=123)
        store.start_match("bravo")
        store.update_match_meta(
            map="Ozeti",
            map_raw="Europe",
            lives=3,
            spawns=2,
            role_xp={"Wardog": 7},
            role_xp_total={"Wardog": 100},
            role_xp_partial=False,
        )
        record = store.end_match()
        self.assertEqual(record["map"], "Ozeti")
        self.assertEqual(record["map_raw"], "Europe")
        self.assertEqual(record["lives"], 3)
        self.assertEqual(record["spawns"], 2)
        self.assertEqual(record["role_xp"], {"Wardog": 7})
        self.assertEqual(record["role_xp_total"], {"Wardog": 100})
        self.assertFalse(record["role_xp_partial"])

        reloaded = HistoryStore(self.path)
        last = reloaded.last_match
        self.assertEqual(last["map"], "Ozeti")
        self.assertEqual(last["lives"], 3)
        self.assertEqual(last["role_xp"], {"Wardog": 7})
        self.assertEqual(reloaded.data["version"], VERSION)

    def test_old_history_without_meta_loads_as_current_version(self):
        old = {
            "version": 1,
            "updated_at": "2026-01-01T00:00:00+00:00",
            "active_session": None,
            "active_match": None,
            "last_session": None,
            "last_match": {
                "id": "old-m1",
                "session_id": "old",
                "balance": 10,
                "spent": 1,
                "gained": 11,
                "faction": "bravo",
                "partial": False,
            },
            "sessions": [],
            "matches": [],
            "lifetime": {},
        }
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(old, fh)
        store = HistoryStore(self.path)
        self.assertEqual(store.data["version"], VERSION)
        self.assertEqual(store.last_match["balance"], 10)
        self.assertIsNone(store.last_match.get("map"))


if __name__ == "__main__":
    unittest.main()
