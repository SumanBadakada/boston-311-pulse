"""Resident flow: grouping, photo GPS, matching, overdue detection, escalation and report text. Offline."""
import io
import unittest
import urllib.parse

import pandas as pd
from PIL import Image

from pulse.issues import (case_details, draft_escalation, find_matches, group_of, photo_location,
                          prepare_report)

TODAY = pd.Timestamp("2026-10-14")


def jpeg(lat=None, lon=None) -> bytes:
    img, ex = Image.new("RGB", (32, 32), "gray"), Image.Exif()
    if lat is not None:
        def dms(x):
            x = abs(x); d = int(x); m = int((x - d) * 60)
            return (d, m, round(((x - d) * 60 - m) * 60, 4))
        ex[0x8825] = {1: "N", 2: dms(lat), 3: "W", 4: dms(lon)}
    b = io.BytesIO()
    img.save(b, "JPEG", exif=ex)
    return b.getvalue()


def case(cid, rtype, lat, lon, status="Open", opened="2026-09-01", target="2026-09-15", on_time=0,
         hours=None, district=2, address="12 Main St"):
    return dict(case_id=cid, open_date=pd.Timestamp(opened), hour=9, request_type=rtype, department="PWDx",
                neighborhood="South End", case_status=status, on_time_flag=on_time, resolution_hours=hours,
                latitude=lat, longitude=lon, target_date=pd.Timestamp(target) if target else pd.NaT,
                council_district=district, address=address)


class GroupingTests(unittest.TestCase):
    def test_both_systems_map_to_the_same_group(self):
        self.assertEqual(group_of("Request for Pothole Repair"), group_of("Pothole"))
        self.assertEqual(group_of("Street Light Outages"), "Streetlight out")

    def test_no_false_substring_matches(self):
        self.assertEqual(group_of("Requests for Street Cleaning"), "Overflowing or missed trash")  # not "tree"
        self.assertEqual(group_of("Illegally Blocked Sidewalk"), "Illegal parking / blocked sidewalk")
        self.assertEqual(group_of("Unsatisfactory Living Conditions"), "Something else")


class PhotoTests(unittest.TestCase):
    def test_gps_read_from_exif(self):
        lat, lon = photo_location(jpeg(42.3467, -71.0723))
        self.assertAlmostEqual(lat, 42.3467, places=4)
        self.assertAlmostEqual(lon, -71.0723, places=4)

    def test_no_gps_or_outside_boston(self):
        self.assertIsNone(photo_location(jpeg()))
        self.assertIsNone(photo_location(jpeg(40.7128, -74.0060)))  # New York
        self.assertIsNone(photo_location(b"not an image"))


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame([
            case("1", "Request for Pothole Repair", 42.34670, -71.07230),            # ~0 m
            case("BCS-2", "Pothole", 42.34750, -71.07230),                            # ~90 m
            case("3", "Request for Pothole Repair", 42.35200, -71.07230),            # ~590 m
            case("4", "Graffiti Removal", 42.34671, -71.07231),                      # wrong type
            case("5", "Pothole", 42.34672, -71.07232, status="Closed", hours=24 * 3,
                 opened="2026-10-08"),                                                # just fixed
            case("6", "Pothole", 42.34673, -71.07233, status="Closed", hours=24,
                 opened="2026-08-01"),                                                # closed long ago
        ])

    def test_same_group_nearby_open_or_just_closed(self):
        m = find_matches(self.df, 42.34670, -71.07230, "Pothole or road damage", today=TODAY)
        self.assertEqual(list(m.case_id), ["1", "5", "BCS-2"])

    def test_wider_radius_finds_more(self):
        m = find_matches(self.df, 42.34670, -71.07230, "Pothole or road damage", radius_m=700, today=TODAY)
        self.assertIn("3", list(m.case_id))

    def test_something_else_matches_any_type(self):
        m = find_matches(self.df, 42.34670, -71.07230, "Something else", radius_m=20, today=TODAY)
        self.assertIn("4", list(m.case_id))


class CaseTests(unittest.TestCase):
    def test_overdue_case_details_and_email(self):
        row = pd.Series(case("101006726212", "Pothole", 42.3, -71.0)).copy()
        row["distance_m"] = 12
        c = case_details(row, TODAY)
        self.assertTrue(c["overdue"])
        self.assertEqual(c["days_past_target"], 29)
        self.assertEqual(c["councilor"], "Edward M. Flynn")
        self.assertEqual(c["link"], "https://311.boston.gov/tickets/101006726212")
        e = draft_escalation(c, "It is getting deeper.", "Sam")
        self.assertEqual((e["to"], e["cc"]), ("311@boston.gov", "city.council@boston.gov"))
        self.assertIn("101006726212", e["subject"])
        for s in ("29 days past", "It is getting deeper.", "Councilor Edward M. Flynn (District 2)", "Sam"):
            self.assertIn(s, e["body"])
        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(e["mailto"]).query)
        self.assertEqual(parsed["body"][0], e["body"])

    def test_case_within_target_is_not_overdue(self):
        c = case_details(pd.Series(case("BCS-9", "Pothole", 42.3, -71.0, target="2026-10-30", on_time=1)), TODAY)
        self.assertFalse(c["overdue"])
        self.assertIsNone(c["link"])  # new-system cases have no public page

    def test_prepared_report(self):
        r = prepare_report("Graffiti", "Tag on the mailbox", 42.3467, -71.0723, "12 Main St", "This week", "corner")
        for s in ("Issue: Graffiti", "Location: 12 Main St", "First noticed: This week", "More detail: corner"):
            self.assertIn(s, r["text"])
        self.assertEqual(r["file_at"], "https://311.boston.gov/")


if __name__ == "__main__":
    unittest.main()
