# 311 Pulse: Boston

[![tests](https://github.com/SumanBadakada/boston-311-pulse/actions/workflows/tests.yml/badge.svg)](https://github.com/SumanBadakada/boston-311-pulse/actions/workflows/tests.yml)

**A weekly operations brief for Boston city managers, built from 311 service requests.** It answers four questions every Monday: *what's surging, who's falling behind, which neighborhoods are being served worse than they should be, and which problems keep coming back.* An AI writes the summary; every number in it is computed from the data and checked before it's shown.

![311 Pulse brief for the week ending 2025-12-31](docs/brief.png)

## Why

Boston logs hundreds of thousands of 311 requests a year. Dashboards show what happened; a manager starting the week needs to know what to act on. This started as a team Tableau analysis for *Analytics for Managers* at BU Questrom, comparing each neighborhood's on-time rate with what its request mix predicts. 311 Pulse turns that one-off analysis into a tool that runs on any week and says what changed.

## What it finds

| Check | How | Example (week ending Dec 31 2025) |
| --- | --- | --- |
| **Surges** | Last 7 days vs the prior 4 weeks for every neighborhood × request type, ranked by a Poisson z-score so 40→120 outranks 2→6 | Unshoveled Sidewalk in Dorchester: 74 reports vs ~1 normally (a snowstorm) |
| **Falling behind** | Cases open on the chosen date that miss their service target, opened in the last 60 days, by department | Transportation: 7,000 late open Parking Enforcement cases |
| **Behind expected** | The team's mix-adjusted gap: on-time rate minus the citywide rate for the same request types, last 30 days | Roslindale: 68.0% on time vs 73.5% expected (−5.5 pts) |
| **Repeat hotspots** | Same request type at the same ~100 m spot, 30 days | 121 Parking Enforcement reports at one South End spot, 120 still open |
| **Data check** | Request types that nearly vanished citywide | Flags types moved to Boston's new 311 system, so a data change isn't read as fewer problems |

## Keeping the AI honest

The AI never computes. `pulse/engine.py` produces every figure with pandas. `pulse/writer.py` sends only those facts to Claude, then extracts every number from the draft and checks it against the facts. **If any number can't be traced, the draft is thrown away** and a deterministic template brief is used instead. The same check guards follow-up questions in the app. Without an API key the template writer runs, so the tool always works.

## Run it

```bash
pip install -r requirements.txt

python -m pulse                                # HTML brief for the latest week in the sample data
python -m pulse --asof 2025-11-05 --out nov.html
streamlit run app.py                           # interactive: pick a week, map, follow-up questions

export ANTHROPIC_API_KEY=...                   # optional: AI-written brief and Q&A
```

The repo ships `data/sample_311.csv.gz`: 68K requests from Sept 15 to Dec 31 2025. Upload the full cleaned dataset in the app's sidebar, or pass `--data path/to/boston_311_clean.csv`.

## How it's built

```
pulse/engine.py   surges, overdue, behind-expected, hotspots, data check (pure pandas)
pulse/writer.py   template brief, Claude brief + number check, follow-up Q&A
pulse/report.py   self-contained HTML brief (inline SVG chart, light/dark)
app.py            Streamlit UI
tests/            offline tests on synthetic data with known answers
```

Design choices worth noting:
- **"As of" any date.** The data stores today's status, so a case closed after the chosen date is treated as still open on it (close time = open time + resolution hours). Any past week can be replayed exactly as it looked then.
- **Actionable over exhaustive.** Overdue cases older than 60 days are counted but not ranked; a 300-day-old open case is a records question, a 3-week-old one can still be worked.
- **A type with no history is not a surge.** New or renamed request codes are excluded from surges rather than flagged at "∞x normal".

## Data and limits

City of Boston [311 Service Requests](https://data.boston.gov/dataset/311-service-requests), cleaned for the course project (2024–2025, 553K requests). `expected_on_time` is the citywide on-time rate for each request type. `on_time_flag` is a case's final result, so "falling behind" lists open cases that end up late.

Boston began moving 311 case types to a new system in late 2025, and moved types leave the legacy dataset; volumes after October 2025 are partly a data effect. A live connector to the city's API is on the roadmap and needs to combine the legacy and new-system datasets.

## Roadmap

- Live daily refresh from the city's open data API, combining legacy and new-system records
- Email the brief every Monday (GitHub Actions)
- Department-level views and week-over-week trend for each flagged item

---

Built by [Suman Somaiah](https://github.com/SumanBadakada), extending a team project from *Analytics for Managers* (BU Questrom), with AI pair-programming (Claude). Not an official City of Boston product. MIT licensed.
