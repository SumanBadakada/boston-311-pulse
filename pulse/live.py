"""Pull recent 311 requests from Analyze Boston and convert them to the cleaned schema.

Boston is moving 311 to a new system, so recent cases live in two datasets with
different columns: the legacy yearly dataset (numeric case ids) and the
NEW SYSTEM dataset (case ids starting "BCS-"). Both are pulled, mapped to one
schema, and combined.

    python -m pulse.live --days 42 --out data/live_311.csv.gz

Uses the city's CKAN datastore SQL API; standard library + pandas only.
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.parse
import urllib.request

import pandas as pd

API = "https://data.boston.gov/api/3/action/datastore_search_sql"
# Add each new year's legacy resource id from the dataset page when the city publishes it.
LEGACY = {2026: "1a0b420d-99f1-4887-9851-990b2a5a6e17", 2025: "9d7c2214-4709-478a-a2e8-fb2020a5bb94"}
NEW_SYSTEM = "254adca6-64ab-4c5c-9fc0-a6da622be185"
PAGE = 30000  # the city API caps rows per query; page beyond it
TZ = "America/New_York"

LEGACY_COLS = ["case_enquiry_id", "open_dt", "closed_dt", "on_time", "case_status", "closure_reason",
               "type", "department", "neighborhood", "source", "latitude", "longitude"]
NEW_COLS = ["case_id", "open_date", "close_date", "on_time", "case_status", "closure_reason",
            "service_name", "assigned_department", "neighborhood", "report_source", "latitude", "longitude"]

# New-system department names carry a code in brackets, e.g. "Public Works Department (PWD)".
# Map those codes onto the legacy codes so one department isn't split in two.
NEW_DEPT_CODES = {"PWD": "PWDx", "BTD": "BTDT", "ISD": "ISD", "PRD": "PARK", "PARKS": "PARK",
                  "BWSC": "BWSC", "PM": "PROP", "PROP": "PROP", "ACC": "ANML", "BPD": "BPD"}

# Closures that end a case without fixing anything (duplicates, invalid, referred elsewhere).
# Mirrors the course dataset's admin_close flag; those cases don't count as resolved.
ADMIN_CLOSE = re.compile(r"duplicate|invalid|noted|no action|unable to|not (?:a )?city|referred|transfer"
                         r"|canceled|cancelled|withdrawn|test", re.I)


def _sql(query: str, retries: int = 3) -> list[dict]:
    url = API + "?" + urllib.parse.urlencode({"sql": query})
    req = urllib.request.Request(url, headers={"User-Agent": "boston-311-pulse/0.2"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)["result"]["records"]
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))
    return []


def _fetch(resource: str, cols: list[str], date_col: str, since: str) -> pd.DataFrame:
    select = ", ".join(f'"{c}"' for c in cols)
    rows, offset = [], 0
    while True:
        batch = _sql(f'SELECT {select} FROM "{resource}" WHERE "{date_col}" >= \'{since}\' '
                     f'ORDER BY "_id" LIMIT {PAGE} OFFSET {offset}')
        rows += batch
        if len(batch) < PAGE:
            return pd.DataFrame(rows, columns=cols)
        offset += PAGE


def _dept_from_new(name: str) -> str:
    m = re.search(r"\(([A-Za-z]+)\)\s*$", str(name or ""))
    if m:
        return NEW_DEPT_CODES.get(m.group(1).upper(), m.group(1).upper())
    return str(name or "Unknown")


def _local(ts: pd.Series, utc: bool) -> pd.Series:
    t = pd.to_datetime(ts, errors="coerce", utc=utc, format="mixed")
    return t.dt.tz_convert(TZ).dt.tz_localize(None) if utc else t


def to_clean(raw: pd.DataFrame, system: str) -> pd.DataFrame:
    """Map one raw dataset onto the cleaned schema used by the engine."""
    if system == "legacy":
        opened, closed = _local(raw.open_dt, False), _local(raw.closed_dt, False)
        rtype, dept, source, cid = raw["type"], raw.department, raw.source, raw.case_enquiry_id.astype(str)
    else:
        opened, closed = _local(raw.open_date, True), _local(raw.close_date, True)
        rtype, dept, source, cid = raw.service_name, raw.assigned_department.map(_dept_from_new), raw.report_source, raw.case_id
    status = raw.case_status.astype(str).str.strip().str.title()
    closed_ok = status.eq("Closed")
    admin = closed_ok & raw.closure_reason.fillna("").astype(str).str.contains(ADMIN_CLOSE)
    hours = (closed - opened).dt.total_seconds() / 3600
    out = pd.DataFrame({
        "case_id": cid,
        "open_date": opened.dt.normalize(),
        "hour": opened.dt.hour,
        "request_type": rtype.astype(str).str.replace(r"\s+", " ", regex=True).str.strip(),
        "department": dept.astype(str),
        "source": source,
        "neighborhood": raw.neighborhood.fillna("Unknown").astype(str).str.strip(),
        "case_status": status.where(status.isin(["Open", "Closed"]), "Open"),
        "resolved": (closed_ok & ~admin).astype(int),
        "admin_close": admin.astype(int),
        "on_time_flag": raw.on_time.astype(str).str.upper().eq("ONTIME").astype(int),
        "resolution_hours": hours.where(closed_ok & (hours >= 0)).round(2),
        "latitude": pd.to_numeric(raw.latitude, errors="coerce"),
        "longitude": pd.to_numeric(raw.longitude, errors="coerce"),
        "system": system,
    })
    return out[out.open_date.notna()]


def expected_rates(since: str) -> pd.Series:
    """Citywide on-time rate per request type over closed cases since `since`.

    Computed by the city's database (one small GROUP BY per dataset) instead of
    downloading a year of rows. Same definition as the course dataset's expected_on_time.
    """
    parts = []
    for rid, tcol, dcol in [(LEGACY[2026], "type", "open_dt"), (LEGACY[2025], "type", "open_dt"),
                            (NEW_SYSTEM, "service_name", "open_date")]:
        recs = _sql(
            f'SELECT "{tcol}" AS request_type, COUNT(*) AS n, '
            f"SUM(CASE WHEN UPPER(\"on_time\") = 'ONTIME' THEN 1 ELSE 0 END) AS on_time "
            f"FROM \"{rid}\" WHERE \"case_status\" = 'Closed' AND \"{dcol}\" >= '{since}' "
            f'GROUP BY "{tcol}"')
        parts.append(pd.DataFrame(recs, columns=["request_type", "n", "on_time"]))
    t = pd.concat(parts)
    t["request_type"] = t.request_type.astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    t[["n", "on_time"]] = t[["n", "on_time"]].apply(pd.to_numeric)
    g = t.groupby("request_type")[["n", "on_time"]].sum()
    return (g.on_time / g.n).round(4)


def fetch_recent(days: int = 42, history_days: int = 365) -> pd.DataFrame:
    """The last `days` of requests from both systems, with expected_on_time from the last year."""
    now = pd.Timestamp.now(tz=TZ).tz_localize(None).normalize()
    start = now - pd.Timedelta(days=days)
    since = start.strftime("%Y-%m-%d")
    frames = [to_clean(_fetch(LEGACY[y], LEGACY_COLS, "open_dt", since), "legacy")
              for y in sorted({start.year, now.year}) if y in LEGACY]
    frames.append(to_clean(_fetch(NEW_SYSTEM, NEW_COLS, "open_date", since), "new"))
    df = pd.concat(frames, ignore_index=True).drop_duplicates("case_id")
    df = df[df.open_date < now]  # drop today: a partial day would look like a slump
    rates = expected_rates((now - pd.Timedelta(days=history_days)).strftime("%Y-%m-%d"))
    df["expected_on_time"] = df.request_type.map(rates)
    return df.sort_values(["open_date", "hour"]).reset_index(drop=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Download recent Boston 311 requests in the cleaned schema")
    p.add_argument("--days", type=int, default=42)
    p.add_argument("--out", default="data/live_311.csv.gz")
    a = p.parse_args(argv)
    df = fetch_recent(a.days)
    df.to_csv(a.out, index=False)
    by_sys = df.system.value_counts().to_dict()
    print(f"{len(df):,} requests {df.open_date.min():%Y-%m-%d} to {df.open_date.max():%Y-%m-%d} {by_sys} -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
