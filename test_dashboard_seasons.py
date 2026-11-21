import json
import os
import shutil
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dashboard import DashboardData, DashboardServer, is_loopback_address
from history_store import HistoryStore
from seasons import InvalidSeasonName, SeasonExistsError, SeasonStore


def write_history(path, balance, matches=1):
    store = HistoryStore(path)
    store.data["lifetime"] = {
        "sessions": 1,
        "matches": matches,
        "balance": balance,
        "spent": 100,
        "gained": balance + 100,
    }
    store.save()


class SeasonRoutingTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        self.history_path = os.path.join(self.dir, "history.json")
        self.events_path = os.path.join(self.dir, "cash_events.csv")
        write_history(self.history_path, 100)
        with open(self.events_path, "w", encoding="utf-8") as handle:
            handle.write(
                "time_utc,match_id,session_id,match_uid,event,amount,"
                "match_balance,session_balance,faction\n"
            )

        season_dir = os.path.join(self.dir, "seasons", "2")
        os.makedirs(season_dir)
        write_history(os.path.join(season_dir, "history.json"), 500, matches=3)
        with open(os.path.join(season_dir, "cash_events.csv"), "w", encoding="utf-8") as handle:
            handle.write(
                "time_utc,match_id,session_id,match_uid,event,amount,"
                "match_balance,session_balance,faction\n"
            )

        self.store = SeasonStore(self.dir)
        self.server = DashboardServer(
            host="127.0.0.1",
            port=0,
            data=DashboardData(self.history_path, self.events_path),
            season_store=self.store,
            auto_open=False,
        )
        self.addCleanup(self.server.stop)
        self.assertTrue(self.server.start())
        self.base = f"http://127.0.0.1:{self.server.port}"

    def get_json(self, path):
        with urlopen(self.base + path, timeout=5) as response:
            return json.loads(response.read())

    def post_json(self, path, payload):
        request = Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())

    def test_seasons_endpoint_lists_current_and_archived(self):
        payload = self.get_json("/api/seasons")
        self.assertTrue(payload["current"]["current"])
        self.assertEqual(payload["current"]["summary"]["balance"], 100)
        self.assertEqual(payload["seasons"][0]["id"], "2")
        self.assertEqual(payload["seasons"][0]["summary"]["balance"], 500)
        self.assertTrue(payload["can_archive"])

    def test_state_can_select_an_archived_season(self):
        current = self.get_json("/api/state")
        archived = self.get_json("/api/state?season=2")
        self.assertEqual(current["lifetime"]["balance"], 100)
        self.assertEqual(archived["lifetime"]["balance"], 500)

    def test_unknown_season_is_404(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + "/api/state?season=missing", timeout=5)
        self.assertEqual(caught.exception.code, 404)

    def test_traversal_season_is_400(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + "/api/state?season=..%2Foutside", timeout=5)
        self.assertEqual(caught.exception.code, 400)

    def test_post_new_season_archives_and_resets_current(self):
        status, payload = self.post_json("/api/new-season", {"name": "3"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["season"]["id"], "3")
        self.assertTrue(os.path.isdir(os.path.join(self.dir, "seasons", "3")))
        self.assertTrue(os.path.exists(self.history_path))
        self.assertEqual(self.store.current_summary()["matches"], 0)

        snapshot = self.get_json("/api/seasons")
        self.assertIn("3", [season["id"] for season in snapshot["seasons"]])

    def test_post_new_season_rejects_invalid_name(self):
        with self.assertRaises(HTTPError) as caught:
            self.post_json("/api/new-season", {"name": "../escape"})
        self.assertEqual(caught.exception.code, 400)

    def test_post_new_season_rejects_duplicate_name(self):
        with self.assertRaises(HTTPError) as caught:
            self.post_json("/api/new-season", {"name": "2"})
        self.assertEqual(caught.exception.code, 409)


class CallbackTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        self.calls = []
        self.server = DashboardServer(
            host="127.0.0.1",
            port=0,
            data=DashboardData(
                os.path.join(self.dir, "history.json"),
                os.path.join(self.dir, "cash_events.csv"),
            ),
            on_new_season=self.callback,
            auto_open=False,
        )
        self.addCleanup(self.server.stop)
        self.assertTrue(self.server.start())
        self.base = f"http://127.0.0.1:{self.server.port}"

    def callback(self, name):
        self.calls.append(name)
        if name == "invalid":
            raise InvalidSeasonName("bad name")
        if name == "duplicate":
            raise SeasonExistsError("already there")
        return {"ok": True, "season": {"id": name, "name": name}}

    def post(self, name):
        request = Request(
            self.base + "/api/new-season",
            data=json.dumps({"name": name}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())

    def test_callback_is_used_instead_of_local_archive(self):
        status, payload = self.post("2")
        self.assertEqual(status, 200)
        self.assertEqual(payload["season"]["id"], "2")
        self.assertEqual(self.calls, ["2"])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "seasons", "2")))

    def test_callback_validation_errors_map_to_http_statuses(self):
        with self.assertRaises(HTTPError) as invalid:
            self.post("invalid")
        self.assertEqual(invalid.exception.code, 400)
        with self.assertRaises(HTTPError) as duplicate:
            self.post("duplicate")
        self.assertEqual(duplicate.exception.code, 409)


class LoopbackTests(unittest.TestCase):
    def test_loopback_addresses(self):
        self.assertTrue(is_loopback_address("127.0.0.1"))
        self.assertTrue(is_loopback_address("::1"))
        self.assertTrue(is_loopback_address("::ffff:127.0.0.1"))
        self.assertFalse(is_loopback_address("192.168.1.20"))
        self.assertFalse(is_loopback_address(""))


if __name__ == "__main__":
    unittest.main()
