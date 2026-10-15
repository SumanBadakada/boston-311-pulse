"""Follow-up questions about request types the brief didn't flag (the 'needle pickups' bug)."""
import unittest

import pandas as pd

from pulse.engine import build_brief, question_facts
from pulse.writer import answer_question, ungrounded_numbers

ASOF = pd.Timestamp("2026-10-13")


def week(rtype, hood, n, day):
    return [dict(case_id=f"{rtype}{hood}{day}{i}", open_date=pd.Timestamp(day), hour=9, request_type=rtype,
                 neighborhood=hood, department="PWDx", case_status="Open", resolution_hours=None,
                 resolved=0, on_time_flag=0, latitude=42.3, longitude=-71.06,
                 target_date=pd.Timestamp("2026-11-01"), expected_on_time=0.8) for i in range(n)]


class QuestionFactsTests(unittest.TestCase):
    def setUp(self):
        rows = (week("Needle Pickup", "South End", 6, "2026-10-10") + week("Needle Pickup", "Roxbury", 2, "2026-10-11")
                + week("Needle Pickup", "South End", 8, "2026-09-20")
                + week("Pothole", "West Roxbury", 3, "2026-10-12") + week("Pothole", "Roxbury", 4, "2026-10-12")
                + week("Missed Waste Pick-up", "Dorchester", 5, "2026-10-09"))
        self.df = pd.DataFrame(rows)

    def test_needle_question_finds_needle_pickups(self):
        f = question_facts(self.df, ASOF, "Are there any needle pickups requested this week?")
        [m] = f["matched_request_types"]
        self.assertEqual((m["label"], m["this_week"], m["still_open_of_this_weeks"]), ("Needle Pickup", 8, 8))
        self.assertEqual(m["by_neighborhood"], {"South End": 6, "Roxbury": 2})
        self.assertEqual(m["usual_per_week"], 2)

    def test_synonyms_and_short_words(self):
        f = question_facts(self.df, ASOF, "any syringes? how busy was it")
        self.assertEqual([m["label"] for m in f["matched_request_types"]], ["Needle Pickup"])  # "was" is not "Waste"

    def test_full_neighborhood_name_wins(self):
        f = question_facts(self.df, ASOF, "Any potholes in West Roxbury?")
        self.assertEqual([(m["label"], m["this_week"]) for m in f["matched_request_types"]],
                         [("Pothole in West Roxbury", 3)])

    def test_answer_without_ai_uses_the_counts(self):
        b = build_brief(self.df, ASOF)
        f = question_facts(self.df, ASOF, "needle pickups this week?")
        import os
        os.environ["PULSE_PROVIDER"] = "none"
        try:
            text = answer_question("needle pickups this week?", b, f)
        finally:
            del os.environ["PULSE_PROVIDER"]
        self.assertIn("Needle Pickup: 8 requests", text)
        self.assertEqual(ungrounded_numbers(text, b, f), [])


if __name__ == "__main__":
    unittest.main()
