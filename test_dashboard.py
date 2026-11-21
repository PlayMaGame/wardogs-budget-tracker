import csv
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from dashboard import DashboardData


def match(
    mid,
    started,
    ended,
    faction,
    map_name,
    balance,
    spent,
    gained,
    duration,
    lives,
    spawns,
    role_xp,
):
    return {
        "id": mid,
        "session_id": "s1",
        "started_at": started,
        "ended_at": ended,
        "faction": faction,
        "map": map_name,
        "map_raw": map_name,
        "balance": balance,
        "spent": spent,
        "gained": gained,
        "duration_s": duration,
        "lives": lives,
        "spawns": spawns,
        "role_xp": role_xp,
        "role_xp_total": role_xp,
        "role_xp_partial": False,
        "partial": False,
        "resumed": False,
        "active": False,
        "interrupted": False,
    }


class DashboardDataTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        self.history_path = os.path.join(self.dir, "history.json")
        self.events_path = os.path.join(self.dir, "cash_events.csv")

        matches = [
            match(
                "s1-m1",
                "2026-11-01T10:05:00+00:00",
                "2026-11-01T10:25:00+00:00",
                "bravo",
                "Ozeti",
                500,
                200,
                700,
                1200,
                2,
                1,
                {"Wardog": 10},
            ),
            match(
                "s1-m2",
                "2026-11-01T11:00:00+00:00",
                "2026-11-01T11:40:00+00:00",
                "alpha",
                "Bakurani",
                -300,
                500,
                200,
                2400,
                3,
                2,
                {"Medic": 5},
            ),
            match(
                "s1-m3",
                "2026-11-01T12:00:00+00:00",
                "2026-11-01T13:00:00+00:00",
                "bravo",
                "Ozeti",
                1000,
                300,
                1300,
                3600,
                4,
                3,
                {"Wardog": 20},
            ),
            match(
                "s1-m4",
                "2026-11-01T13:30:00+00:00",
                "2026-11-01T13:50:00+00:00",
                "bravo",
                "NorthAmerica",
                200,
                100,
                300,
                1200,
                1,
                0,
                {"Wardog": 5},
            ),
        ]
        state = {
            "version": 2,
            "updated_at": "2026-11-01T14:00:00+00:00",
            "active_session": {
                "id": "s2",
                "started_at": "2026-11-01T14:00:00+00:00",
                "ended_at": None,
                "game_pid": 1,
                "balance": 0,
                "spent": 0,
                "gained": 0,
                "matches": 0,
                "active": True,
                "interrupted": False,
                "last_seen_at": "2026-11-01T14:00:00+00:00",
            },
            "active_match": None,
            "last_session": {
                "id": "s1",
                "started_at": "2026-11-01T10:00:00+00:00",
                "ended_at": "2026-11-01T13:55:00+00:00",
                "game_pid": 1,
                "balance": 1400,
                "spent": 1100,
                "gained": 2500,
                "matches": 4,
                "active": False,
                "interrupted": False,
                "last_seen_at": "2026-11-01T13:55:00+00:00",
            },
            "last_match": matches[-1],
            "sessions": [
                {
                    "id": "s1",
                    "started_at": "2026-11-01T10:00:00+00:00",
                    "ended_at": "2026-11-01T13:55:00+00:00",
                    "game_pid": 1,
                    "balance": 1400,
                    "spent": 1100,
                    "gained": 2500,
                    "matches": 4,
                    "active": False,
                    "interrupted": False,
                    "last_seen_at": "2026-11-01T13:55:00+00:00",
                }
            ],
            "matches": matches,
            "lifetime": {
                "sessions": 1,
                "matches": 4,
                "balance": 1400,
                "spent": 1100,
                "gained": 2500,
            },
        }
        with open(self.history_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh)

        rows = [
            ("2026-11-01T10:05:00+00:00", 1, "s1", "s1-m1", "match_start", "", "", "", "bravo"),
            ("2026-11-01T10:06:00+00:00", 1, "s1", "s1-m1", "gained", 700, 700, 700, "bravo"),
            ("2026-11-01T10:07:00+00:00", 1, "s1", "s1-m1", "spent", -200, 500, 500, "bravo"),
            ("2026-11-01T11:00:00+00:00", 2, "s1", "s1-m2", "match_start", "", "", "", "alpha"),
            ("2026-11-01T11:01:00+00:00", 2, "s1", "s1-m2", "spent", -500, -500, -500, "alpha"),
            ("2026-11-01T11:02:00+00:00", 2, "s1", "s1-m2", "gained", 200, -300, -300, "alpha"),
            ("2026-11-01T12:00:00+00:00", 3, "s1", "s1-m3", "match_start", "", "", "", "bravo"),
            ("2026-11-01T12:01:00+00:00", 3, "s1", "s1-m3", "gained", 1300, 1300, 1300, "bravo"),
            ("2026-11-01T12:02:00+00:00", 3, "s1", "s1-m3", "spent", -300, 1000, 1000, "bravo"),
            ("2026-11-01T13:30:00+00:00", 4, "s1", "s1-m4", "match_start", "", "", "", "bravo"),
            ("2026-11-01T13:31:00+00:00", 4, "s1", "s1-m4", "gained", 300, 300, 300, "bravo"),
            ("2026-11-01T13:32:00+00:00", 4, "s1", "s1-m4", "spent", -100, 200, 200, "bravo"),
        ]
        with open(self.events_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
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
            )
            writer.writerows(rows)

        self.data = DashboardData(self.history_path, self.events_path)

    def test_state_exposes_active_and_lifetime(self):
        state = self.data.state()
        self.assertEqual(state["active_session"]["id"], "s2")
        self.assertEqual(state["lifetime"]["matches"], 4)
        self.assertEqual(len(state["recent_events"]), 12)

    def test_matches_are_newest_first_and_filterable(self):
        matches = self.data.matches()
        self.assertEqual([m["id"] for m in matches], ["s1-m4", "s1-m3", "s1-m2", "s1-m1"])
        self.assertEqual(len(self.data.matches(faction="bravo")), 3)
        self.assertEqual(len(self.data.matches(faction="Valkyra")), 3)
        self.assertEqual(len(self.data.matches(map_name="Ozeti")), 2)
        self.assertEqual(len(self.data.matches(result="loss")), 1)
        self.assertEqual(len(self.data.matches(q="oze")), 2)
        self.assertEqual(len(self.data.matches(since="2026-11-01T11:30:00+00:00")), 2)

    def test_sessions_are_newest_first_and_include_active(self):
        sessions = self.data.sessions()
        self.assertEqual([s["id"] for s in sessions], ["s2", "s1"])
        self.assertTrue(sessions[0]["active"])
        self.assertEqual(sessions[1]["duration_s"], 14100)

    def test_session_detail_has_matches_and_cumulative_curve(self):
        detail = self.data.session("s1")
        self.assertEqual(detail["id"], "s1")
        self.assertEqual(
            [m["id"] for m in detail["matches"]],
            ["s1-m4", "s1-m3", "s1-m2", "s1-m1"],
        )
        self.assertEqual(detail["curve"][0]["balance"], 0)
        self.assertEqual(detail["curve"][-1]["balance"], 1400)
        self.assertEqual(
            detail["curve"][-1]["time"], "2026-11-01T13:55:00+00:00"
        )
        by_time = {point["time"]: point["balance"] for point in detail["curve"]}
        self.assertEqual(by_time["2026-11-01T11:02:00+00:00"], 200)
        self.assertEqual(by_time["2026-11-01T12:01:00+00:00"], 1500)
        self.assertIsNone(self.data.session("missing"))

    def test_session_without_matches_has_start_point(self):
        detail = self.data.session("s2")
        self.assertEqual(detail["matches"], [])
        self.assertEqual(
            detail["curve"],
            [{"time": "2026-11-01T14:00:00+00:00", "balance": 0}],
        )

    def test_history_retries_transient_permission_error(self):
        real_open = open
        calls = {"count": 0}

        def flaky(path, *args, **kwargs):
            if (
                os.path.abspath(path) == os.path.abspath(self.history_path)
                and calls["count"] < 3
            ):
                calls["count"] += 1
                raise PermissionError(13, "locked", path)
            return real_open(path, *args, **kwargs)

        with mock.patch("builtins.open", side_effect=flaky):
            detail = self.data.session("s1")

        self.assertIsNotNone(detail)
        self.assertEqual(detail["id"], "s1")
        self.assertEqual(calls["count"], 3)

    def test_history_falls_back_to_backup_when_primary_is_locked(self):
        shutil.copyfile(self.history_path, self.history_path + ".bak")
        real_open = open

        def locked_primary(path, *args, **kwargs):
            if os.path.abspath(path) == os.path.abspath(self.history_path):
                raise PermissionError(13, "locked", path)
            return real_open(path, *args, **kwargs)

        with mock.patch("builtins.open", side_effect=locked_primary):
            detail = self.data.session("s1")

        self.assertIsNotNone(detail)
        self.assertEqual(detail["id"], "s1")

    def test_match_detail_has_events_and_curve(self):
        detail = self.data.match("s1-m3")
        self.assertEqual(detail["id"], "s1-m3")
        self.assertEqual(len(detail["events"]), 3)
        self.assertEqual(detail["curve"][0]["balance"], 0)
        self.assertEqual(detail["curve"][-1]["balance"], 1000)
        self.assertIsNone(self.data.match("missing"))

    def test_insights(self):
        insights = self.data.insights()
        self.assertEqual(insights["totals"]["matches"], 4)
        self.assertEqual(insights["totals"]["net"], 1400)
        self.assertEqual(insights["per_faction"]["Valkyra"]["matches"], 3)
        self.assertEqual(insights["per_faction"]["Valkyra"]["net"], 1700)
        self.assertEqual(insights["per_faction"]["Lonestar"]["net"], -300)
        self.assertEqual(insights["per_map"]["Ozeti"]["matches"], 2)
        self.assertEqual(insights["role_xp"]["Wardog"], 35)
        self.assertEqual(insights["streaks"]["current"], 2)
        self.assertEqual(insights["streaks"]["best"], 2)
        self.assertEqual(insights["best_match"]["id"], "s1-m3")
        self.assertEqual(insights["worst_match"]["id"], "s1-m2")

    def test_export_csv(self):
        text = self.data.export_csv()
        rows = list(csv.reader(text.splitlines()))
        self.assertEqual(rows[0][0], "id")
        self.assertEqual(len(rows), 5)

    def test_missing_files_are_safe(self):
        data = DashboardData(
            os.path.join(self.dir, "missing.json"),
            os.path.join(self.dir, "missing.csv"),
        )
        self.assertIsNone(data.state()["active_session"])
        self.assertEqual(data.matches(), [])
        self.assertEqual(data.insights()["totals"]["matches"], 0)


if __name__ == "__main__":
    unittest.main()
