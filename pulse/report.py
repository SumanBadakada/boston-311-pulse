"""A self-contained HTML brief: one file to email or open in any browser, no server needed."""
from __future__ import annotations

import html
import re

import pandas as pd

from .engine import Brief

from . import theme

CSS = f"""
:root{{--ink:{theme.INK};--muted:{theme.MUTED};--line:{theme.LINE};--bg:{theme.NIGHT};--card:{theme.SURFACE};
  --accent:{theme.AMBER};--warn:{theme.AMBER}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 Manrope,-apple-system,Segoe UI,sans-serif}}
{theme.GLOW_CSS}
.pulse-hero{{padding-bottom:12px}}
main{{max-width:900px;margin:0 auto;padding:0 16px 72px}}
h2{{font-size:18px;font-weight:700;letter-spacing:-.01em;margin:40px 0 12px}}
.brief{{background:var(--card);border:1px solid var(--line);border-radius:22px;padding:22px 26px;position:relative}}
.brief::before{{content:"";position:absolute;inset:-1px;border-radius:22px;pointer-events:none;
  background:radial-gradient(ellipse at top left,rgba(255,181,71,.10),transparent 55%)}}
.brief ul{{margin:8px 0 0;padding-left:18px}}.brief li{{margin:6px 0}}.brief p{{margin:0}}
.src{{color:var(--muted);font-size:12px;margin-top:12px}}
table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{text-align:left;padding:10px 10px;border-bottom:1px solid var(--line)}}
th{{color:var(--muted);font-weight:600}}td.n,th.n{{text-align:right;font-variant-numeric:tabular-nums}}
.empty{{color:var(--muted)}}.warn{{color:var(--warn)}}svg text{{fill:var(--muted);font-size:11px}}
.wrap{{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:18px;padding:4px 10px}}
.sub{{color:var(--muted)}}
"""


def _md(text: str) -> str:
    """Just enough markdown for the brief: bold and bullet lists."""
    out, in_list = [], False
    for line in text.splitlines():
        line = html.escape(line.strip())
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{line[2:]}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            if line:
                out.append(f"<p>{line}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _table(rows: list[dict], cols: list[tuple[str, str]], empty: str) -> str:
    if not rows:
        return f'<p class="empty">{html.escape(empty)}</p>'
    num = {k for k, _ in cols if isinstance(rows[0].get(k), (int, float))}
    head = "".join(f'<th class="{"n" if k in num else ""}">{html.escape(label)}</th>' for k, label in cols)
    body = "".join(
        "<tr>" + "".join(f'<td class="{"n" if k in num else ""}">{html.escape(f"{r[k]:,}" if isinstance(r[k], int) else str(r[k]))}</td>'
                         for k, _ in cols) + "</tr>" for r in rows)
    return f'<div class="wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _volume_chart(df: pd.DataFrame, asof: pd.Timestamp, days: int = 56) -> str:
    """Daily request volume for the last 8 weeks, the current window shaded."""
    s = df[(df.open_date > asof - pd.Timedelta(days=days)) & (df.open_date <= asof)].groupby("open_date").size()
    s = s.reindex(pd.date_range(asof - pd.Timedelta(days=days - 1), asof), fill_value=0)
    if s.max() == 0:
        return ""
    w, h, pad = 820, 150, 26
    bw = (w - pad) / len(s)
    bars = []
    for i, (d, v) in enumerate(s.items()):
        bh = (h - 30) * v / s.max()
        cur = d > asof - pd.Timedelta(days=7)
        bars.append(f'<rect x="{pad + i * bw:.1f}" y="{h - 18 - bh:.1f}" width="{bw * 0.8:.1f}" height="{bh:.1f}" '
                    f'fill="var(--{"accent" if cur else "line"})"><title>{d:%b %d}: {v:,}</title></rect>')
    labels = "".join(f'<text x="{pad + i * bw:.1f}" y="{h - 4}">{d:%b %d}</text>'
                     for i, d in enumerate(s.index) if i % 14 == 0)
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Daily 311 requests, last 8 weeks">'
            f'<text x="0" y="12">{s.max():,}</text>{"".join(bars)}{labels}</svg>')


def render(b: Brief, brief_md: str, source: str, df: pd.DataFrame) -> str:
    asof = pd.Timestamp(b.asof)
    # live files hold complete data for ~6 weeks; older rows are only still-open cases, which would look like a lull
    chart_days = 42 if "system" in df.columns else 56
    sections = [
        ("Surges this week", _table(b.spikes, [("neighborhood", "Neighborhood"), ("request_type", "Request type"),
                                               ("observed", "This week"), ("expected", "Normal"), ("ratio", "x normal")],
                                    "No unusual surges this week.")),
        (f"Falling behind: open cases that miss their target (opened in the last {b.overdue_recent_days} days)",
         _table(b.overdue, [("department", "Department"), ("request_type", "Request type"),
                            ("open_overdue", "Open, late"), ("median_age_days", "Median age (days)")],
                "No recent overdue cases.")),
        ("Neighborhoods behind expected (last 30 days, mix-adjusted)",
         _table(b.behind, [("neighborhood", "Neighborhood"), ("cases", "Cases"), ("on_time_pct", "On time %"),
                           ("expected_pct", "Expected %"), ("gap_pts", "Gap (pts)"), ("median_hours", "Median hours")],
                "Every neighborhood is at or ahead of expected.")),
        ("Repeat hotspots (same issue, same ~100 m spot, last 30 days)",
         _table(b.hotspots, [("request_type", "Request type"), ("neighborhood", "Neighborhood"),
                             ("reports", "Reports"), ("still_open", "Still open"),
                             ("latitude", "Lat"), ("longitude", "Long")], "No repeat hotspots.")),
    ]
    if b.data_watch:
        sections.append(('<span class="warn">Data check: request types that nearly vanished</span>',
                         _table(b.data_watch, [("request_type", "Request type"), ("observed", "This week"),
                                               ("expected", "Normal")], "")))
    body = "".join(f"<h2>{t}</h2>{c}" for t, c in sections)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>311 Pulse {b.asof}</title>
<link rel="stylesheet" href="{theme.FONT_URL}"><style>{CSS}</style></head><body>
{theme.hero("311 Pulse: Boston", f"The week ending {b.asof} in Boston 311: {b.requests_this_window:,} requests against a typical {b.requests_weekly_baseline:,}. What surged, who is falling behind, and what keeps coming back.", "Refreshed every morning from City of Boston open data", [])}
<main>
<div class="brief">{_md(brief_md)}<div class="src">Written by: {html.escape(source)}. Every figure is computed from the data; the AI only words it.</div></div>
<h2>Daily requests, last {chart_days // 7} weeks (this week highlighted)</h2>{_volume_chart(df, asof, chart_days)}
{body}
<p class="sub" style="margin-top:30px">Data: City of Boston 311 service requests (Analyze Boston). Not an official City product.</p>
</main></body></html>"""
