import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone

import msgpack

from breadcrumbs import (
    BreadcrumbReader,
    display_map_name,
    find_latest_run_dir,
    read_breadcrumbs,
)


def crumb(timestamp, category, message, data=None):
    return {
        "timestamp": timestamp,
        "category": category,
        "message": message,
        "data": data or {},
    }


def pack(path, items):
    with open(path, "wb") as fh:
        for item in items:
            fh.write(msgpack.packb(item))


class BreadcrumbFileTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def run_dir(self, name="test.run"):
        path = os.path.join(
            self.root, "Wardogs", ".sentry-native", name
        )
        os.makedirs(path, exist_ok=True)
        return path

    def test_reads_concatenated_objects_and_tolerates_partial_tail(self):
        path = os.path.join(self.run_dir(), "__sentry-breadcrumb1")
        pack(
            path,
            [
                crumb(
                    "2026-11-05T21:20:55.171295Z",
                    "Unreal",
                    "PostLoadMapWithWorld",
                    {"Map": "Europe"},
                ),
                crumb("2026-11-05T21:21:00.529214Z", "Player", "Player Spawned"),
            ],
        )
        with open(path, "ab") as fh:
            fh.write(b"\x81")
        items = read_breadcrumbs(path)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["data"]["Map"], "Europe")

    def test_display_map_name_aliases_known_maps(self):
        self.assertEqual(display_map_name("Europe"), "Ozeti")
        self.assertEqual(display_map_name("Kavkazi"), "Bakurani")
        self.assertEqual(display_map_name("NorthAmerica"), "Zestafona")
        self.assertEqual(display_map_name("Russia"), "Sabatulo")
        self.assertIsNone(display_map_name(None))


class BreadcrumbReaderTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def make_run(self, name="test.run", items=None):
        run = os.path.join(self.root, "Wardogs", ".sentry-native", name)
        os.makedirs(run, exist_ok=True)
        path = os.path.join(run, "__sentry-breadcrumb1")
        pack(path, items or [])
        return run, path

    def test_poll_reads_map_and_spawns_and_dedupes(self):
        self.make_run(
            items=[
                crumb(
                    "2026-11-05T21:20:55.171295Z",
                    "Unreal",
                    "PostLoadMapWithWorld",
                    {"Map": "Europe"},
                ),
                crumb("2026-11-05T21:21:00.529214Z", "Player", "Player Spawned"),
                crumb("2026-11-05T21:29:07.805098Z", "Player", "Player Spawned"),
            ]
        )
        reader = BreadcrumbReader(install_dir=self.root)
        reader.poll()
        self.assertEqual(reader.current_map(), "Ozeti")
        self.assertEqual(reader.map_raw, "Europe")
        self.assertEqual(len(reader.spawn_events()), 2)
        reader.poll()
        self.assertEqual(len(reader.spawn_events()), 2)

    def test_frontend_map_is_not_a_match_map(self):
        self.make_run(
            items=[
                crumb(
                    "2026-11-05T21:20:21.066247Z",
                    "Unreal",
                    "PostLoadMapWithWorld",
                    {"Map": "Frontend"},
                )
            ]
        )
        reader = BreadcrumbReader(install_dir=self.root)
        reader.poll()
        self.assertIsNone(reader.current_map())
        self.assertEqual(reader.map_raw, "Frontend")

    def test_spawn_events_are_utc_datetimes(self):
        self.make_run(
            items=[
                crumb("2026-11-05T21:21:00.529214Z", "Player", "Player Spawned"),
            ]
        )
        reader = BreadcrumbReader(install_dir=self.root)
        reader.poll()
        spawns = reader.spawn_events()
        self.assertEqual(spawns, [datetime(2026, 11, 5, 21, 21, 0, 529214, tzinfo=timezone.utc)])

    def test_newest_run_directory_is_used(self):
        old_run, old_path = self.make_run(
            "old.run",
            [
                crumb(
                    "2026-11-05T21:20:55.171295Z",
                    "Unreal",
                    "PostLoadMapWithWorld",
                    {"Map": "Europe"},
                )
            ],
        )
        new_run, new_path = self.make_run(
            "new.run",
            [
                crumb(
                    "2026-11-05T22:20:55.171295Z",
                    "Unreal",
                    "PostLoadMapWithWorld",
                    {"Map": "Kavkazi"},
                )
            ],
        )
        os.utime(old_path, (1, 1))
        os.utime(new_path, (2, 2))
        self.assertEqual(find_latest_run_dir(self.root), new_run)
        reader = BreadcrumbReader(install_dir=self.root)
        reader.poll()
        self.assertEqual(reader.current_map(), "Bakurani")


if __name__ == "__main__":
    unittest.main()
