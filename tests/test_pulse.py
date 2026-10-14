"""Offline tests on small synthetic data with known answers.

    python -m unittest discover -s tests -t .
"""
import unittest

import pandas as pd

from pulse.engine import behind_expected, build_brief, data_watch, hotspots, open_at, overdue, spikes
from pulse.report import render
from pulse.writer import template_brief, ungrounded_numbers

ASOF = pd.Timestamp("2025-03-31")


def rows(n, date, rtype="Pothole", hood="Dorchester", dept="PWDx", status="Closed", on_time=1,
         hours=10.0, expected=0.9, lat=42.30, lon=-71.06):
    return [dict(open_date=pd.Timestamp(date), hour=9, request_type=rtype, department=dept, neighborhood=hood,
                 case_status=status, resolved=int(status == "Closed"), admin_close=0, on_time_flag=on_time,
                 resolution_hours=hours if status == "Closed" else None, expected_on_time=expected,
                 latitude=lat, longitude=lon) for _ in range(n)]


def baseline():
    """Steady 2 potholes a day in Dorchester and Roxbury for 5 weeks."""
    out = []
    for d in pd.date_range(ASOF - pd.Timedelta(days=34), ASOF):
        out += rows(2, d, hood="Dorchester") + rows(2, d, hood="Roxbury", lat=42.32)
    return out


class EngineTests(unittest.TestCase):
    def test_surge_found_and_steady_series_ignored(self):
        df = pd.DataFrame(baseline() + rows(40, ASOF - pd.Timedelta(days=2), rtype="Unshoveled Sidewalk",
                                            hood="Dorchester") + rows(5, ASOF - pd.Timedelta(days=20),
                                                                      rtype="Unshoveled Sidewalk"))
        found = spikes(df, ASOF)
        self.assertEqual([(s["neighborhood"], s["request_type"]) for s in found],
                         [("Dorchester", "Unshoveled Sidewalk")])

    def test_brand_new_type_is_not_called_a_surge(self):
        df = pd.DataFrame(baseline() + rows(40, ASOF - pd.Timedelta(days=1), rtype="New Code"))
        self.assertEqual(spikes(df, ASOF), [])

    def test_vanished_type_goes_to_data_watch(self):
        old = []
        for d in pd.date_range(ASOF - pd.Timedelta(days=34), ASOF - pd.Timedelta(days=8)):
            old += rows(6, d, rtype="Moved To New System")
        df = pd.DataFrame(baseline() + old)
        self.assertEqual([x["request_type"] for x in data_watch(df, ASOF)], ["Moved To New System"])

    def test_case_closed_after_asof_counts_as_open_on_asof(self):
        df = pd.DataFrame(rows(1, ASOF - pd.Timedelta(days=5), hours=24 * 30)    # closed later
                          + rows(1, ASOF - pd.Timedelta(days=5), hours=2))       # closed same day
        self.assertEqual(len(open_at(df, ASOF)), 1)

    def test_overdue_ranks_recent_late_cases_and_counts_old_ones(self):
        df = pd.DataFrame(rows(5, ASOF - pd.Timedelta(days=10), status="Open", on_time=0)
                          + rows(9, ASOF - pd.Timedelta(days=200), status="Open", on_time=0)
                          + rows(4, ASOF - pd.Timedelta(days=10), status="Open", on_time=1))
        ranked, total = overdue(df, ASOF)
        self.assertEqual(ranked[0]["open_overdue"], 5)
        self.assertEqual(ranked[0]["department"], "Public Works")
        self.assertEqual(total, 14)

    def test_behind_expected_uses_mix_adjusted_gap(self):
        d = ASOF - pd.Timedelta(days=3)
        df = pd.DataFrame(rows(70, d, hood="Roxbury", on_time=1, expected=0.9)
                          + rows(30, d, hood="Roxbury", on_time=0, expected=0.9)       # 70% vs 90%
                          + rows(100, d, hood="Dorchester", on_time=1, expected=0.9))  # ahead
        out = behind_expected(df, ASOF)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["neighborhood"], out[0]["gap_pts"]), ("Roxbury", -20.0))

    def test_hotspot_groups_nearby_reports(self):
        d = ASOF - pd.Timedelta(days=4)
        df = pd.DataFrame(rows(5, d, lat=42.30001) + rows(5, d, lat=42.30020) + rows(2, d, lat=42.35))
        [h] = hotspots(df, ASOF)
        self.assertEqual(h["reports"], 10)


class WriterTests(unittest.TestCase):
    def setUp(self):
        df = pd.DataFrame(baseline() + rows(40, ASOF - pd.Timedelta(days=2), rtype="Unshoveled Sidewalk")
                          + rows(5, ASOF - pd.Timedelta(days=20), rtype="Unshoveled Sidewalk"))
        self.df, self.b = df, build_brief(df, ASOF)

    def test_template_brief_is_fully_grounded(self):
        self.assertEqual(ungrounded_numbers(template_brief(self.b), self.b), [])

    def test_invented_numbers_are_caught(self):
        self.assertEqual(ungrounded_numbers("Requests rose 37% to 9,999.", self.b), [37.0, 9999.0])

    def test_report_renders(self):
        page = render(self.b, template_brief(self.b), "template", self.df)
        self.assertIn("Unshoveled Sidewalk", page)
        self.assertIn("<svg", page)


if __name__ == "__main__":
    unittest.main()
