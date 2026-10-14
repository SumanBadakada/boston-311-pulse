"""Resident side: photo -> issue -> existing case, or a ready-to-file new report.

Flow
1. `classify_photo` asks Gemini what the photo shows, constrained to the issue
   groups below. Without a key the resident picks the group themselves.
2. `photo_location` reads GPS from the photo's EXIF; if there is none (most
   browser uploads strip it), `geocode` turns an address into coordinates.
3. `find_matches` looks for open or just-closed cases of the same kind nearby.
4. No match? `find_matches` again with a wider radius after the resident adds
   detail. Still nothing -> `prepare_report` writes a report to file on Boston's
   official 311 site. Nothing is ever filed in the resident's name automatically.
5. A matched case past its target date -> `draft_escalation` writes an email to
   311 and the district's city councilor, which the resident reviews and sends.
"""
from __future__ import annotations

import io
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass

import pandas as pd

# Plain-language issue groups, each matching request types from BOTH city systems
# (legacy names like "Request for Pothole Repair", new names like "Pothole").
ISSUE_GROUPS: dict[str, str] = {
    "Illegal parking / blocked sidewalk": r"parking enforcement|blocked sidewalk|double park",
    "Pothole or road damage": r"pothole|pavement|roadway repair|street repair|manhole|utility casting",
    "Broken sidewalk": r"sidewalk repair|broken sidewalk|\bcurb",
    "Unshoveled sidewalk / snow": r"unshoveled|\bsnow|\bice\b",
    "Streetlight out": r"street ?light|light outage",
    "Graffiti": r"graffiti",
    "Illegal dumping": r"dumping|illegal disposal|construction debris|bulk item",
    "Overflowing or missed trash": r"overflowing|missed (?:trash|waste)|\btrash|recycl|litter|debris|barrel|waste receptacle|street cleaning",
    "Needle / sharps": r"needle|sharps",
    "Dead animal": r"dead animal",
    "Rodents or pests": r"rodent|\brats?\b|pest infestation",
    "Abandoned vehicle or bike": r"abandoned (?:vehicle|bicycle|bike)",
    "Fallen or damaged tree": r"\btrees?\b|branch|stump|pruning",
    "Overgrown weeds": r"overgrown|weeds",
    "Damaged or missing sign": r"sign repair|knocked down sign|missing / knocked|\bsign\b",
    "Traffic signal problem": r"traffic signal",
    "Park or playground damage": r"\bparks?\b|playground|park equipment|ballfield",
    "Something else": r"$^",
}

TICKET_URL = "https://311.boston.gov/tickets/{}"
REPORT_URL = "https://311.boston.gov/"
CITY_311_EMAIL = "311@boston.gov"
CITY_COUNCIL_EMAIL = "city.council@boston.gov"  # individual councilor emails are not published
# Boston City Council, 2026-2027 term (boston.gov/departments/city-council, Oct 2026). Update after elections.
COUNCILORS = {1: "Gabriela Coletta Zapata", 2: "Edward M. Flynn", 3: "John FitzGerald", 4: "Brian Worrell",
              5: "Enrique J. Pepén", 6: "Benjamin J. Weber", 7: "Miniard Culpepper", 8: "Sharon Durkan",
              9: "Liz Breadon"}


def group_of(request_type: str) -> str:
    t = str(request_type).lower()
    for g, pat in ISSUE_GROUPS.items():
        if re.search(pat, t):
            return g
    return "Something else"


# ---------- photo understanding ----------

@dataclass
class PhotoRead:
    group: str
    description: str
    confidence: float
    has_people_or_plates: bool
    source: str  # "gemini" or "manual"


VISION_PROMPT = """You help Boston residents report public problems to 311.
Look at the photo and pick the ONE issue group that best fits, from this exact list:
{groups}
Reply with JSON only: {{"group": <one of the list>, "description": <one factual sentence a city crew
could act on, e.g. size, position, hazard>, "confidence": <0-1>, "has_people_or_plates": <true if
identifiable faces or licence plates are visible>}}. If the photo does not show a public-space problem,
use "Something else" with low confidence."""


def classify_photo(image_bytes: bytes, mime: str = "image/jpeg") -> PhotoRead | None:
    """Gemini vision read of the photo. None when no key or the call fails (the resident picks instead)."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
        r = client.models.generate_content(
            model=os.environ.get("PULSE_VISION_MODEL", "gemini-3.1-flash-lite"),
            contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime),
                      VISION_PROMPT.format(groups="\n".join(f"- {g}" for g in ISSUE_GROUPS))],
            config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json"))
        d = json.loads(re.search(r"\{.*\}", r.text or "", re.S).group(0))
        group = d.get("group") if d.get("group") in ISSUE_GROUPS else "Something else"
        return PhotoRead(group, str(d.get("description", "")).strip(), float(d.get("confidence", 0.5)),
                         bool(d.get("has_people_or_plates", False)), "gemini")
    except Exception:
        return None


def _dms(value, ref) -> float:
    d, m, s = (float(x) for x in value)
    deg = d + m / 60 + s / 3600
    return -deg if ref in ("S", "W") else deg


def photo_location(image_bytes: bytes) -> tuple[float, float] | None:
    """(lat, lon) from the photo's EXIF GPS tags, if present and inside Boston."""
    try:
        from PIL import Image
        exif = Image.open(io.BytesIO(image_bytes)).getexif()
        gps = exif.get_ifd(0x8825)  # GPSInfo
        lat = _dms(gps[2], gps[1])
        lon = _dms(gps[4], gps[3])
    except Exception:
        return None
    return (lat, lon) if in_boston(lat, lon) else None


def in_boston(lat: float, lon: float) -> bool:
    return 42.22 <= lat <= 42.41 and -71.20 <= lon <= -70.98


def geocode(address: str) -> tuple[float, float, str] | None:
    """Address -> (lat, lon, matched address) via OpenStreetMap Nominatim, limited to Boston."""
    q = address if "boston" in address.lower() else f"{address}, Boston, MA"
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "limit": 1, "viewbox": "-71.20,42.41,-70.98,42.22", "bounded": 1})
    req = urllib.request.Request(url, headers={"User-Agent": "boston-311-pulse (github.com/SumanBadakada/boston-311-pulse)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            hits = json.load(r)
    except Exception:
        return None
    if not hits:
        return None
    lat, lon = float(hits[0]["lat"]), float(hits[0]["lon"])
    return (lat, lon, hits[0].get("display_name", address)) if in_boston(lat, lon) else None


# ---------- matching ----------

def distance_m(lat1, lon1, lat2, lon2):
    """Haversine distance in metres (works on scalars or pandas Series)."""
    import numpy as np
    lat1r, lon1r, lat2r, lon2r = (np.radians(np.asarray(x, dtype=float)) for x in (lat1, lon1, lat2, lon2))
    a = np.sin((lat2r - lat1r) / 2) ** 2 + np.cos(lat1r) * np.cos(lat2r) * np.sin((lon2r - lon1r) / 2) ** 2
    return 6_371_000 * 2 * np.arcsin(np.sqrt(a))


def find_matches(df: pd.DataFrame, lat: float, lon: float, group: str, radius_m: float = 150,
                 recent_closed_days: int = 7, today: pd.Timestamp | None = None, top: int = 5) -> pd.DataFrame:
    """Open cases (and ones closed in the last week) of the same issue group near a point, nearest first.

    Closed-recently cases are included so a resident learns "this was just fixed" rather than
    filing a duplicate. With group "Something else", every type nearby is a candidate.
    """
    today = today or pd.Timestamp.now().normalize()
    d = df.dropna(subset=["latitude", "longitude"])
    # cheap bounding box first (~0.01 deg is ~1 km), then exact distance
    box = radius_m / 80_000
    d = d[(d.latitude.between(lat - box, lat + box)) & (d.longitude.between(lon - box * 1.4, lon + box * 1.4))]
    if d.empty:
        return d.assign(distance_m=[], group=[])
    closed_at = d.open_date + pd.to_timedelta(d.get("hour", 0), unit="h") + pd.to_timedelta(d.resolution_hours, unit="h")
    keep = (d.case_status == "Open") | (closed_at >= today - pd.Timedelta(days=recent_closed_days))
    d = d[keep].assign(group=lambda x: x.request_type.map(group_of))
    if group != "Something else":
        d = d[d.group == group]
    d = d.assign(distance_m=distance_m(d.latitude, d.longitude, lat, lon).round(0))
    return d[d.distance_m <= radius_m].sort_values(["distance_m", "open_date"]).head(top)


# ---------- case details and escalation ----------

def case_details(row: pd.Series, today: pd.Timestamp | None = None) -> dict:
    today = today or pd.Timestamp.now().normalize()
    target = row.get("target_date")
    target = pd.to_datetime(target) if pd.notna(target) else None
    is_open = row.case_status == "Open"
    days_open = int((today - row.open_date).days)
    overdue = bool(is_open and ((target is not None and target < today) or (target is None and row.on_time_flag == 0)))
    district = row.get("council_district")
    district = int(district) if pd.notna(district) else None
    cid = str(row.case_id)
    return {
        "case_id": cid,
        "request_type": row.request_type,
        "status": row.case_status,
        "opened": row.open_date.strftime("%b %d, %Y"),
        "days_open": days_open,
        "target": target.strftime("%b %d, %Y") if target is not None else None,
        "days_past_target": int((today - target).days) if (overdue and target is not None) else None,
        "overdue": overdue,
        "address": str(row.get("address") or "").strip() or None,
        "neighborhood": row.neighborhood,
        "department": row.department,
        "council_district": district,
        "councilor": COUNCILORS.get(district) if district else None,
        "link": TICKET_URL.format(cid) if cid.isdigit() else None,  # new-system (BCS-) cases have no public page yet
        "distance_m": int(row.distance_m) if "distance_m" in row and pd.notna(row.distance_m) else None,
    }


def draft_escalation(c: dict, resident_note: str = "", name: str = "") -> dict:
    """Email to 311, copied to the City Council for the district councilor. The resident reviews and sends."""
    from .engine import DEPT_NAMES
    dept = DEPT_NAMES.get(c["department"], c["department"])
    where = c["address"] or c["neighborhood"]
    late = (f"It was due to be resolved by {c['target']} and is now {c['days_past_target']} days past that target."
            if c.get("days_past_target") else f"It has been open for {c['days_open']} days.")
    attn = (f"Councilor {c['councilor']} (District {c['council_district']})" if c.get("councilor")
            else "the district city councilor")
    subject = f"Follow-up on 311 case {c['case_id']}: {c['request_type']} at {where}"
    body = "\n".join(filter(None, [
        "Hello,",
        "",
        f"I'm writing to follow up on 311 case {c['case_id']} ({c['request_type']}) at {where}, "
        f"in {c['neighborhood']}. The case was opened on {c['opened']} and is assigned to {dept}. {late}",
        "",
        f"What I'm seeing: {resident_note.strip()}" if resident_note.strip() else None,
        "" if resident_note.strip() else None,
        "Could you share an update on when this will be addressed, or let me know if more information would help?",
        f"Case page: {c['link']}" if c.get("link") else None,
        "",
        f"I'm copying the City Council for the attention of {attn}.",
        "",
        "Thank you,",
        name.strip() or "[Your name]",
        "[Your phone or email, if you'd like a reply]",
    ]))
    mailto = (f"mailto:{CITY_311_EMAIL}?" + urllib.parse.urlencode(
        {"cc": CITY_COUNCIL_EMAIL, "subject": subject, "body": body}, quote_via=urllib.parse.quote))
    return {"to": CITY_311_EMAIL, "cc": CITY_COUNCIL_EMAIL, "subject": subject, "body": body, "mailto": mailto}


def prepare_report(group: str, description: str, lat: float, lon: float, address: str | None,
                   noticed: str = "", extra: str = "") -> dict:
    """Everything the resident needs to file on Boston's official 311 site, ready to paste."""
    where = address or f"{lat:.5f}, {lon:.5f}"
    lines = [f"Issue: {group}", f"Location: {where}", f"Coordinates: {lat:.5f}, {lon:.5f}"]
    if description:
        lines.append(f"What it looks like: {description}")
    if noticed:
        lines.append(f"First noticed: {noticed}")
    if extra:
        lines.append(f"More detail: {extra}")
    lines.append("No existing open case was found within 400 m for this issue (checked against City of Boston open data).")
    return {"text": "\n".join(lines), "file_at": REPORT_URL,
            "map": f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=18/{lat:.5f}/{lon:.5f}"}
