"""Turn computed facts into a short brief a city manager can read in a minute.

Two writers share one contract: input is the Brief's facts, output is markdown.
- `template_brief` is deterministic and needs no API key.
- `ai_brief` asks an LLM (Gemini's free tier, or Claude) to write it, then checks
  that every number in the text appears in the facts. If any number can't be traced, the AI draft is
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


def _allowed_numbers(b: Brief, extra: dict | None = None) -> set[float]:
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
    walk(extra or {})
    allowed.update({0, 1, 2, 3, 4, 5, 30, 311})  # list counts, "30 days", the service name
    return allowed


def ungrounded_numbers(text: str, b: Brief, extra: dict | None = None) -> list[float]:
    """Numbers in the text that don't appear anywhere in the computed facts."""
    allowed = _allowed_numbers(b, extra)
    return sorted(x for x in _numbers_in(text) if x not in allowed)


# ---------- AI writers ----------

SYSTEM = """You write a weekly operations brief for Boston city managers from 311 service request data.
Use ONLY the facts in the JSON you are given. Never compute new numbers (no sums, differences or
percentages you derive yourself) and never estimate: copy figures exactly as they appear.
Lead with the single most actionable item. Then at most five bullets, each naming the place,
the request type and the department where known, and saying why it matters operationally.
If data_watch is non-empty, add a final bullet warning that those request types may have moved to
Boston's new 311 system, so the drop is likely a data change. Plain language, no jargon, under 170 words.
Markdown only."""

QA_SYSTEM = """You answer a city manager's question about Boston 311 requests for the week in the facts.
You get two JSON blocks: "brief" (items flagged this week) and "lookup" (counts for the request types and
neighborhoods the question names: this_week, usual_per_week, still_open_of_this_weeks, by_neighborhood,
by_day). Answer from the lookup first. If a matched type has this_week of 0, say none were reported.
Use ONLY these facts and copy figures exactly; never compute or estimate new numbers. If neither block
covers the question, say so plainly. Two to four sentences, plain language."""

GEMINI_MODEL = "gemini-3.1-flash-lite"   # free tier, same model as the Boston Lease Checker
CLAUDE_MODEL = "claude-sonnet-5-5"


def provider() -> str | None:
    """Gemini first (free tier), then Claude, else no AI. PULSE_PROVIDER forces one."""
    forced = os.environ.get("PULSE_PROVIDER", "").lower()
    if forced in ("gemini", "claude", "none"):
        return None if forced == "none" else forced
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    return None


def _complete(system: str, user: str, max_tokens: int) -> tuple[str, str]:
    """One model call. Returns (text, provider name)."""
    which = provider()
    if which == "gemini":
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        r = client.models.generate_content(
            model=os.environ.get("PULSE_MODEL", GEMINI_MODEL), contents=user,
            config=types.GenerateContentConfig(system_instruction=system, temperature=0.2,
                                               max_output_tokens=max_tokens))
        return (r.text or "").strip(), "gemini"
    if which == "claude":
        import anthropic
        msg = anthropic.Anthropic().messages.create(
            model=os.environ.get("PULSE_MODEL", CLAUDE_MODEL), max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}])
        return "".join(c.text for c in msg.content if getattr(c, "type", "") == "text").strip(), "claude"
    raise RuntimeError("no AI provider configured")


def ai_brief(b: Brief) -> tuple[str, str]:
    """AI-written brief, kept only if every number in it traces to the facts."""
    text, who = _complete(SYSTEM, json.dumps(b.to_dict(), indent=1), 700)
    bad = ungrounded_numbers(text, b)
    if not text or bad:
        return template_brief(b), f"template ({who} draft rejected: untraceable numbers {bad[:5]})"
    return text, who


def write_brief(b: Brief) -> tuple[str, str]:
    if provider() is None:
        return template_brief(b), "template (no AI key set)"
    try:
        return ai_brief(b)
    except Exception as e:  # network, quota, bad key: never leave the manager without a brief
        return template_brief(b), f"template (AI unavailable: {type(e).__name__})"


def template_answer(lookup: dict) -> str | None:
    """A plain answer straight from the lookup, used without an AI key or when the AI's draft fails the check."""
    rows = lookup.get("matched_request_types") or lookup.get("neighborhood_totals") or []
    if not rows:
        return None
    out = []
    for r in rows[:4]:
        line = (f"{r['label']}: {r['this_week']:,} request{'s' if r['this_week'] != 1 else ''} in the week of "
                f"{lookup['week']} (usually about {r['usual_per_week']:g} a week)")
        if r["this_week"]:
            line += f", {r["still_open_of_this_weeks"]:,} of them still open"
            if r.get("by_neighborhood"):
                line += ". Most in " + ", ".join(f"{k} ({v})" for k, v in list(r["by_neighborhood"].items())[:3])
        out.append(line + ".")
    return " ".join(out)


def answer_question(question: str, b: Brief, lookup: dict | None = None) -> str:
    """Follow-up Q&A over the computed facts, with the same number check as the brief.

    `lookup` (engine.question_facts) carries counts for whatever the question names, so questions
    about request types the brief didn't flag (needle pickups, graffiti...) still get real answers.
    """
    lookup = lookup or {}
    plain = template_answer(lookup)
    if provider() is None:
        return plain or "Follow-up questions need an AI key (GEMINI_API_KEY is free)."
    facts = json.dumps({"brief": b.to_dict(), "lookup": lookup})
    try:
        text, _ = _complete(QA_SYSTEM, f"Facts:\n{facts}\n\nQuestion: {question}", 400)
    except Exception as e:
        return plain or f"The AI service is unavailable right now ({type(e).__name__}). Please try again shortly."
    bad = ungrounded_numbers(text, b, lookup)
    if bad:
        return plain or ("I couldn't answer that reliably from this week's figures "
                         f"(the draft used numbers not in the data: {bad[:3]}).")
    return text
