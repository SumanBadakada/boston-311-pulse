"""Turn computed facts into a short brief a city manager can read in a minute.

Two writers share one contract: input is the Brief's facts, output is markdown.
- `template_brief` is deterministic and needs no API key.
- `claude_brief` asks Claude to write it, then checks that every number in the
  text appears in the facts. If any number can't be traced, the AI draft is
  discarded and the template brief is used instead. The model can reword and
  prioritise, but it cannot introduce a figure.
"""
from __future__ import annotations

import json
import os
import re

from .engine import Brief


def _n(x: float) -> str:
    return f"{x:,.0f}" if float(x).is_integer() else f"{x:,.1f}"


def template_brief(b: Brief) -> str:
    lines = [f"**311 Pulse, week ending {b.asof}.** {_n(b.requests_this_window)} requests in the last "
             f"{b.window_days} days, against a typical {_n(b.requests_weekly_baseline)}."]
    if b.spikes:
        s = b.spikes[0]
        lines.append(f"- **Surge:** {s['request_type']} in {s['neighborhood']}: {s['observed']} reports vs "
                     f"about {_n(s['expected'])} normally ({_n(s['ratio'])}x).")
        if len(b.spikes) > 1:
            others = "; ".join(f"{x['request_type']} in {x['neighborhood']} ({_n(x['ratio'])}x)" for x in b.spikes[1:3])
            lines.append(f"- **Also rising:** {others}.")
    if b.overdue:
        o = b.overdue[0]
        lines.append(f"- **Falling behind:** {o['department']} has {_n(o['open_overdue'])} open {o['request_type']} "
                     f"cases opened in the last {b.overdue_recent_days} days that miss their target; "
                     f"{_n(b.overdue_total)} late open cases in this data overall.")
    if b.behind:
        n = b.behind[0]
        lines.append(f"- **Behind expected:** {n['neighborhood']} closed {_n(n['on_time_pct'])}% of cases on time vs "
                     f"{_n(n['expected_pct'])}% expected for its request mix ({_n(n['gap_pts'])} pts).")
    if b.hotspots:
        h = b.hotspots[0]
        lines.append(f"- **Repeat hotspot:** {_n(h['reports'])} {h['request_type']} reports at one spot in "
                     f"{h['neighborhood']} in 30 days, {_n(h['still_open'])} still open.")
    if b.data_watch:
        names = ", ".join(d["request_type"] for d in b.data_watch[:3])
        lines.append(f"- **Check the data first:** {names} nearly vanished this week. Boston is moving case types "
                     "to a new 311 system, so this may be a data change rather than fewer problems.")
    return "\n".join(lines)


# ---------- grounding check ----------

_NUM = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")


def _numbers_in(text: str) -> set[float]:
    out = set()
    for tok in _NUM.findall(text):
        try:
            out.add(round(float(tok.replace(",", "")), 1))
        except ValueError:
            pass
    return out


def _allowed_numbers(b: Brief) -> set[float]:
    allowed: set[float] = set()

    def walk(v):
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            allowed.update({round(float(v), 1), round(float(v)), round(abs(float(v)), 1), round(abs(float(v)))})
        elif isinstance(v, str):
            allowed.update(_numbers_in(v))  # dates and numbers inside names
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(b.to_dict())
    allowed.update({0, 1, 2, 3, 4, 5, 30, 311})  # list counts, "30 days", the service name
    return allowed


def ungrounded_numbers(text: str, b: Brief) -> list[float]:
    """Numbers in the text that don't appear anywhere in the computed facts."""
    allowed = _allowed_numbers(b)
    return sorted(x for x in _numbers_in(text) if x not in allowed)


# ---------- Claude writer ----------

SYSTEM = """You write a weekly operations brief for Boston city managers from 311 service request data.
Use ONLY the facts in the JSON you are given. Never compute new numbers (no sums, differences or
percentages you derive yourself) and never estimate: copy figures exactly as they appear.
Lead with the single most actionable item. Then at most five bullets, each naming the place,
the request type and the department where known, and saying why it matters operationally.
If data_watch is non-empty, add a final bullet warning that those request types may have moved to
Boston's new 311 system, so the drop is likely a data change. Plain language, no jargon, under 170 words.
Markdown only."""


def claude_brief(b: Brief, model: str | None = None) -> tuple[str, str]:
    """Returns (markdown, source) where source is 'claude' or 'template (reason)'."""
    try:
        import anthropic
    except ImportError:
        return template_brief(b), "template (anthropic package not installed)"
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model or os.environ.get("PULSE_MODEL", "claude-sonnet-5-5"), max_tokens=700, system=SYSTEM,
        messages=[{"role": "user", "content": json.dumps(b.to_dict(), indent=1)}])
    text = "".join(c.text for c in msg.content if getattr(c, "type", "") == "text").strip()
    bad = ungrounded_numbers(text, b)
    if bad:
        return template_brief(b), f"template (AI draft rejected: untraceable numbers {bad[:5]})"
    return text, "claude"


def write_brief(b: Brief) -> tuple[str, str]:
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return claude_brief(b)
        except Exception as e:  # network, quota, bad key: never leave the manager without a brief
            return template_brief(b), f"template (AI unavailable: {type(e).__name__})"
    return template_brief(b), "template (no API key)"


QA_SYSTEM = """You answer a city manager's question about this week's Boston 311 brief.
Use ONLY the JSON facts given. If the facts don't contain the answer, say so plainly and suggest
what data would answer it. Never compute or estimate new numbers. Two to four sentences."""


def answer_question(question: str, b: Brief, model: str | None = None) -> str:
    """Follow-up Q&A over the computed facts, with the same number check as the brief."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "Follow-up questions need an Anthropic API key (set ANTHROPIC_API_KEY)."
    import anthropic
    msg = anthropic.Anthropic().messages.create(
        model=model or os.environ.get("PULSE_MODEL", "claude-sonnet-5-5"), max_tokens=400, system=QA_SYSTEM,
        messages=[{"role": "user", "content": f"Facts:\n{json.dumps(b.to_dict())}\n\nQuestion: {question}"}])
    text = "".join(c.text for c in msg.content if getattr(c, "type", "") == "text").strip()
    bad = ungrounded_numbers(text, b)
    if bad:
        return f"I couldn't answer that reliably from this week's figures (the draft used numbers not in the data: {bad[:3]})."
    return text
