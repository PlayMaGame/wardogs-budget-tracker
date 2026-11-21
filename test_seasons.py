import csv
import json
import os
import shutil
import tempfile
import unittest

from history_store import HistoryStore
from seasons import (
    InvalidSeasonName,
    SeasonExistsError,
    SeasonNotFound,
    SeasonStore,
    sanitize_name,
)


class SanitizeNameTests(unittest.TestCase):
    def test_trims_and_accepts_numbers_and_spaces(self):
        self.assertEqual(sanitize_name(" 2 "), "2")
        self.assertEqual(sanitize_name("Season 2"), "Season 2")

    def test_rejects_empty_and_dot_names(self):
        for value in ("", "   ", ".", "..", "..."):
            with self.subTest(value=value):
                with self.assertRaises(InvalidSeasonName):
                    sanitize_name(value)

    def test_rejects_path_characters(self):
        for value in ("../2", "a/b", "a\\b", "a:b", 'a"b', "a<b", "a>b", "a|b", "a?b", "a*b"):
            with self.subTest(value=value):
                with self.assertRaises(InvalidSeasonName):
                    sanitize_name(value)

    def test_rejects_reserved_current(self):
        for value in ("current", "CURRENT", " Current "):
            with self.subTest(value=value):
                with self.assertRaises(InvalidSeasonName):
                    sanitize_name(value)

    def test_rejects_too_long_and_trailing_dot(self):
        with self.assertRaises(InvalidSeasonName):
            sanitize_name("x" * 41)
        with self.assertRaises(InvalidSeasonName):
            sanitize_name("season.")


class HistoryResetTests(unittest.TestCase):
    def test_reset_clears_sessions_matches_and_lifetime(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(os.path.join(folder, "history.json"))
            store.start_session(pid=1)
            store.start_match("alpha")
            store.update_match(100, 300, 200)
            store.end_match()
            store.end_session()

            store.reset()

            self.assertEqual(store.data["sessions"], [])
            self.assertEqual(store.data["matches"], [])
            self.assertEqual(store.data["lifetime"]["matches"], 0)
            self.assertIsNone(store.active_session)
            self.assertIsNone(store.active_match)
            reloaded = HistoryStore(store.path)
            self.assertEqual(reloaded.data["lifetime"]["balance"], 0)


class SeasonStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        self.history = HistoryStore(os.path.join(self.dir, "history.json"))
        self.history.start_session(pid=123)
        self.history.start_match("bravo")
        self.history.update_match(200, 700, 500)
        self.history.end_match()
        self.history.end_session()
        self.events_path = os.path.join(self.dir, "cash_events.csv")
        with open(self.events_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
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
            writer.writerow(
                [
                    "2026-11-01T10:00:00+00:00",
                    1,
                    "s1",
                    "s1-m1",
                    "gained",
                    700,
                    700,
                    700,
                    "bravo",
                ]
            )
        self.history_path = self.history.path
        self.bak_path = self.history_path + ".bak"
        with open(self.history_path, "rb") as source:
            with open(self.bak_path, "wb") as target:
                target.write(source.read())
        with open(self.events_path, "rb") as source:
            with open(self.events_path + ".bak", "wb") as target:
                target.write(source.read())
        self.store = SeasonStore(self.dir)

    def test_snapshot_reports_current_lifetime(self):
        snapshot = self.store.snapshot()
        self.assertTrue(snapshot["current"]["current"])
        self.assertEqual(snapshot["current"]["summary"]["matches"], 1)
        self.assertEqual(snapshot["current"]["summary"]["balance"], 500)

    def test_archive_moves_current_files_and_writes_meta(self):
        descriptor = self.store.archive("2")

        season_dir = os.path.join(self.dir, "seasons", "2")
        self.assertTrue(os.path.isdir(season_dir))
        for name in (
            "history.json",
            "history.json.bak",
            "cash_events.csv",
            "cash_events.csv.bak",
        ):
            self.assertTrue(os.path.exists(os.path.join(season_dir, name)), name)
        self.assertFalse(os.path.exists(self.history_path))
        self.assertFalse(os.path.exists(self.events_path))

        with open(os.path.join(season_dir, "meta.json"), encoding="utf-8") as handle:
            meta = json.load(handle)
        self.assertEqual(meta["name"], "2")
        self.assertEqual(meta["summary"]["matches"], 1)
        self.assertEqual(meta["summary"]["balance"], 500)
        self.assertEqual(descriptor["id"], "2")

    def test_archive_keeps_a_backup_of_each_file(self):
        self.store.archive("2")
        season_dir = os.path.join(self.dir, "seasons", "2")
        self.assertTrue(os.path.exists(os.path.join(season_dir, "history.json.bak")))
        self.assertTrue(os.path.exists(os.path.join(season_dir, "cash_events.csv.bak")))

    def test_duplicate_archive_name_is_rejected(self):
        self.store.archive("2")
        with self.assertRaises(SeasonExistsError):
            self.store.archive("2")

    def test_paths_rejects_traversal_and_missing_seasons(self):
        with self.assertRaises(InvalidSeasonName):
            self.store.paths("../outside")
        with self.assertRaises(SeasonNotFound):
            self.store.paths("missing")

    def test_paths_for_archived_season(self):
        self.store.archive("2")
        history_path, events_path = self.store.paths("2")
        self.assertEqual(
            os.path.normcase(history_path),
            os.path.normcase(os.path.join(self.dir, "seasons", "2", "history.json")),
        )
        self.assertEqual(
            os.path.normcase(events_path),
            os.path.normcase(os.path.join(self.dir, "seasons", "2", "cash_events.csv")),
        )

    def test_validate_new_name_checks_format_and_duplicates(self):
        self.assertEqual(self.store.validate_new("3"), "3")
        self.store.archive("3")
        with self.assertRaises(SeasonExistsError):
            self.store.validate_new("3")
        with self.assertRaises(InvalidSeasonName):
            self.store.validate_new("../escape")

    def test_reset_current_creates_empty_history_and_csv_header(self):
        self.store.archive("2")
        self.store.reset_current()

        fresh = HistoryStore(self.history_path)
        self.assertEqual(fresh.data["sessions"], [])
        self.assertEqual(fresh.data["matches"], [])
        self.assertEqual(fresh.data["lifetime"]["matches"], 0)
        self.assertIsNone(fresh.active_session)

        with open(self.events_path, newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(
            rows,
            [
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
            ],
        )


if __name__ == "__main__":
    unittest.main()
