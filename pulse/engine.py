"""The numbers behind the brief. Plain pandas, no AI.

Every finding is computed here, deterministically, for a chosen "as of" date, so
the same data always gives the same brief and every figure can be traced back
to rows. The AI layer only rewords these facts; it never computes anything.

Expected columns (the cleaned schema from the Analytics for Managers project):
open_date, request_type, department, neighborhood, case_status, resolved,
admin_close, on_time_flag, resolution_hours, expected_on_time, latitude, longitude
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

# Boston 311 department codes as they appear in the data
DEPT_NAMES = {"PWDx": "Public Works", "BTDT": "Transportation", "ISD": "Inspectional Services",
              "PARK": "Parks & Recreation", "INFO": "311 / Information", "GEN": "General Services",
              "BWSC": "Water & Sewer Commission", "PROP": "Property Management", "ANML": "Animal Care & Control",
              "BPD": "Police", "BPHC": "Public Health Commission"}

REQUIRED = ["open_date", "request_type", "department", "neighborhood", "case_status",
            "resolved", "on_time_flag", "resolution_hours", "expected_on_time", "latitude", "longitude"]


def load(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["open_date"], low_memory=False)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"Data is missing columns: {', '.join(missing)}")
    for col in ("request_type", "neighborhood", "department"):
        df[col] = df[col].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    return df


@dataclass
class Brief:
    asof: str
    window_days: int
    baseline_weeks: int
    requests_this_window: int
    requests_weekly_baseline: int
    spikes: list[dict] = field(default_factory=list)
    data_watch: list[dict] = field(default_factory=list)
    overdue: list[dict] = field(default_factory=list)
    overdue_total: int = 0
    overdue_recent_days: int = 60
    behind: list[dict] = field(default_factory=list)
    hotspots: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _window(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return df[(df.open_date > start) & (df.open_date <= end)]


def spikes(df: pd.DataFrame, asof: pd.Timestamp, days: int = 7, baseline_weeks: int = 4,
           min_count: int = 15, min_ratio: float = 2.0, top: int = 8) -> list[dict]:
    """Neighborhood x request type combinations running well above their recent normal.

    Expected count = average per-window count over the baseline weeks before the window.
    Score = (observed - expected) / sqrt(expected), a Poisson z-score, so a jump from
    2 to 6 does not outrank a jump from 40 to 120.
    """
    cur = _window(df, asof - pd.Timedelta(days=days), asof)
    base_end = asof - pd.Timedelta(days=days)
    base = _window(df, base_end - pd.Timedelta(weeks=baseline_weeks), base_end)
    keys = ["neighborhood", "request_type"]
    obs = cur.groupby(keys).size().rename("observed")
    exp = (base.groupby(keys).size() / (baseline_weeks * 7 / days)).rename("expected")
    t = pd.concat([obs, exp], axis=1).fillna(0)
    # A type with no baseline at all is usually a new code or a data change, not a surge.
    t = t[(t.observed >= min_count) & (t.expected >= 1)]
    t["ratio"] = t.observed / t.expected
    t["z"] = (t.observed - t.expected) / np.sqrt(t.expected)
    t = t[t.ratio >= min_ratio].sort_values("z", ascending=False).head(top)
    return [{"neighborhood": n, "request_type": r, "observed": int(row.observed),
             "expected": round(float(row.expected), 1), "ratio": round(float(row.ratio), 1)}
            for (n, r), row in t.iterrows()]


def data_watch(df: pd.DataFrame, asof: pd.Timestamp, days: int = 7, baseline_weeks: int = 4,
               min_baseline: float = 30, max_ratio: float = 0.2, top: int = 5) -> list[dict]:
    """Request types that nearly vanished citywide.

    Boston began moving case types to a new 311 system in late 2025, and moved
    types disappear from this dataset. A collapse like that is a data issue to
    check before anyone reads it as falling demand.
    """
    cur = _window(df, asof - pd.Timedelta(days=days), asof)
    base_end = asof - pd.Timedelta(days=days)
    base = _window(df, base_end - pd.Timedelta(weeks=baseline_weeks), base_end)
    exp = base.groupby("request_type").size() / (baseline_weeks * 7 / days)
    obs = cur.groupby("request_type").size().reindex(exp.index, fill_value=0)
    t = pd.DataFrame({"observed": obs, "expected": exp})
    t = t[(t.expected >= min_baseline) & (t.observed <= t.expected * max_ratio)]
    t = t.sort_values("expected", ascending=False).head(top)
    return [{"request_type": r, "observed": int(row.observed), "expected": round(float(row.expected), 1)}
            for r, row in t.iterrows()]


def open_at(df: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    """Cases already opened and not yet closed at the end of the as-of day.

    The data stores today's status, so a case closed after the as-of date counts
    as open on it. Close time = open time + resolution_hours.
    """
    end = asof + pd.Timedelta(days=1)
    opened = df.open_date + pd.to_timedelta(df.get("hour", 0), unit="h")
    closed = opened + pd.to_timedelta(df.resolution_hours, unit="h")
    still_open = (df.case_status == "Open") | (closed > end)
    return df[(opened < end) & still_open]


def overdue(df: pd.DataFrame, asof: pd.Timestamp, recent_days: int = 60,
            top: int = 8) -> tuple[list[dict], int]:
    """Cases open on the as-of date that miss their service target, opened recently.

    Old cases are counted in the total but not ranked: a 300-day-old case is
    a records question, while one opened last month can still be worked. Note
    `on_time_flag` is each case's final on-time result, so this ranks open cases
    that end up late.
    """
    o = open_at(df, asof)
    o = o[o.on_time_flag == 0].assign(age_days=lambda x: (asof - x.open_date).dt.days)
    recent = o[o.age_days <= recent_days]
    g = (recent.groupby(["department", "request_type"])
               .agg(open_overdue=("age_days", "size"), median_age_days=("age_days", "median"))
               .sort_values("open_overdue", ascending=False).head(top))
    rows = [{"department": DEPT_NAMES.get(d, d), "request_type": r, "open_overdue": int(x.open_overdue),
             "median_age_days": int(x.median_age_days)} for (d, r), x in g.iterrows()]
    return rows, int(len(o))


def backlog(df: pd.DataFrame, asof: pd.Timestamp) -> list[dict]:
    """Every case open on the as-of date, per department: how many, how many past target, how old."""
    o = open_at(df, asof).assign(age_days=lambda x: (asof - x.open_date).dt.days)
    if "target_date" in o.columns:
        target = pd.to_datetime(o.target_date, errors="coerce")
        late = target.lt(asof) | (target.isna() & o.on_time_flag.eq(0))
    else:
        late = o.on_time_flag.eq(0)
    g = (o.assign(late=late.astype(int)).groupby("department")
          .agg(open_cases=("late", "size"), past_target=("late", "sum"),
               median_age_days=("age_days", "median"), oldest_days=("age_days", "max"))
          .sort_values("open_cases", ascending=False))
    return [{"department": DEPT_NAMES.get(d, d), "open_cases": int(x.open_cases), "past_target": int(x.past_target),
             "past_target_pct": round(100 * x.past_target / x.open_cases), "median_age_days": int(x.median_age_days),
             "oldest_days": int(x.oldest_days)} for d, x in g.iterrows()]


def behind_expected(df: pd.DataFrame, asof: pd.Timestamp, days: int = 30,
                    min_cases: int = 100, top: int = 5) -> list[dict]:
    """Neighborhoods resolving cases on time less often than their request mix predicts.

    Same metric as the team's Tableau "Gap (pts)": actual on-time rate minus the
    expected rate, where the expectation is the citywide on-time rate for each
    request type. Mix-adjusted, so a neighborhood isn't penalised for getting
    harder kinds of requests.
    """
    w = _window(df, asof - pd.Timedelta(days=days), asof)
    w = w[(w.resolved == 1) & w.expected_on_time.notna()]
    g = w.groupby("neighborhood").agg(cases=("on_time_flag", "size"), on_time=("on_time_flag", "mean"),
                                      expected=("expected_on_time", "mean"),
                                      median_hours=("resolution_hours", "median"))
    g = g[g.cases >= min_cases]
    g["gap_pts"] = (g.on_time - g.expected) * 100
    g = g[g.gap_pts < 0].sort_values("gap_pts").head(top)
    return [{"neighborhood": n, "cases": int(x.cases), "on_time_pct": round(float(x.on_time * 100), 1),
             "expected_pct": round(float(x.expected * 100), 1), "gap_pts": round(float(x.gap_pts), 1),
             "median_hours": round(float(x.median_hours), 1)} for n, x in g.iterrows()]


def hotspots(df: pd.DataFrame, asof: pd.Timestamp, days: int = 30, min_count: int = 8,
             top: int = 8, precision: int = 3) -> list[dict]:
    """The same request type reported again and again at one spot (~100 m grid).

    Repeats usually mean the underlying problem wasn't fixed, only the case closed.
    """
    w = _window(df, asof - pd.Timedelta(days=days), asof).dropna(subset=["latitude", "longitude"])
    w = w.assign(still_open=w.index.isin(open_at(w, asof).index).astype(int))
    # 3 decimal places of lat/long is roughly a 100 m cell in Boston
    w = w.assign(lat=w.latitude.round(precision), lon=w.longitude.round(precision))
    g = (w.groupby(["request_type", "lat", "lon"])
          .agg(reports=("request_type", "size"), neighborhood=("neighborhood", lambda s: s.mode().iat[0]),
               still_open=("still_open", "sum"))
          .reset_index())
    g = g[g.reports >= min_count].sort_values("reports", ascending=False).head(top)
    return [{"request_type": x.request_type, "neighborhood": x.neighborhood, "latitude": float(x.lat),
             "longitude": float(x.lon), "reports": int(x.reports), "still_open": int(x.still_open)}
            for x in g.itertuples()]


def build_brief(df: pd.DataFrame, asof: str | pd.Timestamp | None = None,
                days: int = 7, baseline_weeks: int = 4) -> Brief:
    asof = pd.Timestamp(asof) if asof is not None else df.open_date.max()
    cur = _window(df, asof - pd.Timedelta(days=days), asof)
    base_end = asof - pd.Timedelta(days=days)
    base = _window(df, base_end - pd.Timedelta(weeks=baseline_weeks), base_end)
    od, od_total = overdue(df, asof)
    return Brief(
        asof=asof.strftime("%Y-%m-%d"), window_days=days, baseline_weeks=baseline_weeks,
        requests_this_window=int(len(cur)),
        requests_weekly_baseline=int(round(len(base) / (baseline_weeks * 7 / days))),
        spikes=spikes(df, asof, days, baseline_weeks),
        data_watch=data_watch(df, asof, days, baseline_weeks),
        overdue=od, overdue_total=od_total,
        behind=behind_expected(df, asof),
        hotspots=hotspots(df, asof),
    )
