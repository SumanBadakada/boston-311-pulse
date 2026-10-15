"""Predictions from two years of Boston 311 history (2024-2025, 553K requests).

1. How long a fix usually takes: the median and 80th-percentile resolution time of similar
   past cases. "Similar" starts narrow (same request type, neighborhood and season) and widens
   when there are too few examples, so every prediction rests on at least MIN_N real cases and
   says which comparison it used.
2. The chance a case is closed by its target date, from the same comparison group.
3. A seasonal outlook: the issue types that usually rise over the next four weeks, from the
   same weeks in past years.

Deliberately simple and explainable: empirical quantiles, no black box, evaluated on a holdout
year (see build_model). Model files live in data/model.json, built by `python -m pulse.predict`.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd

from .issues import group_of

MODEL_PATH = Path(__file__).resolve().parent.parent / "data" / "model.json"
MIN_N = 30
SEASONS = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring", 5: "Spring",
           6: "Summer", 7: "Summer", 8: "Summer", 9: "Fall", 10: "Fall", 11: "Fall"}
# Narrowest comparison first. Request types differ between the legacy and new 311 systems,
# so the issue-group levels catch new-system cases the type levels don't know.
LEVELS = [("request_type", "neighborhood", "season"), ("request_type", "season"), ("request_type",),
          ("group", "neighborhood", "season"), ("group", "season"), ("group",), ()]
LEVEL_LABEL = {
    ("request_type", "neighborhood", "season"): "this request type, in this neighborhood, in this season",
    ("request_type", "season"): "this request type citywide, in this season",
    ("request_type",): "this request type citywide",
    ("group", "neighborhood", "season"): "similar issues in this neighborhood, in this season",
    ("group", "season"): "similar issues citywide, in this season",
    ("group",): "similar issues citywide",
    (): "all Boston 311 requests",
}


def season_of(month: int) -> str:
    return SEASONS[int(month)]


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    r = df[(df.resolved == 1) & df.resolution_hours.notna()].copy()
    r["group"] = r.request_type.map(group_of)
    r["season"] = r.open_date.dt.month.map(season_of)
    return r


def fit(df: pd.DataFrame) -> dict:
    """Quantile tables for every comparison level, keyed 'level|value|value'."""
    r = _prep(df)
    tables: dict[str, dict] = {}
    for i, keys in enumerate(LEVELS):
        g = r.groupby(list(keys)) if keys else [((), r)]
        for k, part in g:
            if len(part) < MIN_N:
                continue
            k = k if isinstance(k, tuple) else (k,)
            tables["|".join([str(i), *map(str, k)])] = {
                "n": int(len(part)),
                "p50": round(float(part.resolution_hours.quantile(.5)), 2),
                "p80": round(float(part.resolution_hours.quantile(.8)), 2),
                "on_time": round(float(part.on_time_flag.mean()), 3),
            }
    return tables


def lookup(tables: dict, request_type: str, neighborhood: str, month: int) -> dict:
    vals = {"request_type": request_type, "neighborhood": neighborhood,
            "group": group_of(request_type), "season": season_of(month)}
    for i, keys in enumerate(LEVELS):
        if "group" in keys and vals["group"] == "Something else":
            continue  # a catch-all bucket of unrelated issues says nothing about this one
        hit = tables.get("|".join([str(i), *(str(vals[k]) for k in keys)]))
        if hit:
            return {**hit, "basis": LEVEL_LABEL[keys], "season": vals["season"]}
    raise KeyError("model has no citywide fallback")


def _weekly_outlook(df: pd.DataFrame) -> dict:
    """Average requests per issue group for each week of the year."""
    d = df.assign(group=df.request_type.map(group_of), week=df.open_date.dt.isocalendar().week.astype(int),
                  year=df.open_date.dt.year)
    per_year = d.groupby(["group", "year", "week"]).size().rename("n").reset_index()
    avg = per_year.groupby(["group", "week"]).n.mean().round(1)
    return {g: {int(w): float(v) for w, v in s.droplevel(0).items()} for g, s in avg.groupby(level=0)}


def evaluate(train: pd.DataFrame, test: pd.DataFrame) -> dict:
    """Fit on one year, predict the next: is the typical time close, and is the 80% range honest?"""
    tables = fit(train)
    t = _prep(test)
    preds = [lookup(tables, a, b, c) for a, b, c in zip(t.request_type, t.neighborhood, t.open_date.dt.month)]
    p50 = pd.Series([p["p50"] for p in preds], index=t.index)
    p80 = pd.Series([p["p80"] for p in preds], index=t.index)
    on = pd.Series([p["on_time"] for p in preds], index=t.index)
    base = float(_prep(train).resolution_hours.median())
    err = (t.resolution_hours - p50).abs() / 24
    return {
        "test_cases": int(len(t)),
        "median_abs_error_days": round(float(err.median()), 2),
        "baseline_median_abs_error_days": round(float(((t.resolution_hours - base).abs() / 24).median()), 2),
        "share_within_80pct_range": round(float((t.resolution_hours <= p80).mean()), 3),
        "on_time_predicted": round(float(on.mean()), 3),
        "on_time_actual": round(float(t.on_time_flag.mean()), 3),
        "on_time_brier": round(float(((on - t.on_time_flag) ** 2).mean()), 3),
        "slow_cases_median_abs_error_days": round(float(err[t.resolution_hours > 72].median()), 1),
        "slow_cases_share_within_80pct_range": round(float((t.resolution_hours <= p80)[t.resolution_hours > 72].mean()), 3),
    }


def build_model(path: str) -> dict:
    df = pd.read_csv(path, parse_dates=["open_date"], low_memory=False)
    df["request_type"] = df.request_type.astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    ev = evaluate(df[df.open_date.dt.year == 2024], df[df.open_date.dt.year == 2025])
    return {"trained_on": f"{df.open_date.min():%Y-%m-%d} to {df.open_date.max():%Y-%m-%d}",
            "cases": int((df.resolved == 1).sum()), "evaluation": ev,
            "fix_times": fit(df), "outlook": _weekly_outlook(df)}


@lru_cache(maxsize=1)
def model() -> dict | None:
    try:
        return json.loads(MODEL_PATH.read_text())
    except (OSError, ValueError):
        return None


def _days(hours: float) -> str:
    if hours < 24:
        return "under a day" if hours >= 1 else "within hours"
    d = hours / 24
    return f"{d:.0f} day{'s' if round(d) != 1 else ''}" if d < 60 else f"{d / 30:.0f} months"


def fix_time(request_type: str, neighborhood: str, opened: pd.Timestamp) -> dict | None:
    """Plain-language fix-time prediction for one case, or None without a model file."""
    m = model()
    if not m:
        return None
    p = lookup(m["fix_times"], request_type, neighborhood, opened.month)
    return {**p, "typical": _days(p["p50"]), "range": _days(p["p80"]),
            "expected_by": (opened + pd.Timedelta(hours=p["p80"])).normalize()}


def outlook(asof: pd.Timestamp, weeks: int = 4, top: int = 6) -> list[dict]:
    """Issue groups that usually rise most over the next `weeks`, vs the last `weeks`, in past years."""
    m = model()
    if not m:
        return []
    w = int(asof.isocalendar().week)
    nxt = [((w + i - 1) % 52) + 1 for i in range(1, weeks + 1)]
    prv = [((w - i - 1) % 52) + 1 for i in range(0, weeks)]
    rows = []
    for g, by_week in m["outlook"].items():
        if g == "Something else":
            continue
        a = sum(by_week.get(str(x), 0) for x in nxt) / weeks
        b = sum(by_week.get(str(x), 0) for x in prv) / weeks
        if a >= 20:  # ignore tiny categories
            rows.append({"issue": g, "per_week_next": round(a), "per_week_recent": round(b),
                         "change_pct": round((a - b) / b * 100) if b else None})
    rows.sort(key=lambda r: (r["change_pct"] is None, -(r["change_pct"] or 0)))
    return rows[:top]


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "boston_311_clean.csv"
    out = build_model(src)
    MODEL_PATH.write_text(json.dumps(out, separators=(",", ":")))
    print(json.dumps(out["evaluation"], indent=1), f"\n{len(out['fix_times'])} comparison groups -> {MODEL_PATH}")
