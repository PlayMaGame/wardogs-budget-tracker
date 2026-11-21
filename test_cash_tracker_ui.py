import os
import unittest

from cash_tracker import (
    DIM,
    GREEN,
    RED,
    cash_per_hour,
    cash_per_hour_str,
    elevation_badge,
    is_process_elevated,
)
from overlay import (
    Overlay,
    _clock,
    _linear_points,
    _log_points,
    _month_splits,
    _ordinal,
    _short_money,
    _signed_short,
)


class ElevationBadgeTests(unittest.TestCase):
    def test_elevated_uses_red_and_admin(self):
        segments = elevation_badge(True)
        self.assertEqual(segments[0], ("● ", RED))
        self.assertEqual("".join(text for text, _ in segments), "● ADMIN")

    def test_not_elevated_uses_green_and_ok(self):
        segments = elevation_badge(False)
        self.assertEqual(segments[0], ("● ", GREEN))
        self.assertEqual("".join(text for text, _ in segments), "● OK")

    def test_unknown_uses_dim(self):
        segments = elevation_badge(None)
        self.assertEqual(segments[0], ("● ", DIM))
        self.assertEqual("".join(text for text, _ in segments), "● --")

    def test_current_process_is_detected_as_not_elevated(self):
        detected = is_process_elevated(os.getpid())
        if detected is True:
            self.skipTest("test process is elevated")
        self.assertIs(detected, False)

    def test_missing_process_is_unknown(self):
        self.assertIsNone(is_process_elevated(None))
        self.assertIsNone(is_process_elevated(0))


class OverlayElevationTests(unittest.TestCase):
    def segments(self, elevated):
        overlay = Overlay()
        return overlay._segments(
            {
                "balance": 0,
                "spent": 0,
                "gained": 0,
                "fps": 60,
                "live": True,
                "partial": False,
                "elevated": elevated,
            }
        )

    def test_overlay_elevated_is_red_admin(self):
        segments = self.segments(True)
        self.assertEqual(segments[0], ("● ", (255 | (92 << 8) | (92 << 16)), "label"))
        self.assertEqual(segments[1][0], "ADMIN")

    def test_overlay_not_elevated_is_green_ok(self):
        segments = self.segments(False)
        self.assertEqual(segments[0], ("● ", (95 | (235 << 8) | (130 << 16)), "label"))
        self.assertEqual(segments[1][0], "OK")

    def test_overlay_unknown_is_dim(self):
        segments = self.segments(None)
        self.assertEqual(segments[0], ("● ", (150 | (150 << 8) | (150 << 16)), "label"))
        self.assertEqual(segments[1][0], "--")


class CashPerHourTests(unittest.TestCase):
    def test_positive_rate(self):
        self.assertEqual(cash_per_hour(500, 3600), 500)

    def test_negative_rate(self):
        self.assertEqual(cash_per_hour(-250, 1800), -500)

    def test_zero_rate(self):
        self.assertEqual(cash_per_hour(0, 3600), 0)

    def test_missing_balance(self):
        self.assertIsNone(cash_per_hour(None, 3600))

    def test_under_one_minute_is_unavailable(self):
        self.assertIsNone(cash_per_hour(500, 59))

    def test_no_elapsed_time_is_unavailable(self):
        self.assertIsNone(cash_per_hour(500, 0))

    def test_format_positive(self):
        self.assertEqual(cash_per_hour_str(1234), "+$1,234/h")

    def test_format_negative(self):
        self.assertEqual(cash_per_hour_str(-567), "-$567/h")

    def test_format_zero(self):
        self.assertEqual(cash_per_hour_str(0), "$0/h")

    def test_format_missing(self):
        self.assertEqual(cash_per_hour_str(None), "--")


class OverlayCashPerHourTests(unittest.TestCase):
    def segments(self, rate):
        overlay = Overlay()
        return overlay._segments(
            {
                "balance": 0,
                "spent": 0,
                "gained": 0,
                "fps": 60,
                "live": True,
                "partial": False,
                "elevated": False,
                "cash_per_hour": rate,
            }
        )

    def test_overlay_positive_rate(self):
        segments = self.segments(1234)
        self.assertIn(
            ("CASH/H", 150 | (150 << 8) | (150 << 16), "label"), segments
        )
        self.assertIn(
            ("+$1,234/h", 95 | (235 << 8) | (130 << 16), "value"), segments
        )

    def test_overlay_negative_rate(self):
        segments = self.segments(-567)
        self.assertIn(
            ("-$567/h", 255 | (92 << 8) | (92 << 16), "value"), segments
        )

    def test_overlay_zero_rate(self):
        segments = self.segments(0)
        self.assertIn(
            ("$0/h", 225 | (225 << 8) | (225 << 16), "value"), segments
        )

    def test_overlay_missing_rate(self):
        segments = self.segments(None)
        self.assertIn(
            ("--", 170 | (170 << 8) | (170 << 16), "value"), segments
        )


class GoldPanelTests(unittest.TestCase):
    def test_short_money(self):
        self.assertEqual(_short_money(166000), "$166k")
        self.assertEqual(_short_money(2798240), "$2.80M")
        self.assertEqual(_short_money(None), "--")

    def test_ordinal(self):
        self.assertEqual(_ordinal(42), "42nd")
        self.assertEqual(_ordinal(1), "1st")
        self.assertEqual(_ordinal(13), "13th")

    def test_log_points_cheapest_is_lowest(self):
        points, lo, hi = _log_points(
            [1000, 2000], 0, 0, 100, 100, floor=1000, ceiling=2000
        )
        self.assertGreater(points[0][1], points[1][1])
        self.assertLess(lo, 1000)
        self.assertGreater(hi, 2000)

    def test_log_points_single_value_is_centred(self):
        points, _lo, _hi = _log_points([1000], 0, 0, 100, 100)
        self.assertEqual(points[0][0], 50)

    def test_month_splits_mark_each_new_month(self):
        labels = [
            "2026-08-30", "2026-08-31", "2026-09-01",
            "2026-09-02", "2026-10-01",
        ]
        splits = _month_splits(labels)
        self.assertEqual([name for _, name in splits], ["Sep", "Oct"])
        self.assertAlmostEqual(splits[0][0], 0.5)
        self.assertAlmostEqual(splits[1][0], 1.0)

    def test_linear_points_include_zero_and_map_time(self):
        points, lo, hi = _linear_points(
            [(0, 0), (60, 12000)], 0, 0, 100, 100, x_max=120,
        )
        self.assertLess(points[1][1], points[0][1])
        self.assertEqual(points[1][0], 50)
        self.assertLess(lo, 0)
        self.assertGreater(hi, 12000)

    def test_clock_and_signed_money(self):
        self.assertEqual(_clock(0), "0:00")
        self.assertEqual(_clock(65), "1:05")
        self.assertEqual(_signed_short(12000), "+$12k")
        self.assertEqual(_signed_short(-12000), "-$12k")
        self.assertEqual(_signed_short(0), "$0")

    def test_panel_follows_f8_hold(self):
        overlay = Overlay()
        self.assertFalse(overlay._panel_open())
        overlay._foreground_is_game = lambda: True
        overlay._f8_down = lambda: True
        overlay._sync_panel()
        self.assertTrue(overlay._panel_open())
        overlay._f8_down = lambda: False
        overlay._sync_panel()
        self.assertFalse(overlay._panel_open())
        overlay._f8_down = lambda: True
        overlay._foreground_is_game = lambda: False
        overlay._sync_panel()
        self.assertFalse(overlay._panel_open())


class OverlayGoldTests(unittest.TestCase):
    def segments(self, gold):
        overlay = Overlay()
        return overlay._segments(
            {
                "balance": 0,
                "spent": 0,
                "gained": 0,
                "fps": 60,
                "live": True,
                "partial": False,
                "elevated": False,
                "cash_per_hour": 0,
                "gold": gold,
            }
        )

    def test_gold_price_and_stars_follow_fps(self):
        segments = self.segments({"price": 621425, "stars": 0, "zone": "fair"})
        texts = [text for text, _, _ in segments]
        self.assertIn("GOLD", texts)
        self.assertIn("$621,425", texts)
        self.assertNotIn("☆☆☆", texts)
        self.assertFalse(any(kind == "symbol" for _, _, kind in segments))
        self.assertGreater(texts.index("GOLD"), texts.index("FPS"))
        self.assertIn(
            ("$621,425", 244 | (201 << 8) | (93 << 16), "value"), segments
        )

    def test_gold_two_stars_shows_only_earned(self):
        segments = self.segments({"price": 230000, "stars": 2, "zone": "buy"})
        self.assertIn(
            ("★★", 244 | (201 << 8) | (93 << 16), "symbol"), segments
        )
        self.assertNotIn("★★★", [text for text, _, _ in segments])

    def test_gold_buy_zone_is_green(self):
        segments = self.segments({"price": 250000, "stars": 3, "zone": "buy"})
        self.assertIn(
            ("$250,000", 95 | (235 << 8) | (130 << 16), "value"), segments
        )
        self.assertIn(
            ("★★★", 244 | (201 << 8) | (93 << 16), "symbol"), segments
        )

    def test_gold_missing_shows_dashes(self):
        segments = self.segments(None)
        texts = [text for text, _, _ in segments]
        self.assertIn("--", texts)
        self.assertFalse(any(kind == "symbol" for _, _, kind in segments))
        self.assertGreater(texts.index("GOLD"), texts.index("FPS"))


if __name__ == "__main__":
    unittest.main()
