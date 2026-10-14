"""Mapping tests on real sample rows recorded from both live datasets (no network)."""
import json
import unittest
from pathlib import Path

import pandas as pd

from pulse.live import LEGACY_COLS, NEW_COLS, _dept_from_new, to_clean

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "live_schema_sample.json").read_text())


def raw(name, cols):
    return pd.DataFrame(SAMPLE[name]["records"])[cols]


class LiveMappingTests(unittest.TestCase):
    def test_legacy_rows_map_to_clean_schema(self):
        df = to_clean(raw("legacy_2025", LEGACY_COLS), "legacy")
        r = df.iloc[0]
        self.assertEqual(r.request_type, "Requests for Street Cleaning")
        self.assertEqual(r.department, "PWDx")
        self.assertEqual((r.resolved, r.on_time_flag, r.hour), (1, 1, 9))
        self.assertAlmostEqual(r.resolution_hours, 0.77, places=2)
        self.assertEqual(str(r.open_date.date()), "2025-01-02")

    def test_open_legacy_case_has_no_resolution_time(self):
        df = to_clean(raw("legacy_2026", LEGACY_COLS), "legacy")
        open_rows = df[df.case_status == "Open"]
        self.assertTrue(len(open_rows) > 0)
        self.assertTrue(open_rows.resolution_hours.isna().all())
        self.assertTrue((open_rows.resolved == 0).all())

    def test_new_system_rows_convert_utc_and_department(self):
        df = to_clean(raw("new_system", NEW_COLS), "new")
        r = df.iloc[0]
        self.assertEqual(r.case_id, "BCS-00245057")
        self.assertEqual(r.department, "PWDx")          # "Public Works Department (PWD)"
        self.assertEqual(r.hour, 2)                      # 06:30 UTC = 02:30 Boston (EDT)
        self.assertAlmostEqual(r.resolution_hours, 0.8, places=1)
        self.assertEqual(r.request_type, "Litter & Debris")

    def test_department_codes(self):
        self.assertEqual(_dept_from_new("Boston Transportation Department (BTD)"), "BTDT")
        self.assertEqual(_dept_from_new("Some New Office"), "Some New Office")


if __name__ == "__main__":
    unittest.main()
