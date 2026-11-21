import os
import shutil
import tempfile
import unittest

from gold_price import GoldPriceService, compute_snapshot

DAY_MS = 86_400_000
START_MS = 1_760_000_000_000


def make_payload(prices, updated=None):
    points = [
        {"t": START_MS + index * DAY_MS, "price": price}
        for index, price in enumerate(prices)
    ]
    return {
        "updatedAt": updated if updated is not None else points[-1]["t"],
        "points": points,
        "stats": {"changes": {"7d": 0.05}},
    }


class ComputeSnapshotTests(unittest.TestCase):
    def test_star_boundaries_follow_the_window_low(self):
        cases = [
            (1000, 3),
            (1400, 2),
            (1401, 1),
            (1700, 1),
            (1701, 0),
        ]
        for current, expected in cases:
            with self.subTest(current=current):
                snapshot = compute_snapshot(make_payload([1000] * 89 + [current]))
                self.assertEqual(snapshot["low90"], 1000)
                self.assertEqual(snapshot["star2_at"], 1400)
                self.assertEqual(snapshot["star1_at"], 1700)
                self.assertEqual(snapshot["buy_at"], 2000)
                self.assertEqual(snapshot["stars"], expected)

    def test_zones_use_two_times_low_then_the_median(self):
        prices = [1000] * 44 + [5000] * 45

        def zone_for(current):
            return compute_snapshot(make_payload(prices + [current]))["zone"]

        self.assertEqual(zone_for(2000), "buy")
        self.assertEqual(zone_for(2001), "fair")
        self.assertEqual(zone_for(3000), "fair")
        self.assertEqual(zone_for(5001), "wait")

    def test_window_is_the_last_ninety_points(self):
        old_low = [100] * 10
        recent = [1000] * 90
        snapshot = compute_snapshot(make_payload(old_low + recent))
        self.assertEqual(snapshot["window_days"], 90)
        self.assertEqual(snapshot["low90"], 1000)
        self.assertEqual(snapshot["all_time_low"], 100)
        self.assertFalse(snapshot["record"])
        self.assertEqual(snapshot["stars"], 3)

    def test_old_low_leaves_the_window(self):
        with_low = [1000] + [2000] * 89
        self.assertEqual(compute_snapshot(make_payload(with_low))["low90"], 1000)
        after_roll = with_low + [2000]
        snapshot = compute_snapshot(make_payload(after_roll))
        self.assertEqual(snapshot["low90"], 2000)
        self.assertEqual(snapshot["window_days"], 90)

    def test_record_and_percentile(self):
        snapshot = compute_snapshot(make_payload([5000] * 89 + [900]))
        self.assertTrue(snapshot["record"])
        self.assertEqual(snapshot["percentile"], 0)
        self.assertEqual(snapshot["low90"], 900)
        self.assertEqual(snapshot["history"][-1][1], 900)

    def test_empty_payload_raises(self):
        with self.assertRaises(ValueError):
            compute_snapshot({"points": []})


class GoldServiceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        self.cache = os.path.join(self.dir, "gold_price.json")

    def test_fetch_writes_cache_and_survives_restart(self):
        service = GoldPriceService(
            cache_path=self.cache,
            fetcher=lambda: make_payload([1000] * 90),
        )
        snapshot = service.snapshot()
        self.assertEqual(snapshot["price"], 1000)
        self.assertTrue(os.path.exists(self.cache))

        offline = GoldPriceService(
            cache_path=self.cache,
            fetcher=lambda: (_ for _ in ()).throw(OSError("no network")),
        )
        cached = offline.peek()
        self.assertEqual(cached["price"], 1000)
        offline.refresh()
        stale = offline.peek()
        self.assertTrue(stale["offline"])
        self.assertEqual(stale["price"], 1000)

    def test_cold_offline_returns_placeholder(self):
        service = GoldPriceService(
            cache_path=os.path.join(self.dir, "missing.json"),
            fetcher=lambda: (_ for _ in ()).throw(OSError("no network")),
        )
        snapshot = service.snapshot()
        self.assertFalse(snapshot["ok"])
        self.assertTrue(snapshot["offline"])
        self.assertIsNone(snapshot["price"])


if __name__ == "__main__":
    unittest.main()
