"""Predictions: quantiles, fallback to broader comparisons, outlook, backlog, and the email evidence line."""
import unittest

import pandas as pd

from pulse import predict
from pulse.engine import backlog
from pulse.issues import draft_escalation


def resolved(n, rtype, hood, month, hours, on_time=1, year=2024):
    return [dict(open_date=pd.Timestamp(year, month, 5), request_type=rtype, neighborhood=hood, resolved=1,
                 resolution_hours=h, on_time_flag=on_time) for h in ([hours] * n if not isinstance(hours, list) else hours)]


class FixTimeTests(unittest.TestCase):
    def setUp(self):
        rows = (resolved(40, "Request for Pothole Repair", "Dorchester", 1, list(range(1, 41)))     # winter, local
                + resolved(40, "Request for Pothole Repair", "Roxbury", 7, 100.0, on_time=0)        # summer
                + resolved(5, "Graffiti Removal", "South End", 4, 300.0))                            # too few
        self.tables = predict.fit(pd.DataFrame(rows))

    def test_narrowest_group_with_enough_cases(self):
        p = predict.lookup(self.tables, "Request for Pothole Repair", "Dorchester", 2)
        self.assertEqual(p["basis"], "this request type, in this neighborhood, in this season")
        self.assertEqual((p["n"], p["p50"]), (40, 20.5))

    def test_falls_back_when_local_data_is_thin(self):
        p = predict.lookup(self.tables, "Request for Pothole Repair", "Hyde Park", 1)
        self.assertEqual(p["basis"], "this request type citywide, in this season")

    def test_new_system_name_uses_issue_group(self):
        p = predict.lookup(self.tables, "Pothole", "Dorchester", 1)
        self.assertEqual(p["basis"], "similar issues in this neighborhood, in this season")

    def test_too_few_cases_never_used(self):
        p = predict.lookup(self.tables, "Graffiti Removal", "South End", 4)
        self.assertEqual(p["basis"], "all Boston 311 requests")

    def test_seasons(self):
        self.assertEqual([predict.season_of(m) for m in (1, 4, 7, 10, 12)],
                         ["Winter", "Spring", "Summer", "Fall", "Winter"])

    def test_evaluation_reports_honest_range(self):
        df = pd.DataFrame(resolved(60, "Request for Pothole Repair", "Dorchester", 1, list(range(1, 61)))
                          + resolved(60, "Request for Pothole Repair", "Dorchester", 1, list(range(1, 61)), year=2025))
        ev = predict.evaluate(df[df.open_date.dt.year == 2024], df[df.open_date.dt.year == 2025])
        self.assertAlmostEqual(ev["share_within_80pct_range"], 0.8, delta=0.02)


class OutlookAndBacklogTests(unittest.TestCase):
    def test_outlook_ranks_rising_issues(self):
        saved = predict.model
        weeks = {str(w): 100.0 for w in range(1, 53)}
        rising = {**weeks, **{str(w): 300.0 for w in range(5, 9)}}
        predict.model = lambda: {"outlook": {"Pothole or road damage": rising, "Graffiti": weeks}}
        try:
            rows = predict.outlook(pd.Timestamp("2026-01-26"))  # ISO week 5: next four weeks are 6-9
        finally:
            predict.model = saved
        self.assertEqual(rows[0]["issue"], "Pothole or road damage")
        self.assertGreater(rows[0]["change_pct"], 50)

    def test_backlog_counts_open_and_late_by_department(self):
        asof = pd.Timestamp("2026-10-14")
        base = dict(hour=9, request_type="Pothole", neighborhood="Dorchester", resolution_hours=None,
                    on_time_flag=0, case_status="Open", open_date=pd.Timestamp("2026-09-01"))
        df = pd.DataFrame([{**base, "department": "PWDx", "target_date": pd.Timestamp("2026-09-10")},
                           {**base, "department": "PWDx", "target_date": pd.Timestamp("2026-11-01")},
                           {**base, "department": "BTDT", "target_date": pd.Timestamp("2026-09-10")}])
        rows = {r["department"]: r for r in backlog(df, asof)}
        self.assertEqual((rows["Public Works"]["open_cases"], rows["Public Works"]["past_target"]), (2, 1))
        self.assertEqual(rows["Transportation"]["past_target_pct"], 100)


class EmailEvidenceTests(unittest.TestCase):
    def test_escalation_cites_typical_fix_time(self):
        c = {"case_id": "1", "request_type": "Pothole", "address": "1 Main St", "neighborhood": "Dorchester",
             "opened": "Sep 01, 2026", "department": "PWDx", "days_open": 43, "target": "Sep 15, 2026",
             "days_past_target": 29, "link": None, "councilor": None, "council_district": None,
             "fix": {"range": "5 days", "n": 442}}
        body = draft_escalation(c)["body"]
        self.assertIn("usually resolved within 5 days (8 in 10, based on 442 past cases)", body)
        self.assertIn("open 43 days", body)


if __name__ == "__main__":
    unittest.main()
