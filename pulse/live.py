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
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
import urllib.parse
import urllib.request

import pandas as pd

API = "https://data.boston.gov/api/3/action/datastore_search_sql"
# Add each new year's legacy resource id from the dataset page when the city publishes it.
LEGACY = {2026: "1a0b420d-99f1-4887-9851-990b2a5a6e17", 2025: "9d7c2214-4709-478a-a2e8-fb2020a5bb94"}
NEW_SYSTEM = "254adca6-64ab-4c5c-9fc0-a6da622be185"
CATALOG = "https://data.boston.gov/api/3/action/package_show?id=311-service-requests"
OPEN311 = "https://311.boston.gov/open311/v2/requests.json"
PAGE = 10000
OPEN311_PAGES = 5
PHOTO_FIELDS = ["submitted_photo", "closed_photo"]
TZ = "America/New_York"

LEGACY_COLS = ["case_enquiry_id", "open_dt", "closed_dt", "on_time", "case_status", "closure_reason",
               "type", "department", "neighborhood", "source", "latitude", "longitude",
               "sla_target_dt", "location", "city_council_district"]
NEW_COLS = ["case_id", "open_date", "close_date", "on_time", "case_status", "closure_reason",
            "service_name", "assigned_department", "neighborhood", "report_source", "latitude", "longitude",
            "target_close_date", "full_address", "city_council_district", "closure_comments"]

# New-system department names carry a code in brackets, e.g. "Public Works Department (PWD)".
# Map those codes onto the legacy codes so one department isn't split in two.
NEW_DEPT_CODES = {"PWD": "PWDx", "BTD": "BTDT", "ISD": "ISD", "PRD": "PARK", "PARKS": "PARK",
                  "BWSC": "BWSC", "PM": "PROP", "PROP": "PROP", "ACC": "ANML", "BPD": "BPD"}

# The new system names finer-grained neighborhoods; fold them into the legacy groupings the
# course analysis (and the legacy dataset) use, so one area isn't split across two names.
NEW_NEIGHBORHOODS = {
    "Allston": "Allston / Brighton", "Brighton": "Allston / Brighton",
    "Fenway": "Fenway / Kenmore / Audubon Circle / Longwood", "Longwood": "Fenway / Kenmore / Audubon Circle / Longwood",
    "Downtown": "Downtown / Financial District", "Chinatown": "Downtown / Financial District",
    "Leather District": "Downtown / Financial District", "North End": "Downtown / Financial District",
    "West End": "Downtown / Financial District", "Bay Village": "South End",
    "South Boston": "South Boston / South Boston Waterfront",
    "South Boston Waterfront": "South Boston / South Boston Waterfront", "Mattapan": "Greater Mattapan",
}

# Closures that end a case without fixing anything (duplicates, invalid, referred elsewhere).
# Mirrors the course dataset's admin_close flag; those cases don't count as resolved.
ADMIN_CLOSE = re.compile(r"duplicate|invalid|noted|no action|unable to|not (?:a )?city|referred|transfer"
                         r"|canceled|cancelled|withdrawn|test", re.I)


def _get_json(url: str, retries: int = 3, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": "boston-311-pulse/0.3"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.load(response)
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            delay = 2 ** (attempt + 1)
            if isinstance(error, HTTPError):
                if error.code not in (408, 429, 500, 502, 503, 504):
                    raise
                retry_after = error.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = int(retry_after)
                    except ValueError:
                        try:
                            delay = (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
                        except (ValueError, TypeError):
                            pass
            if attempt == retries - 1 or delay > 120:
                raise
            time.sleep(max(1, delay))


def _sql(query: str) -> list[dict]:
    url = API + "?" + urllib.parse.urlencode({"sql": query})
    return _get_json(url)["result"]["records"]


def resources():
    """Discover new yearly resources while checking the two known feeds remain available."""
    entries = _get_json(CATALOG)["result"]["resources"]
    legacy = {}
    new = None
    for entry in entries:
        if not entry.get("datastore_active"):
            continue
        name = (entry.get("name") or "").strip()
        year = re.fullmatch(r"311 service requests\s*-\s*(\d{4})", name, re.I)
        if year:
            legacy[int(year[1])] = entry["id"]
        elif name.casefold() == "311 service requests - new system":
            new = entry["id"]
    if not new or not set(LEGACY).issubset(legacy):
        raise ValueError("Expected Boston 311 datasets are missing from the city catalog.")
    return legacy, new


def _fetch(resource: str, cols: list[str], date_col: str, since: str, where: str = "") -> pd.DataFrame:
    select = ", ".join(f'"{c}"' for c in cols)
    extra = f" AND {where}" if where else ""
    rows, cursor = [], -1
    while True:
        batch = _sql(f"""SELECT "_id", {select} FROM "{resource}" WHERE "{date_col}" >= '{since}'{extra} """
                     f'AND "_id" > {cursor} ORDER BY "_id" LIMIT {PAGE}')
        if not batch:
            return pd.DataFrame(rows, columns=cols)
        next_cursor = int(batch[-1]["_id"])
        if next_cursor <= cursor:
            raise ValueError(f"City data pagination stopped advancing for {resource}.")
        rows.extend(batch)
        cursor = next_cursor


LEGACY_CLOSE_PREFIX = re.compile(
    r"^\s*Case Closed\.?\s*(?:Closed date\s*:\s*(?:\w{3} \w{3} \d{1,2} [\d:]+ \w{3,4} \d{4}|\d{4}-\d{2}-\d{2}[ T][\d:.]+))?\s*",
    re.I)


def closure_note(reason, comments=None) -> str:
    """The City's closing note in plain words: legacy notes lose their timestamp prefix,
    new-system notes join the reason and any crew comment."""
    r = LEGACY_CLOSE_PREFIX.sub("", str(reason or "")).strip()
    c = str(comments or "").strip()
    if c and c.lower() not in ("none", "nan") and c.lower() != r.lower():
        r = f"{r}: {c}" if r else c
    return "" if r.lower() in ("none", "nan") else r


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
        target, address = _local(raw.sla_target_dt, False), raw.location
        rtype, dept, source, cid = raw["type"], raw.department, raw.source, raw.case_enquiry_id.astype(str)
    else:
        opened, closed = _local(raw.open_date, False), _local(raw.close_date, False)
        target, address = _local(raw.target_close_date, False), raw.full_address
        rtype, dept, source, cid = raw.service_name, raw.assigned_department.map(_dept_from_new), raw.report_source, raw.case_id
    notes = raw.closure_comments if "closure_comments" in raw else [None] * len(raw)
    hood = raw.neighborhood.fillna("Unknown").astype(str).str.strip()
    hood = hood.replace(NEW_NEIGHBORHOODS)  # legacy also uses "Allston", "Mattapan", "South Boston" at times
    status = raw.case_status.astype(str).str.strip().str.title()
    closed_ok = status.eq("Closed")
    admin = closed_ok & raw.closure_reason.fillna("").astype(str).str.contains(ADMIN_CLOSE)
    hours = (closed - opened).dt.total_seconds() / 3600
    out = pd.DataFrame({
        "case_id": cid,
        "open_date": opened.dt.normalize(),
        "hour": opened.dt.hour,
        "opened_at": opened,
        "closed_at": closed.where(closed_ok),
        "request_type": rtype.astype(str).str.replace(r"\s+", " ", regex=True).str.strip(),
        "department": dept.astype(str),
        "source": source,
        "neighborhood": hood,
        "case_status": status.where(status.isin(["Open", "Closed"]), "Open"),
        "resolved": (closed_ok & ~admin).astype(int),
        "admin_close": admin.astype(int),
        "on_time_flag": raw.on_time.astype(str).str.upper().eq("ONTIME").astype(int),
        "resolution_hours": hours.where(closed_ok & (hours >= 0)).round(2),
        "latitude": pd.to_numeric(raw.latitude, errors="coerce"),
        "longitude": pd.to_numeric(raw.longitude, errors="coerce"),
        "system": system,
        "target_date": target,
        "address": address.fillna("").astype(str).str.replace("&amp;", "&").str.replace(r"\s+", " ", regex=True).str.strip(),
        "council_district": pd.to_numeric(raw.city_council_district, errors="coerce").astype("Int64"),
        "closure_note": [closure_note(r, c) for r, c in zip(raw.closure_reason, notes)],
    })
    for field in PHOTO_FIELDS:
        if field in raw:
            out[field] = raw[field].map(public_photo)
    return out[out.open_date.notna() & out.case_id.notna() & ~out.case_id.isin(["", "None", "nan"])]


def expected_rates(since: str, feeds, through: str) -> pd.DataFrame:
    """Citywide on-time rate per request type over closed cases since `since`.

    Computed by the city's database (one small GROUP BY per dataset) instead of
    downloading a year of rows. Same definition as the course dataset's expected_on_time.
    """
    parts = []
    for rid, tcol, dcol in feeds:
        recs = _sql(
            f'SELECT "{tcol}" AS request_type, COUNT(*) AS n, '
            f"SUM(CASE WHEN UPPER(\"on_time\") = 'ONTIME' THEN 1 ELSE 0 END) AS on_time "
            f"FROM \"{rid}\" WHERE \"case_status\" = 'Closed' AND \"{dcol}\" >= '{since}' AND \"{dcol}\" < '{through}' "
            f'GROUP BY "{tcol}"')
        parts.append(pd.DataFrame(recs, columns=["request_type", "n", "on_time"]))
    t = pd.concat(parts)
    t["request_type"] = t.request_type.astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    t[["n", "on_time"]] = t[["n", "on_time"]].apply(pd.to_numeric)
    g = t.groupby("request_type")[["n", "on_time"]].sum()
    return g.assign(rate=(g.on_time / g.n).round(4))


def public_photo(value):
    if not isinstance(value, str):
        return None
    url = value.strip()
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if (parts.scheme == "https" and parts.hostname in {
            "spot-boston-res.cloudinary.com", "res.cloudinary.com", "311.boston.gov"}
            and not parts.username and not parts.password and not parts.query and not parts.fragment):
        return url
    return None


def _open311_records(cutoff):
    seen = {}
    end = cutoff.tz_localize(TZ).tz_convert("UTC")
    start = (cutoff - pd.Timedelta(days=4)).tz_localize(TZ).tz_convert("UTC")
    for page in range(1, OPEN311_PAGES + 1):
        params = urllib.parse.urlencode({"page": page, "page_size": 100,
                                         "start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                         "end_date": end.strftime("%Y-%m-%dT%H:%M:%SZ")})
        records = _get_json(f"{OPEN311}?{params}", timeout=25)
        if not isinstance(records, list):
            raise ValueError("Unexpected Open311 response.")
        fresh = {str(r["service_request_id"]).upper(): r for r in records if r.get("service_request_id")}
        if not fresh or set(fresh).issubset(seen):
            break
        seen.update(fresh)
        time.sleep(0.3)
    return list(seen.values())


def _link_key(opened, lat, lon, address, request_type):
    """Match across ID schemes only when timestamp, coordinates and street address agree."""
    if pd.isna(opened) or pd.isna(lat) or pd.isna(lon) or not isinstance(address, str):
        return None
    street = re.split(r",|\s+Boston\b", address, maxsplit=1, flags=re.I)[0]
    street = re.sub(r"[^a-z0-9]+", " ", street.lower()).strip()
    if not street:
        return None
    return (pd.Timestamp(opened).floor("s"), round(float(lat), 5), round(float(lon), 5), street, str(request_type).strip().casefold())


def enrich_open311(df, records):
    """Use exact case IDs or unique exact location/time keys. Keep CKAN as the counting source."""
    def unique_keys(rows):
        keys, repeated = {}, set()
        for key, value in rows:
            if key is not None:
                if key in keys:
                    repeated.add(key)
                keys[key] = value
        return {key: value for key, value in keys.items() if key not in repeated}

    by_id = {str(row["service_request_id"]).upper(): row for row in records}
    timestamps = _local(pd.Series([row.get("requested_datetime") for row in records], dtype="object"), True)
    by_location = unique_keys(
        (_link_key(ts, row.get("lat"), row.get("long"), row.get("address"), row.get("service_name")), row)
        for ts, row in zip(timestamps, records)
    )
    case_locations = unique_keys(
        (_link_key(row.opened_at, row.latitude, row.longitude, row.address, row.request_type), row.Index)
        for row in df.itertuples()
    )
    matched = {case_locations[key]: row for key, row in by_location.items() if key in case_locations}
    matched.update({row.Index: by_id[row.case_id.upper()]
                    for row in df.itertuples() if row.case_id.upper() in by_id})
    for idx, row in matched.items():
        media = public_photo(row.get("media_url"))
        update = pd.to_datetime(row.get("updated_datetime"), errors="coerce", utc=True)
        if media and not pd.notna(df.at[idx, "submitted_photo"]):
            df.at[idx, "submitted_photo"] = media
        if pd.notna(update):
            df.at[idx, "open311_updated_at"] = update.tz_convert(TZ).tz_localize(None)
        note = row.get("status_notes")
        if isinstance(note, str) and note.strip() and df.at[idx, "case_status"] == "Closed":
            if not df.at[idx, "closure_note"]:
                df.at[idx, "closure_note"] = note.strip()
    return len(matched)


def fetch_recent(days: int = 42, history_days: int = 365, open_days: int = 180) -> pd.DataFrame:
    """Fetch complete Boston days, plus older cases that are still open."""
    now = pd.Timestamp.now(tz=TZ).tz_localize(None).normalize()
    legacy, new = resources()
    start = now - pd.Timedelta(days=days + 2)
    open_start = now - pd.Timedelta(days=open_days)
    # Both Analyze Boston feeds report Boston wall time. Open311 reports UTC.
    frames = []
    for cutoff, extra in ((start, ""), (open_start, "UPPER(\"case_status\") = 'OPEN'")):
        frames.extend(to_clean(_fetch(rid, LEGACY_COLS + PHOTO_FIELDS, "open_dt",
                                      cutoff.strftime("%Y-%m-%d"), extra), "legacy")
                      for year, rid in sorted(legacy.items()) if cutoff.year <= year <= now.year)
        frames.append(to_clean(_fetch(new, NEW_COLS + PHOTO_FIELDS, "open_date",
                                      cutoff.strftime("%Y-%m-%d"), extra), "new"))
    df = pd.concat(frames, ignore_index=True).drop_duplicates("case_id")
    latest = df.loc[df.system.eq("new"), "open_date"].max()
    if pd.isna(latest) or latest < now - pd.Timedelta(days=2):
        raise ValueError("The new system has no recent activity. Keeping the last published snapshot.")
    # An extract may end midday. Only publish days followed by later feed activity.
    cutoff = min(now, latest)
    full_start = cutoff - pd.Timedelta(days=days)
    df = df.loc[df.open_date.lt(cutoff) & (df.open_date.ge(full_start) | df.case_status.eq("Open"))].copy()
    if df.empty or df.open_date.max() < cutoff - pd.Timedelta(days=1):
        raise ValueError("The latest complete day has no city records. Keeping the last published snapshot.")
    hist = cutoff - pd.Timedelta(days=history_days)
    feeds = [(rid, "type", "open_dt") for year, rid in legacy.items() if hist.year <= year <= now.year]
    rates = expected_rates(hist.strftime("%Y-%m-%d"), feeds, cutoff.strftime("%Y-%m-%d"))
    new_rates = expected_rates(hist.strftime("%Y-%m-%d"), [(new, "service_name", "open_date")], cutoff.strftime("%Y-%m-%d"))
    counts = pd.concat([rates, new_rates]).groupby(level=0)[["n", "on_time"]].sum()
    for column, values in {"expected_on_time": (counts.on_time / counts.n).round(4),
                           "baseline_cases": counts.n, "baseline_on_time": counts.on_time}.items():
        df[column] = df.request_type.map(values)
    df["open311_updated_at"] = pd.NaT
    try:
        matched = enrich_open311(df, _open311_records(cutoff))
        print(f"Open311 enrichment matched {matched:,} public records.")
    except (URLError, TimeoutError, ValueError, KeyError, TypeError) as error:
        print(f"Open311 enrichment unavailable ({type(error).__name__}). Continuing with Analyze Boston.")
    return df.sort_values(["open_date", "hour"]).reset_index(drop=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Download recent Boston 311 requests in the cleaned schema")
    p.add_argument("--days", type=int, default=42)
    p.add_argument("--out", default="data/live_311.csv.gz")
    a = p.parse_args(argv)
    df = fetch_recent(a.days)
    dest = Path(a.out)
    if dest.is_file():
        old = pd.read_csv(dest, usecols=lambda c: c in {
            "case_id", "open_date", "system", "submitted_photo", "open311_updated_at", "closure_note"
        }, dtype={"case_id": str}, parse_dates=["open_date"])
        anchor = min(old.open_date.max(), df.open_date.max())
        overlap = lambda rows: rows.loc[rows.open_date.between(anchor - pd.Timedelta(days=13), anchor)]
        earlier = overlap(old)
        if a.days >= 28 and len(earlier) >= 1000 and len(overlap(df)) < len(earlier) * 0.70:
            raise ValueError("Recent case coverage dropped by more than 30%. Review the source feeds before publishing.")
        cache = old.drop_duplicates("case_id").set_index("case_id")
        if "submitted_photo" in cache:
            df["submitted_photo"] = df.submitted_photo.fillna(df.case_id.map(cache.submitted_photo.map(public_photo)))
        if "open311_updated_at" in cache:
            old_updates = pd.to_datetime(cache.open311_updated_at, errors="coerce", format="mixed")
            df["open311_updated_at"] = df.open311_updated_at.fillna(df.case_id.map(old_updates))
        if "closure_note" in cache:
            missing = df.case_status.eq("Closed") & df.closure_note.eq("")
            df.loc[missing, "closure_note"] = df.loc[missing, "case_id"].map(cache.closure_note).fillna("")
    with NamedTemporaryFile(dir=dest.parent, delete=False) as handle:
        temp = Path(handle.name)
    try:
        df.to_csv(temp, index=False, compression="gzip" if dest.suffix == ".gz" else None)
        temp.replace(dest)
    finally:
        temp.unlink(missing_ok=True)
    by_sys = df.system.value_counts().to_dict()
    print(f"{len(df):,} requests {df.open_date.min():%Y-%m-%d} to {df.open_date.max():%Y-%m-%d} {by_sys} -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
