import unittest

from names import display_faction, display_map_name


class MapNameTests(unittest.TestCase):
    def test_all_map_codes(self):
        self.assertEqual(display_map_name("Europe"), "Ozeti")
        self.assertEqual(display_map_name("Kavkazi"), "Bakurani")
        self.assertEqual(display_map_name("NorthAmerica"), "Zestafona")
        self.assertEqual(display_map_name("Russia"), "Sabatulo")

    def test_case_and_whitespace(self):
        self.assertEqual(display_map_name("  russia "), "Sabatulo")
        self.assertEqual(display_map_name("northamerica"), "Zestafona")

    def test_display_names_are_idempotent(self):
        for name in ("Ozeti", "Bakurani", "Zestafona", "Sabatulo"):
            self.assertEqual(display_map_name(name), name)

    def test_empty_and_unknown(self):
        self.assertIsNone(display_map_name(None))
        self.assertIsNone(display_map_name(""))
        self.assertIsNone(display_map_name("frontend"))
        self.assertEqual(display_map_name("unknown"), "Unknown")
        self.assertEqual(display_map_name("?"), "Unknown")


class FactionNameTests(unittest.TestCase):
    def test_all_faction_codes(self):
        self.assertEqual(display_faction("alpha"), "Lonestar")
        self.assertEqual(display_faction("bravo"), "Valkyra")
        self.assertEqual(display_faction("charlie"), "Manticore")

    def test_case_and_whitespace(self):
        self.assertEqual(display_faction(" CHARLIE "), "Manticore")

    def test_display_names_are_idempotent(self):
        for name in ("Lonestar", "Valkyra", "Manticore"):
            self.assertEqual(display_faction(name), name)

    def test_missing_and_unknown(self):
        self.assertEqual(display_faction(None), "Unknown")
        self.assertEqual(display_faction(""), "Unknown")
        self.assertEqual(display_faction("?"), "Unknown")
        self.assertEqual(display_faction("unknown"), "Unknown")


if __name__ == "__main__":
    unittest.main()
