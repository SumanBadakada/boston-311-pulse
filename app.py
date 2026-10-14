"""311 Pulse: streamlit run app.py

Tab 1 (residents): photo -> is it already reported? -> case details, an escalation email if it's
stalled, or a ready-to-file report for Boston's official 311 site.
Tab 2 (managers): the weekly operations brief.
"""
import hashlib
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from pulse import issues, theme
from pulse.engine import DEPT_NAMES, build_brief, load
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
MAX_QUESTIONS = 10       # brief follow-ups per visitor session
MAX_PHOTO_READS = 15     # AI photo reads per visitor session; both protect the free AI quota
S = st.session_state


@st.cache_data(show_spinner="Loading 311 data...")
def get_data(src) -> pd.DataFrame:
    df = load(src)
    if "target_date" in df.columns:
        df["target_date"] = pd.to_datetime(df.target_date, errors="coerce")
    return df


@st.cache_data(show_spinner=False, ttl=86400)
def cached_geocode(address: str):
    return issues.geocode(address)


df = get_data(str(LIVE if LIVE.exists() else SAMPLE))
data_end = df.open_date.max()
TODAY = data_end + pd.Timedelta(days=1)  # data runs to yesterday; "today" for ages and targets

st.markdown(theme.STREAMLIT_CSS, unsafe_allow_html=True)

open_cases = df[df.case_status == "Open"]
if "target_date" in df.columns:
    past = open_cases.target_date.lt(TODAY) | (open_cases.target_date.isna() & open_cases.on_time_flag.eq(0))
else:
    past = open_cases.on_time_flag.eq(0)
st.markdown(theme.hero(
    title="Spot a problem?\nSee if Boston already knows.",
    sub="Add a photo of a pothole, a broken streetlight or dumped trash. We check the City's open 311 cases "
        "nearby, help you follow up on stalled ones, and prepare new reports for filing.",
    pill=(f"Live 311 data, updated daily, through {data_end:%B %-d, %Y}" if LIVE.exists() else "Sample 311 data"),
    stats=[("open cases tracked", f"{len(open_cases):,}"),
           ("past their target date", f"{past.mean():.0%}" if len(open_cases) else "n/a"),
           ("new requests last week", f"{(df.open_date > data_end - pd.Timedelta(days=7)).sum():,}")],
), unsafe_allow_html=True)
tab_report, tab_brief = st.tabs(["Report or check an issue", "City brief"])


# =====================================================================
# Residents
# =====================================================================

def reset_flow():
    """A new photo starts over: forget the old location, search radius and email drafts."""
    for k in list(S.keys()):
        if k in ("loc", "wide", "noticed", "extra", "exif_loc") or str(k).startswith("esc_draft_"):
            del S[k]


def show_case(c: dict, key: str):
    """Case card with details, and an escalation email if it's past its target."""
    st.markdown(f"**{c['request_type']}** · case `{c['case_id']}` · **{c['status']}**")
    cols = st.columns(4)
    cols[0].metric("Opened", c["opened"])
    cols[1].metric("Days open", c["days_open"])
    cols[2].metric("Target", c["target"] or "n/a")
    cols[3].metric("Distance", f"{c['distance_m']} m" if c.get("distance_m") is not None else "n/a")
    st.write(f"{c['address'] or c['neighborhood']} · {DEPT_NAMES.get(c['department'], c['department'])}"
             + (f" · Council District {c['council_district']} ({c['councilor']})" if c.get("councilor") else ""))
    if c.get("link"):
        st.markdown(f"[See this case on BOS:311]({c['link']})")

    if c["status"] != "Open":
        st.success("This was closed in the last week. If the problem is still there, file a new report "
                   "and mention this case number.")
    elif c["overdue"]:
        late = f"{c['days_past_target']} days past its target date" if c.get("days_past_target") else "past its target"
        st.error(f"This case is {late}. You can ask 311 and your city councilor for an update.")
        with st.form(f"esc_{key}"):
            note = st.text_area("What you're seeing (optional, added to the email)",
                                placeholder="e.g. The pothole has grown and cars swerve around it at night.")
            name = st.text_input("Your name (optional)")
            go = st.form_submit_button("Draft the email")
        if go or S.get(f"esc_draft_{key}"):
            d = issues.draft_escalation(c, note, name) if go else S[f"esc_draft_{key}"]
            S[f"esc_draft_{key}"] = d
            st.markdown(f"**To:** {d['to']}  \n**Cc:** {d['cc']}  \n**Subject:** {d['subject']}")
            st.code(d["body"], language=None)
            st.link_button("Open in my email app", d["mailto"])
            st.caption("Nothing is sent until you press send in your own email. Individual councilor addresses "
                       "aren't published, so the Council's shared inbox is copied with your councilor named.")
    else:
        due = f" It's due by {c['target']}." if c.get("target") else ""
        st.info(f"Already reported and still within its target time.{due} No need to file it again.")


with tab_report:
    st.markdown('<p class="civic-section">Add a photo of a problem in a public space. '
                "Works best with the phone's location turned on.</p>", unsafe_allow_html=True)

    up = st.file_uploader("Photo", type=["jpg", "jpeg", "png", "webp"], label_visibility="collapsed")
    if up is not None:
        img = up.getvalue()
        h = hashlib.sha1(img).hexdigest()
        if S.get("photo_hash") != h:  # a new photo restarts the flow
            reset_flow()
            S.photo_hash = h
            S.read = None
            if os.environ.get("GEMINI_API_KEY") and S.get("reads", 0) < MAX_PHOTO_READS:
                with st.spinner("Looking at your photo..."):
                    S.read = issues.classify_photo(img, up.type or "image/jpeg")
                S.reads = S.get("reads", 0) + 1
            S.exif_loc = issues.photo_location(img)

        left, right = st.columns([1, 2])
        left.image(img, width="stretch")
        with right:
            read = S.get("read")
            groups = list(issues.ISSUE_GROUPS)
            default = groups.index(read.group) if read else len(groups) - 1
            if read:
                st.write(f"Looks like: **{read.group}** ({read.confidence:.0%} sure). {read.description}")
                if read.has_people_or_plates:
                    st.warning("Your photo shows people or licence plates. Crop them out before sharing it with the City.")
            group = st.selectbox("Issue type (change it if this isn't right)", groups, index=default)
            description = read.description if read else ""

            if S.get("exif_loc"):
                lat, lon = S.exif_loc
                st.write(f"Location from your photo: {lat:.5f}, {lon:.5f}")
                S.loc = (lat, lon, None)
            else:
                st.caption("Your photo has no location data (most browsers remove it). Tell us where it is:")
            addr = st.text_input("Address or intersection", placeholder="e.g. 115 Jersey St, or Albion St & Clifton St")
            if addr and st.button("Find this location"):
                g = cached_geocode(addr)
                if g:
                    S.loc = g
                    S.pop("wide", None)
                else:
                    st.error("Couldn't find that address in Boston. Try adding a house number or a cross street.")

        if S.get("loc"):
            lat, lon, matched = S.loc
            if matched:
                st.caption(f"Location: {matched}")
            st.map(pd.DataFrame({"lat": [lat], "lon": [lon]}), zoom=16, height=220)

            radius = 400 if S.get("wide") else 150
            matches = issues.find_matches(df, lat, lon, group, radius_m=radius, today=TODAY)
            if matches.empty:
                if not S.get("wide"):
                    st.write(f"No open **{group.lower()}** cases within {radius} m. A couple of details, "
                             "then we'll search a wider area before you file:")
                    with st.form("verify"):
                        noticed = st.selectbox("When did you first notice it?",
                                               ["Today", "This week", "This month", "Longer ago"])
                        extra = st.text_input("Anything that helps a crew find it?",
                                              placeholder="e.g. outside the bakery, eastbound lane")
                        if st.form_submit_button("Search a wider area"):
                            S.wide, S.noticed, S.extra = True, noticed, extra
                            st.rerun()
                else:
                    st.success("Nothing matching has been reported nearby, so this looks new. Here's your report, "
                               "ready to file on Boston's official 311 site (attach your photo there):")
                    rep = issues.prepare_report(group, description, lat, lon, matched,
                                                S.get("noticed", ""), S.get("extra", ""))
                    st.code(rep["text"], language=None)
                    c1, c2 = st.columns(2)
                    c1.link_button("File it on BOS:311", rep["file_at"], type="primary")
                    c2.link_button("Open the spot on a map", rep["map"])
                    st.caption("Filing on the City's site gets you a case number and updates. You can also call 311.")
            else:
                st.write(f"**{len(matches)} matching case{'s' if len(matches) > 1 else ''} within {radius} m.** "
                         "Is one of these your issue?")
                for i, (_, row) in enumerate(matches.iterrows()):
                    c = issues.case_details(row, TODAY)
                    with st.expander(f"{c['request_type']} · {c['address'] or c['neighborhood']} · "
                                     f"{c['distance_m']} m away · opened {c['opened']}"
                                     + (" · past its target date" if c["overdue"] else ""), expanded=(i == 0)):
                        show_case(c, f"m{i}")
                if not S.get("wide") and st.button("None of these is my issue"):
                    S.wide = True
                    st.rerun()
                elif S.get("wide"):
                    st.caption("Still not yours? It may be new: file it on BOS:311.")
                    st.link_button("File a new report on BOS:311", issues.REPORT_URL)

    with st.expander("Already have a case number? Check it here"):
        cid = st.text_input("Case number", placeholder="e.g. 101006726212 or BCS-00245057").strip()
        if cid:
            hit = df[df.case_id.astype(str).str.upper() == cid.upper()]
            if hit.empty:
                st.write("That case isn't in the last six weeks of data or the open cases we track. "
                         f"Look it up on [BOS:311]({issues.TICKET_URL.format(cid)}) instead.")
            else:
                show_case(issues.case_details(hit.iloc[0], TODAY), "lookup")

    st.caption("Photos are sent to Google Gemini to recognise the issue and are not stored by this app. "
               "This app never files or sends anything for you.")


# =====================================================================
# Managers: the weekly brief
# =====================================================================

with tab_brief:
    st.markdown('<p class="civic-section">What changed in Boston 311 this week, for city managers.</p>',
                unsafe_allow_html=True)
    hi = df.open_date.max().date()
    lo = df.open_date.min().date()
    if "system" in df.columns:  # live file: full data covers the last 42 days; older rows are only open cases
        lo = max(lo, (pd.Timestamp(hi) - pd.Timedelta(days=41)).date())
    earliest = min(hi, (pd.Timestamp(lo) + pd.Timedelta(days=35)).date())  # needs a 4-week baseline
    asof = st.date_input("Week ending", value=hi, min_value=earliest, max_value=hi)

    b = build_brief(df, pd.Timestamp(asof))
    text, source = write_brief(b)
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
        asked = S.get("asked", 0)
        if asked >= MAX_QUESTIONS:
            st.write("You've reached the question limit for this session. Refresh the page to start again.")
        else:
            S["asked"] = asked + 1
            st.write(answer_question(q, b))

    left, right = st.columns(2)
    with left:
        st.subheader("Surges this week")
        st.dataframe(pd.DataFrame(b.spikes), hide_index=True, width="stretch")
        st.subheader("Falling behind (opened in the last 60 days)")
        st.dataframe(pd.DataFrame(b.overdue), hide_index=True, width="stretch")
    with right:
        st.subheader("Neighborhoods behind expected (30 days)")
        st.dataframe(pd.DataFrame(b.behind), hide_index=True, width="stretch")
        st.subheader("Repeat hotspots (30 days)")
        hs = pd.DataFrame(b.hotspots)
        if not hs.empty:
            st.map(hs, latitude="latitude", longitude="longitude", size="reports")
        st.dataframe(hs, hide_index=True, width="stretch")

    if b.data_watch:
        st.warning("Some request types nearly vanished this week. Boston is moving case types to a new 311 "
                   "system, so check the data before reading this as fewer problems.")
        st.dataframe(pd.DataFrame(b.data_watch), hide_index=True)

    st.download_button("Download this brief (HTML)", render(b, text, source, df),
                       file_name=f"311-pulse-{b.asof}.html", mime="text/html")
