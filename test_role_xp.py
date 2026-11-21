import json
import os
import tempfile
import unittest

from role_xp import read_role_xp


class RoleXpTests(unittest.TestCase):
    def write_save(self, payload):
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".sav")
        handle.close()
        self.addCleanup(lambda: os.path.exists(handle.name) and os.remove(handle.name))
        with open(handle.name, "w", encoding="utf-16-le") as fh:
            json.dump(payload, fh)
        return handle.name

    def test_reads_role_totals_from_wrapped_save(self):
        path = self.write_save(
            {
                "slot": {
                    '(TagName="Meta.Role.Wardog")': 101,
                    '(TagName="Meta.Role.Driver")': 19,
                    '(TagName="Meta.Role.Support")': 20,
                    '(TagName="Meta.Role.Medic")': 12,
                }
            }
        )
        self.assertEqual(
            read_role_xp(path),
            {"Wardog": 101, "Driver": 19, "Support": 20, "Medic": 12},
        )

    def test_missing_file_is_empty(self):
        self.assertEqual(read_role_xp("missing-file.sav"), {})

    def test_invalid_json_is_empty(self):
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".sav")
        handle.close()
        self.addCleanup(lambda: os.path.exists(handle.name) and os.remove(handle.name))
        with open(handle.name, "w", encoding="utf-16-le") as fh:
            fh.write("{not json")
        self.assertEqual(read_role_xp(handle.name), {})

    def test_ignores_non_role_keys_and_coerces_numbers(self):
        path = self.write_save(
            {
                "slot": {
                    "Other": 5,
                    '(TagName="Meta.Role.Recon")': "7",
                    '(TagName="Meta.Role.Pilot")': "not-a-number",
                }
            }
        )
        self.assertEqual(read_role_xp(path), {"Recon": 7})


if __name__ == "__main__":
    unittest.main()
