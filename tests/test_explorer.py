"""Regression checks for case filters and the evidence behind brief counts."""
import unittest

import pandas as pd

from pulse.engine import brief_evidence, build_brief
from pulse.explorer import nearby, search
from tests.test_pulse import ASOF, baseline, rows


class ExplorerTests(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame(
            rows(1, ASOF, status="Open", on_time=0) +
            rows(1, ASOF, status="Open", on_time=0, rtype="Request for Pothole Repair") +
            rows(1, ASOF, status="Closed", rtype="Graffiti")
        ).assign(case_id=["BCS-12", "101013", "101014"], address=["1 Main St", "2 Main St", "3 Main St"],
                 target_date=["2025-03-30", "2025-04-03", None])
        self.cutoff = ASOF + pd.Timedelta(days=1)

    def test_filters_intersect_and_use_explicit_targets(self):
        found = search(self.df, self.cutoff, case="bcs", address="main st", types=["Pothole"],
                       departments=["PWDx"], neighborhoods=["Dorchester"], statuses=["Open"],
                       overdue="Overdue", dates=(ASOF, ASOF))
        self.assertEqual(found.case_id.tolist(), ["BCS-12"])
        self.assertEqual(search(self.df, self.cutoff, overdue="Overdue").case_id.tolist(), ["BCS-12"])

    def test_nearby_labels_duplicate_and_other_issue(self):
        found = nearby(self.df, self.df.iloc[0], self.cutoff).set_index("case_id")
        self.assertEqual(found.match.to_dict(), {"101013": "Likely duplicate", "101014": "Nearby"})

    def test_evidence_matches_period_and_overdue_counts(self):
        df = pd.concat([pd.DataFrame(baseline()), self.df], ignore_index=True)
        for days in (1, 7):
            brief = build_brief(df, ASOF, days=days)
            evidence, _ = brief_evidence(df, brief, "Request volume", {})
            self.assertEqual(evidence.period.eq("Observed").sum(), brief.requests_this_window)
            self.assertEqual(round(evidence.period.eq("Baseline").sum() / (28 / days)),
                             brief.requests_weekly_baseline)
            late, _ = brief_evidence(df, brief, "Falling behind", {})
            self.assertEqual(late.case_id.tolist(), ["BCS-12"])
            self.assertEqual(len(late), brief.overdue_total)
