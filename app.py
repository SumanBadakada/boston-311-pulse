"""311 Pulse: streamlit run app.py"""
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from pulse.engine import build_brief, load
from pulse.report import render
from pulse.writer import answer_question, write_brief

st.set_page_config(page_title="311 Pulse: Boston", layout="wide")

# Keys come from Streamlit secrets on Community Cloud (App settings -> Secrets), or the environment locally.
for key in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
    try:
        if key in st.secrets and not os.environ.get(key):
            os.environ[key] = st.secrets[key]
    except Exception:  # no secrets file locally
        pass

LIVE, SAMPLE = Path("data/live_311.csv.gz"), Path("data/sample_311.csv.gz")
MAX_QUESTIONS = 10  # per visitor session, protects the free AI quota on the public app


@st.cache_data(show_spinner="Loading 311 data...")
def get_data(src) -> pd.DataFrame:
    return load(src)


with st.sidebar:
    st.header("Data")
    upload = st.file_uploader("Cleaned 311 CSV (optional)", type=["csv", "gz"])
    default = LIVE if LIVE.exists() else SAMPLE
    df = get_data(upload if upload is not None else str(default))
    if upload is None:
        st.caption("Live data, refreshed daily from Analyze Boston" if default == LIVE
                   else "Sample data (Sept to Dec 2025)")
    lo, hi = df.open_date.min().date(), df.open_date.max().date()
    earliest = min(hi, (pd.Timestamp(lo) + pd.Timedelta(days=35)).date())  # needs a 4-week baseline
    asof = st.date_input("Week ending", value=hi, min_value=earliest, max_value=hi)
    st.caption(f"{len(df):,} requests, {lo} to {hi}")

b = build_brief(df, pd.Timestamp(asof))
text, source = write_brief(b)

st.title("311 Pulse: Boston")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Requests this week", f"{b.requests_this_window:,}",
          f"{b.requests_this_window - b.requests_weekly_baseline:+,} vs typical", delta_color="off")
c2.metric("Surges flagged", len(b.spikes))
c3.metric("Late open cases", f"{b.overdue_total:,}")
c4.metric("Neighborhoods behind expected", len(b.behind))

st.info(text)
st.caption(f"Written by: {source}. Every figure is computed from the data; the AI only words it.")

q = st.text_input("Ask a follow-up about this week")
if q:
    asked = st.session_state.get("asked", 0)
    if asked >= MAX_QUESTIONS:
        st.write("You've reached the question limit for this session. Refresh the page to start again.")
    else:
        st.session_state["asked"] = asked + 1
        st.write(answer_question(q, b))

left, right = st.columns(2)
with left:
    st.subheader("Surges this week")
    st.dataframe(pd.DataFrame(b.spikes), hide_index=True, use_container_width=True)
    st.subheader("Falling behind (opened in the last 60 days)")
    st.dataframe(pd.DataFrame(b.overdue), hide_index=True, use_container_width=True)
with right:
    st.subheader("Neighborhoods behind expected (30 days)")
    st.dataframe(pd.DataFrame(b.behind), hide_index=True, use_container_width=True)
    st.subheader("Repeat hotspots (30 days)")
    hs = pd.DataFrame(b.hotspots)
    if not hs.empty:
        st.map(hs, latitude="latitude", longitude="longitude", size="reports")
    st.dataframe(hs, hide_index=True, use_container_width=True)

if b.data_watch:
    st.warning("Some request types nearly vanished this week. Boston is moving case types to a new 311 "
               "system, so check the data before reading this as fewer problems.")
    st.dataframe(pd.DataFrame(b.data_watch), hide_index=True)

st.download_button("Download this brief (HTML)", render(b, text, source, df),
                   file_name=f"311-pulse-{b.asof}.html", mime="text/html")
