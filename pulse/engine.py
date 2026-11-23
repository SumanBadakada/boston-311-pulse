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

import re
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
        labels = df[col].astype("string").fillna("").str.replace(r"\s+", " ", regex=True).str.strip()
        df[col] = labels.mask(labels.eq(""), "Unknown")
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


def case_times(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    opened = df.open_date + pd.to_timedelta(df.get("hour", 0), unit="h")
    hours = df.resolution_hours.dropna()
    closed = opened + pd.to_timedelta(hours, unit="h", errors="coerce").reindex(df.index)
    if "opened_at" in df:
        opened = pd.to_datetime(df.opened_at, errors="coerce", format="mixed").fillna(opened)
    if "closed_at" in df:
        closed = pd.to_datetime(df.closed_at, errors="coerce", format="mixed").fillna(closed)
    return opened, closed


def open_at(df: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    """Cases open immediately before the next Boston calendar day."""
    end = asof + pd.Timedelta(days=1)
    opened, closed = case_times(df)
    still_open = df.case_status.eq("Open") | closed.ge(end)
    return df[(opened < end) & still_open]


def overdue_mask(df: pd.DataFrame, cutoff: pd.Timestamp) -> pd.Series:
    """Explicit deadlines take precedence over the City's fallback on-time flag."""
    if "target_date" not in df:
        return df.on_time_flag.eq(0)
    target = pd.to_datetime(df.target_date, errors="coerce", format="mixed")
    return target.lt(cutoff) | (target.isna() & df.on_time_flag.eq(0))


def overdue_cases(df: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    opened = open_at(df, asof)
    return opened.loc[overdue_mask(opened, asof + pd.Timedelta(days=1))].assign(
        age_days=lambda x: (asof - x.open_date).dt.days)


def overdue(df: pd.DataFrame, asof: pd.Timestamp, recent_days: int = 60,
            top: int = 8) -> tuple[list[dict], int]:
    """Count all late open cases and rank recent groups by department and type."""
    o = overdue_cases(df, asof)
    recent = o[o.age_days <= recent_days]
    g = (recent.groupby(["department", "request_type"])
               .agg(open_overdue=("age_days", "size"), median_age_days=("age_days", "median"))
               .sort_values("open_overdue", ascending=False).head(top))
    rows = [{"department": DEPT_NAMES.get(d, d), "department_code": d, "request_type": r, "open_overdue": int(x.open_overdue),
             "median_age_days": int(x.median_age_days)} for (d, r), x in g.iterrows()]
    return rows, int(len(o))


def backlog(df: pd.DataFrame, asof: pd.Timestamp) -> list[dict]:
    """Every case open on the as-of date, per department: how many, how many past target, how old."""
    o = open_at(df, asof).assign(age_days=lambda x: (asof - x.open_date).dt.days)
    late = overdue_mask(o, asof + pd.Timedelta(days=1))
    g = (o.assign(late=late.astype(int)).groupby("department")
          .agg(open_cases=("late", "size"), past_target=("late", "sum"),
               median_age_days=("age_days", "median"), oldest_days=("age_days", "max"))
          .sort_values("open_cases", ascending=False))
    return [{"department": DEPT_NAMES.get(d, d), "open_cases": int(x.open_cases), "past_target": int(x.past_target),
             "past_target_pct": round(100 * x.past_target / x.open_cases), "median_age_days": int(x.median_age_days),
             "oldest_days": int(x.oldest_days)} for d, x in g.iterrows()]


def neighborhood_cohort(df: pd.DataFrame, asof: pd.Timestamp, days: int = 30) -> pd.DataFrame:
    w = _window(df, asof - pd.Timedelta(days=days), asof)
    _, closed = case_times(w)
    return w[(w.resolved == 1) & w.expected_on_time.notna() & closed.lt(asof + pd.Timedelta(days=1))]


def behind_expected(df: pd.DataFrame, asof: pd.Timestamp, days: int = 30,
                    min_cases: int = 100, top: int = 5) -> list[dict]:
    """Compare each neighborhood's observed on-time rate with its case mix."""
    w = neighborhood_cohort(df, asof, days)
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
        spikes=spikes(df, asof, days, baseline_weeks, min_count=5 if days == 1 else 15),
        data_watch=data_watch(df, asof, days, baseline_weeks, min_baseline=5 if days == 1 else 30),
        overdue=od, overdue_total=od_total,
        behind=behind_expected(df, asof),
        hotspots=hotspots(df, asof),
    )


def brief_evidence(df: pd.DataFrame, brief: Brief, section: str, finding: dict):
    """Select the source rows used in a single displayed finding."""
    asof = pd.Timestamp(brief.asof)
    boundary = asof - pd.Timedelta(days=brief.window_days)
    if section in ("Request volume", "Surges", "Data-change alerts"):
        base_start = boundary - pd.Timedelta(weeks=brief.baseline_weeks)
        rows = _window(df, base_start, asof)
        for col in ("neighborhood", "request_type"):
            if col in finding:
                rows = rows[rows[col].eq(finding[col])]
        rows = rows.assign(period=np.where(rows.open_date.gt(boundary), "Observed", "Baseline"))
        explanation = (f"Count observed rows. Divide baseline rows by "
                       f"{brief.baseline_weeks * 7 / brief.window_days:g} for the expected count.")
    elif section == "Falling behind":
        rows = overdue_cases(df, asof)
        if "department_code" in finding:
            rows = rows[rows.department.eq(finding["department_code"]) &
                        rows.request_type.eq(finding["request_type"]) &
                        rows.age_days.le(brief.overdue_recent_days)]
        explanation = "These records were open and past target at the end of the selected Boston date."
    elif section == "Behind expected":
        rows = neighborhood_cohort(df, asof)
        rows = rows[rows.neighborhood.eq(finding["neighborhood"])]
        explanation = ("Average the on-time flags for the observed rate. Average the case-level citywide "
                       "rates for the expected rate. The gap is the difference in percentage points.")
    else:
        rows = _window(df, asof - pd.Timedelta(days=30), asof)
        rows = rows[rows.request_type.eq(finding["request_type"]) &
                    rows.latitude.round(3).eq(finding["latitude"]) &
                    rows.longitude.round(3).eq(finding["longitude"])]
        rows = rows.assign(open_as_of=rows.index.isin(open_at(rows, asof).index))
        explanation = "Count these records at the same rounded coordinates and sum the open-as-of flags."
    fields = ["case_id", "open_date", "opened_at", "closed_at", "target_date", "request_type", "department",
              "neighborhood", "case_status", "system", "on_time_flag", "expected_on_time", "baseline_cases",
              "baseline_on_time", "resolution_hours", "latitude", "longitude", "period", "age_days", "open_as_of"]
    return rows[[field for field in fields if field in rows]], explanation


# ---------- facts for follow-up questions ----------

_STOP = {"this", "that", "week", "weeks", "there", "were", "what", "which", "where", "when", "many", "much",
         "have", "been", "with", "from", "about", "requests", "request", "requested", "reports", "reported",
         "cases", "case", "boston", "city", "does", "show", "pickup", "pickups", "issues", "issue", "complaints",
         "last", "most", "more", "than", "they", "them", "their", "should", "could", "would", "neighborhood"}
_SYNONYMS = {"syringe": "needle", "syringes": "needle", "sharps": "needle", "rats": "rodent", "rat": "rodent",
             "mice": "rodent", "streetlight": "street light", "streetlights": "street light",
             "lights": "light", "potholes": "pothole", "trash": "trash", "garbage": "trash"}


def _terms(question: str) -> list[str]:
    words = re.findall(r"[a-z]+", question.lower())
    out = []
    for w in words:
        if len(w) < 4 and w not in _SYNONYMS or w in _STOP:
            continue
        w = _SYNONYMS.get(w, w)
        out.append(w[:-1] if w.endswith("s") and len(w) > 4 and w not in _SYNONYMS.values() else w)
    return out


def question_facts(df: pd.DataFrame, asof: pd.Timestamp, question: str, days: int = 7,
                   baseline_weeks: int = 4, top_types: int = 8) -> dict:
    """Counts the brief doesn't carry, for whatever the question names.

    The brief only lists flagged items, so 'any needle pickups this week?' had nothing to go on.
    This matches the question's words against request types, issue groups and neighborhoods and
    returns this week's counts, the usual weekly count, how many are still open, and where.
    """
    from .issues import group_of  # local import: issues imports engine-free helpers only

    asof = pd.Timestamp(asof)
    start = asof - pd.Timedelta(days=days)
    cur = _window(df, start, asof)
    base = _window(df, start - pd.Timedelta(weeks=baseline_weeks), start)
    still_open = set(open_at(df, asof).case_id.astype(str))
    terms = _terms(question)

    def has(text: str, t: str) -> bool:  # a word in text starts with the term ("rodent" in "Rodent Activity")
        return re.search(r"\b" + re.escape(t), text.lower()) is not None

    names = [str(h) for h in df.neighborhood.dropna().unique()]
    q = question.lower()
    hoods = [h for h in names if re.search(r"\b" + re.escape(h.lower()) + r"\b", q)]  # named in full wins
    hoods = sorted(h for h in hoods if not any(h != o and h.lower() in o.lower() for o in hoods))  # West Roxbury, not Roxbury
    if not hoods:
        hoods = sorted(h for h in names if any(has(h, t) for t in terms))
    hood_words = {w for h in hoods for w in re.findall(r"[a-z]+", h.lower())}
    terms = [t for t in terms if t not in hood_words]
    types = pd.Series(sorted(set(cur.request_type.dropna()) | set(base.request_type.dropna())))
    groups = types.map(group_of)
    direct = types[[any(has(a, t) for t in terms) for a in types]]  # "pothole" -> "Pothole" first
    hit = direct if len(direct) else types[[g != "Something else" and any(has(g, t) for t in terms)
                                            for g in groups]]

    def summary(part_cur, part_base, label):
        row = {"label": label, "this_week": int(len(part_cur)),
               "usual_per_week": int(round(len(part_base) / baseline_weeks)),
               "still_open_of_this_weeks": int(part_cur.case_id.astype(str).isin(still_open).sum())}
        if len(part_cur) and part_cur.neighborhood.nunique() > 1:
            row["by_neighborhood"] = {str(k): int(v) for k, v in part_cur.neighborhood.value_counts().head(5).items()}
        if len(part_cur):
            row["by_day"] = {d.strftime("%a %b %d"): int(v) for d, v in part_cur.open_date.value_counts().sort_index().items()}
        return row

    scope_cur = cur[cur.neighborhood.isin(hoods)] if hoods else cur
    scope_base = base[base.neighborhood.isin(hoods)] if hoods else base
    matched = []
    for t in hit.head(top_types):
        matched.append(summary(scope_cur[scope_cur.request_type == t], scope_base[scope_base.request_type == t],
                               t + (f" in {', '.join(hoods)}" if hoods else "")))
    facts = {"week": f"{start + pd.Timedelta(days=1):%b %d} to {asof:%b %d, %Y}",
             "matched_request_types": matched}
    if hoods and not matched:  # a place but no issue: what was reported there
        facts["neighborhood_totals"] = [summary(cur[cur.neighborhood == h], base[base.neighborhood == h], h)
                                        for h in hoods]
        facts["top_request_types_there"] = {str(k): int(v) for k, v in
                                            scope_cur.request_type.value_counts().head(8).items()}
    if not matched and not hoods:  # nothing named: give the overall picture by issue group
        g = cur.request_type.map(group_of).value_counts()
        facts["requests_by_issue_group_this_week"] = {str(k): int(v) for k, v in g.items()}
    return facts
