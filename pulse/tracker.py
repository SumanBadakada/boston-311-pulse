"""'Track my case': everything a resident with a case number would want, from data alone.

- verdict: on track, running long, or slower than 8 in 10 similar cases (from predict.fix_time)
- cohort: of similar cases reported the same week, how many are already fixed
- neighbors: other open reports of the same issue nearby (useful evidence when following up)
- the City's closing note, and what to do if it isn't actually fixed
- the next step, and a calendar reminder for when to check back
"""
from __future__ import annotations

import pandas as pd

from . import issues, predict


def _closed_at(row) -> pd.Timestamp | None:
    if row.case_status != "Closed" or pd.isna(row.resolution_hours):
        return None
    return row.open_date + pd.Timedelta(hours=float(row.get("hour", 0) or 0) + float(row.resolution_hours))


def verdict(days_open: int, fix: dict | None, is_open: bool, overdue: bool) -> tuple[str, str]:
    """(level, sentence). Levels: closed, on_track, long, slow."""
    if not is_open:
        return "closed", "Closed"
    if not fix:
        return ("slow", "Past its target date") if overdue else ("on_track", "Open")
    hours = days_open * 24
    if hours <= max(fix["p50"], 24):
        return "on_track", "On track: most similar cases take about this long"
    if hours <= fix["p80"]:
        return "long", "Taking longer than usual, but still within the normal range"
    return "slow", "Slower than 8 in 10 similar cases"


def cohort(df: pd.DataFrame, row, today: pd.Timestamp, window_days: int = 3) -> dict | None:
    """Similar cases (same issue group) reported within a few days of this one: share already fixed."""
    data_start = df.loc[df.case_status == "Closed", "open_date"].min()
    if pd.isna(data_start) or row.open_date - pd.Timedelta(days=window_days) < data_start:
        return None  # older than the window where closed cases are kept, so the share would be wrong
    g = issues.group_of(row.request_type)
    same = df[(df.open_date - row.open_date).abs() <= pd.Timedelta(days=window_days)]
    same = same[same.request_type.map(issues.group_of) == g]
    same = same[same.case_id.astype(str) != str(row.case_id)]
    if len(same) < 10:
        return None
    local = same[same.neighborhood == row.neighborhood]
    out = {"group": g, "n": int(len(same)), "fixed_pct": round(100 * same.case_status.eq("Closed").mean())}
    if len(local) >= 10:
        out.update(local_n=int(len(local)), local_fixed_pct=round(100 * local.case_status.eq("Closed").mean()))
    return out


def neighbors(df: pd.DataFrame, row, radius_m: float = 150) -> int:
    """Other open reports of the same issue within radius_m: signs the problem affects more people."""
    if pd.isna(row.latitude) or pd.isna(row.longitude):
        return 0
    m = issues.find_matches(df, row.latitude, row.longitude, issues.group_of(row.request_type),
                            radius_m=radius_m, recent_closed_days=0, top=100)
    m = m[(m.case_status == "Open") & (m.case_id.astype(str) != str(row.case_id))]
    return int(len(m))


def next_step(level: str, c: dict, fix: dict | None, check_back: pd.Timestamp) -> str:
    if level == "closed":
        return ("If the problem is still there, file a new report and mention this case number, so the City "
                "can see it was closed without a fix.")
    if level == "slow":
        return "It's worth a follow-up. Draft an email to 311 and your city councilor below."
    if c.get("overdue"):
        return ("It's past the City's own target date, though similar cases often take this long. "
                "A polite follow-up is reasonable; you can draft one below.")
    if level == "long":
        return f"Give it a little longer. Most similar cases are fixed by {check_back:%B %-d}; check back then."
    return f"Nothing to do yet. Check back around {check_back:%B %-d}."


def ics(c: dict, when: pd.Timestamp, link: str | None) -> str:
    """A one-event calendar file reminding the resident to check on the case."""
    day = when.strftime("%Y%m%d")
    end = (when + pd.Timedelta(days=1)).strftime("%Y%m%d")
    desc = f"Check on Boston 311 case {c['case_id']} ({c['request_type']}, {c['address'] or c['neighborhood']})."
    if link:
        desc += f" {link}"
    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//311 Pulse//EN", "BEGIN:VEVENT",
        f"UID:311-{c['case_id']}-{day}@311pulse", f"DTSTAMP:{pd.Timestamp.utcnow():%Y%m%dT%H%M%SZ}",
        f"DTSTART;VALUE=DATE:{day}", f"DTEND;VALUE=DATE:{end}",
        f"SUMMARY:Check on 311 case {c['case_id']}", f"DESCRIPTION:{desc}", "END:VEVENT", "END:VCALENDAR", ""])


def track(df: pd.DataFrame, row, today: pd.Timestamp) -> dict:
    """Everything the Track my case view shows, for one case row."""
    c = issues.case_details(row, today)
    fix = predict.fix_time(row.request_type, row.neighborhood, row.open_date)
    c["fix"] = fix
    level, headline = verdict(c["days_open"], fix, c["status"] == "Open", c["overdue"])
    closed = _closed_at(row)
    expected = fix["expected_by"] if fix else None
    check_back = expected if (expected is not None and expected > today) else today + pd.Timedelta(days=7)
    near = neighbors(df, row)
    c["nearby_reports"] = near
    return {
        "case": c, "fix": fix, "level": level, "headline": headline,
        "closed_on": closed.strftime("%B %-d, %Y") if closed is not None else None,
        "closed_after_days": int((closed - row.open_date).days) if closed is not None else None,
        "closure_note": str(row.get("closure_note") or "").strip() or None,
        "cohort": cohort(df, row, today), "nearby_reports": near,
        "next_step": next_step(level, c, fix, check_back),
        "check_back": check_back, "calendar": ics(c, check_back, c.get("link")),
    }
