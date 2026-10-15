"""Track my case: verdicts, cases like mine, neighbors, closing notes, reminders, and the email evidence."""
import unittest

import pandas as pd

from pulse import tracker
from pulse.issues import draft_escalation
from pulse.live import closure_note

FIX = {"p50": 48.0, "p80": 120.0}


class VerdictTests(unittest.TestCase):
    def test_levels_follow_typical_and_80pct_times(self):
        self.assertEqual(tracker.verdict(1, FIX, True, False)[0], "on_track")
        self.assertEqual(tracker.verdict(4, FIX, True, False)[0], "long")
        self.assertEqual(tracker.verdict(9, FIX, True, False)[0], "slow")
        self.assertEqual(tracker.verdict(9, FIX, False, False)[0], "closed")

    def test_without_model_uses_target_date(self):
        self.assertEqual(tracker.verdict(9, None, True, True)[0], "slow")
        self.assertEqual(tracker.verdict(9, None, True, False)[0], "on_track")

    def test_overdue_but_typical_gets_gentle_follow_up(self):
        msg = tracker.next_step("long", {"overdue": True}, FIX, pd.Timestamp("2026-10-20"))
        self.assertIn("past the City's own target date", msg)


def frame(n_open, n_closed, lat=42.30, lon=-71.06, day="2026-10-01"):
    base = dict(request_type="Request for Pothole Repair", neighborhood="Dorchester",
                open_date=pd.Timestamp(day), latitude=lat, longitude=lon)
    rows = [{**base, "case_id": f"o{i}", "case_status": "Open"} for i in range(n_open)]
    rows += [{**base, "case_id": f"c{i}", "case_status": "Closed"} for i in range(n_closed)]
    # one older closed case marks the start of the window where closed cases are kept
    rows.append({**base, "case_id": "early", "case_status": "Closed", "open_date": pd.Timestamp("2026-09-01")})
    return pd.DataFrame(rows)


class CohortTests(unittest.TestCase):
    def test_share_fixed_among_similar_cases(self):
        df = frame(5, 15)
        k = tracker.cohort(df, df.iloc[0], pd.Timestamp("2026-10-14"))
        self.assertEqual((k["n"], k["fixed_pct"]), (19, 79))

    def test_too_few_cases_says_nothing(self):
        df = frame(2, 3)
        self.assertIsNone(tracker.cohort(df, df.iloc[0], pd.Timestamp("2026-10-14")))

    def test_case_older_than_closed_window_says_nothing(self):
        df = frame(5, 15)
        old = df.iloc[0].copy()
        old["open_date"] = pd.Timestamp("2026-06-01")
        self.assertIsNone(tracker.cohort(df, old, pd.Timestamp("2026-10-14")))


class ReminderTests(unittest.TestCase):
    def test_calendar_file_is_an_all_day_event(self):
        c = {"case_id": "101", "request_type": "Pothole", "address": "1 Main St", "neighborhood": "Dorchester"}
        cal = tracker.ics(c, pd.Timestamp("2026-10-20"), "https://311.boston.gov/tickets/101")
        self.assertIn("DTSTART;VALUE=DATE:20261020", cal)
        self.assertIn("SUMMARY:Check on 311 case 101", cal)
        self.assertTrue(cal.startswith("BEGIN:VCALENDAR") and "END:VCALENDAR" in cal)


class ClosureNoteTests(unittest.TestCase):
    def test_strips_legacy_prefix(self):
        self.assertEqual(closure_note("Case Closed. Closed date : 2026-10-01 10:00:00 Resolved"), "Resolved")

    def test_joins_reason_and_comment(self):
        self.assertEqual(closure_note("Resolved", "Property has been cited"), "Resolved: Property has been cited")

    def test_empty_is_none(self):
        self.assertEqual(closure_note(None, None), "")


class EmailNearbyTests(unittest.TestCase):
    def test_email_mentions_nearby_reports(self):
        c = {"case_id": "1", "request_type": "Pothole", "address": "1 Main St", "neighborhood": "Dorchester",
             "opened": "Sep 01, 2026", "department": "PWDx", "days_open": 43, "target": "Sep 15, 2026",
             "days_past_target": 29, "link": None, "councilor": None, "council_district": None,
             "nearby_reports": 4}
        self.assertIn("4 other open reports of the same issue nearby", draft_escalation(c)["body"])


if __name__ == "__main__":
    unittest.main()
