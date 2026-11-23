"""Boston 311 case explorer, resident reporting and city operations briefs."""
import hashlib
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from pulse import issues, predict, theme, tracker
from pulse.dashboard import show_evidence, show_explorer
from pulse.engine import DEPT_NAMES, backlog, build_brief, load, overdue_mask, question_facts
from pulse.report import render
from pulse.writer import answer_question, template_brief

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
def get_data(src, snapshot_mtime) -> pd.DataFrame:
    df = load(src)
    if "target_date" in df.columns:
        df["target_date"] = pd.to_datetime(df.target_date, errors="coerce", format="mixed")
    return df


@st.cache_data(show_spinner=False, ttl=86400)
def cached_geocode(address: str):
    return issues.geocode(address)


snapshot = LIVE if LIVE.exists() else SAMPLE
df = get_data(str(snapshot), snapshot.stat().st_mtime_ns)
data_end = df.open_date.max()
TODAY = data_end + pd.Timedelta(days=1)  # Use the snapshot cutoff for status and targets.

st.markdown(theme.STREAMLIT_CSS, unsafe_allow_html=True)

open_cases = df[df.case_status == "Open"]
past = overdue_mask(open_cases, TODAY)
st.markdown(theme.hero(
    title="Boston 311 Pulse",
    sub="Find service requests, report a neighborhood issue, and see how Boston is responding.",
    pill=(f"City data through {data_end:%B %-d, %Y}" if LIVE.exists() else "Sample 311 data"),
    stats=[("open cases tracked", f"{len(open_cases):,}"),
           ("past target", f"{past.sum():,}"),
           ("new requests last week", f"{(df.open_date > data_end - pd.Timedelta(days=7)).sum():,}")],
), unsafe_allow_html=True)
if LIVE.exists() and data_end < pd.Timestamp.now(tz="America/New_York").tz_localize(None).normalize() - pd.Timedelta(days=2):
    st.warning(f"The last complete snapshot ends {data_end:%B %d, %Y}. Status and overdue figures use that snapshot.")
tab_explore, tab_report, tab_track, tab_brief = st.tabs(
    ["Explore requests", "Report an issue", "Track my case", "Daily and weekly briefs"])


# =====================================================================
# Residents
# =====================================================================

def reset_flow():
    """A new photo starts over: forget the old location, search radius and email drafts."""
    for k in list(S.keys()):
        if k in ("loc", "wide", "noticed", "extra", "exif_loc") or str(k).startswith("esc_draft_"):
            del S[k]


def escalation_form(c: dict, key: str):
    """Optional note and name, then a drafted email the resident sends from their own account."""
    with st.form(f"esc_{key}"):
        note = st.text_area("What you're seeing (optional, added to the email)",
                            placeholder="e.g. The pothole has grown and cars swerve around it at night.")
        name = st.text_input("Your name (optional)")
        options = {"311 and district councilor": "both", "311": "311", "District councilor": "council"}
        if not c.get("council_district"):
            options = {"311": "311"}
        recipient = options[st.selectbox("Send to", list(options))]
        go = st.form_submit_button("Draft the email")
    if go or S.get(f"esc_draft_{key}"):
        d = issues.draft_escalation(c, note, name, recipient) if go else S[f"esc_draft_{key}"]
        S[f"esc_draft_{key}"] = d
        st.text(f"To: {d['to']}\n" + (f"Cc: {d['cc']}\n" if d["cc"] else "") + f"Subject: {d['subject']}")
        st.code(d["body"], language=None)
        st.link_button("Open in my email app", d["mailto"])
        st.caption("Review and send from your email app. Council drafts use the shared City Council inbox "
                   "and name the district councilor when available.")


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
    c["fix"] = predict.fix_time(c["request_type"], c["neighborhood"], c["opened_ts"])
    if c["fix"]:
        f = c["fix"]
        st.caption(f"Similar cases are usually fixed in {f['typical']}, and 8 in 10 within {f['range']}. "
                   f"{f['on_time']:.0%} close by their target date. Based on {f['n']:,} past cases: {f['basis']}.")

    if c["status"] != "Open":
        st.success("This was closed in the last week. If the problem is still there, file a new report "
                   "and mention this case number.")
    elif c["overdue"]:
        late = f"{c['days_past_target']} days past its target date" if c.get("days_past_target") else "past its target"
        st.error(f"This case is {late}. You can ask 311 and your city councilor for an update.")
        escalation_form(c, key)
    else:
        due = f" It's due by {c['target']}." if c.get("target") else ""
        st.info(f"Already reported and still within its target time.{due} No need to file it again.")


with tab_explore:
    show_explorer(df, TODAY, escalation_form)


with tab_report:
    st.markdown('<p class="civic-section">Describe a problem in a public space. '
                "Works best with the phone's location turned on.</p>", unsafe_allow_html=True)

    up = st.file_uploader("Photo", type=["jpg", "jpeg", "png", "webp"], label_visibility="collapsed")
    if up is None and S.get("photo_hash"):
        reset_flow()
        for key in ("photo_hash", "read"):
            S.pop(key, None)
    report_manually = st.checkbox("Continue without a photo", key="manual_report") if up is None else False
    if up is not None or report_manually:
        img = up.getvalue() if up is not None else None
        h = hashlib.sha256(img).hexdigest() if img else None
        if img and S.get("photo_hash") != h:  # a new photo restarts the flow
            reset_flow()
            S.photo_hash = h
            S.read = None
            if os.environ.get("GEMINI_API_KEY") and S.get("reads", 0) < MAX_PHOTO_READS:
                with st.spinner("Looking at your photo..."):
                    S.read = issues.classify_photo(img, up.type or "image/jpeg")
                S.reads = S.get("reads", 0) + 1
            S.exif_loc = issues.photo_location(img)

        left, right = st.columns([1, 2])
        if img:
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
            description = st.text_area("What should the crew look for?", value=read.description if read else "",
                                       key=f"report_description_{h}")
            gps = S.get("exif_loc")
            if gps:
                lat, lon = gps
                st.write(f"Location from your photo: {lat:.5f}, {lon:.5f}")
                if st.button("Use photo location"):
                    S.loc = (lat, lon, None)
                    S.pop("wide", None)
                if "loc" not in S:
                    S.loc = (lat, lon, None)
            addr = st.text_input("Address or intersection", placeholder="e.g. 115 Jersey St, or Albion St & Clifton St")
            if addr and st.button("Find this location"):
                g = cached_geocode(addr)
                if g:
                    S.loc = g
                    S.pop("wide", None)
                else:
                    S.pop("loc", None)
                    S.pop("wide", None)
                    st.error("Couldn't find that address in Boston. Try adding a house number or a cross street.")

        if S.get("loc"):
            lat, lon, matched = S.loc
            if matched:
                st.caption(f"Location: {matched}")
            st.map(pd.DataFrame({"lat": [lat], "lon": [lon]}), zoom=16, height=220)

            match_context = (lat, lon, group)
            if S.get("match_context") != match_context:
                S.pop("wide", None)
                S.match_context = match_context
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
                    f = predict.fix_time(group, "", TODAY)
                    if f:
                        st.caption(f"Once filed, issues like this are usually fixed in {f['typical']}, and 8 in 10 "
                                   f"within {f['range']} ({f['basis']}, {f['n']:,} past cases).")
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
                    if st.checkbox("These cases describe a different problem"):
                        rep = issues.prepare_report(group, description, lat, lon, matched,
                                                    S.get("noticed", ""), S.get("extra", ""), checked_clear=False)
                        st.code(rep["text"], language=None)
                        st.link_button("File a new report on BOS:311", rep["file_at"])

    st.caption("Already reported something? Use the Track my case tab.")
    st.caption("Photos are sent to Google Gemini to recognise the issue and are not stored by this app. "
               "This app never files or sends anything for you.")


# =====================================================================
# Residents: track a case they already reported
# =====================================================================

LEVEL_BOX = {"closed": st.success, "on_track": st.info, "long": st.warning, "slow": st.error}

with tab_track:
    st.markdown('<p class="civic-section">Enter your 311 case number to see where it stands, how it compares '
                "with similar cases, and what to do next.</p>", unsafe_allow_html=True)
    cid = st.text_input("Case number", value=st.query_params.get("case", ""),
                        placeholder="e.g. 101006726212 or BCS-00245057").strip()
    if cid:
        st.query_params["case"] = cid  # the page URL now tracks this case: bookmark it to come back
        hit = df[df.case_id.astype(str).str.upper() == cid.upper()]
        if hit.empty:
            st.write("That case isn't in the data we track (the last six weeks, plus cases still open from the last "
                     f"six months). You can look it up on [BOS:311]({issues.TICKET_URL.format(cid)}).")
        else:
            row = hit.iloc[0]
            t = tracker.track(df, row, TODAY)
            c, fix = t["case"], t["fix"]
            st.subheader(f"{c['request_type']} at {c['address'] or c['neighborhood']}")
            st.caption(f"Case {c['case_id']}, {DEPT_NAMES.get(c['department'], c['department'])}, "
                       f"reported {c['opened']}"
                       + (f", Council District {c['council_district']} ({c['councilor']})" if c.get("councilor") else ""))
            LEVEL_BOX[t["level"]](t["headline"] if t["level"] != "closed" else
                                  f"Closed on {t['closed_on']}, {t['closed_after_days']} days after it was reported.")

            if t["level"] != "closed" and fix:
                p80_days = max(fix["p80"] / 24, 1)
                st.progress(min(c["days_open"] / p80_days, 1.0),
                            text=f"Day {c['days_open']}. Similar cases usually take {fix['typical']}; "
                                 f"8 in 10 are fixed within {fix['range']}.")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Days open" if t["level"] != "closed" else "Days to close",
                      c["days_open"] if t["level"] != "closed" else t["closed_after_days"])
            m2.metric("City target date", c["target"] or "None set")
            m3.metric("Usually fixed in", fix["typical"] if fix else "n/a")
            m4.metric("Closed by target, historically", f"{fix['on_time']:.0%}" if fix else "n/a")

            st.markdown("#### What to do next")
            st.write(t["next_step"])
            if t["level"] == "closed":
                if t["closure_note"]:
                    st.write(f"The City's closing note: *{t['closure_note']}*")
                st.link_button("Not fixed? File a new report on BOS:311", issues.REPORT_URL)
            else:
                if c["overdue"] or t["level"] == "slow":
                    escalation_form(c, "track")
                st.download_button(f"Add a reminder for {t['check_back']:%B %-d} to my calendar", t["calendar"],
                                   file_name=f"311-case-{c['case_id']}.ics", mime="text/calendar")
            if c.get("link"):
                st.markdown(f"[See this case on BOS:311]({c['link']})")

            st.markdown("#### How your case compares")
            notes = []
            if t["cohort"]:
                k = t["cohort"]
                line = (f"Of {k['n']:,} similar issues reported around the same time, {k['fixed_pct']}% are already fixed")
                if k.get("local_n"):
                    line += f" ({k['local_fixed_pct']}% of the {k['local_n']} in {c['neighborhood']})"
                notes.append(line + ".")
            if t["nearby_reports"]:
                notes.append(f"{t['nearby_reports']} other open report{'s' if t['nearby_reports'] > 1 else ''} of the same "
                             "issue nearby, so you're not the only one waiting." if t["level"] != "closed" else
                             f"{t['nearby_reports']} open report{'s' if t['nearby_reports'] > 1 else ''} of the same issue "
                             "nearby, which can mean the problem came back or wasn't fully fixed.")
            if fix:
                notes.append(f"Typical times are based on {fix['n']:,} past cases: {fix['basis']}.")
            for n in notes:
                st.write(n)
            st.caption("Bookmark this page to check again later: the link keeps your case number.")


# =====================================================================
# Managers: the weekly brief
# =====================================================================

with tab_brief:
    st.markdown('<p class="civic-section">See what changed across Boston and check each finding against the underlying records.</p>',
                unsafe_allow_html=True)
    hi = df.open_date.max().date()
    lo = df.open_date.min().date()
    if "system" in df.columns:  # live file: full data covers the last 42 days; older rows are only open cases
        lo = max(lo, (pd.Timestamp(hi) - pd.Timedelta(days=41)).date())
    frequency, date = st.columns(2)
    period = frequency.radio("Brief period", ["Daily", "Weekly"], horizontal=True, index=1)
    days = 1 if period == "Daily" else 7
    earliest = min(hi, (pd.Timestamp(lo) + pd.Timedelta(days=27 + days)).date())
    asof = date.date_input("Period ending", value=hi, min_value=earliest, max_value=hi)

    b = build_brief(df, pd.Timestamp(asof), days=days)
    text, source = template_brief(b), "Computed summary"
    week, back, ahead, times = st.tabs(["Findings", "Backlog", "Outlook", "Fix times"])

    with week:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Requests in period", f"{b.requests_this_window:,}",
                  f"{b.requests_this_window - b.requests_weekly_baseline:+,} vs typical", delta_color="off")
        c2.metric("Surges flagged", len(b.spikes))
        c3.metric("Late open cases", f"{b.overdue_total:,}")
        c4.metric("Neighborhoods behind expected", len(b.behind))
        st.info(text)
        show_evidence(df, b)
        q = st.text_input("Ask a follow-up about this period",
                          placeholder="e.g. Any needle pickups in the South End? How many potholes in Dorchester?")
        if q and st.button("Answer from the records"):
            asked = S.get("asked", 0)
            if asked >= MAX_QUESTIONS:
                st.write("You've reached the question limit for this session.")
            else:
                S["asked"] = asked + 1
                S.brief_answer = (q, asof, days, answer_question(
                    q, b, question_facts(df, pd.Timestamp(asof), q, days=days)))
        if S.get("brief_answer", ())[:3] == (q, asof, days):
            st.write(S.brief_answer[3])
        left, right = st.columns(2)
        with left:
            st.subheader("Surges")
            st.dataframe(pd.DataFrame(b.spikes), hide_index=True, width="stretch")
            st.subheader("Neighborhoods behind expected")
            st.dataframe(pd.DataFrame(b.behind), hide_index=True, width="stretch")
        with right:
            st.subheader("Falling behind")
            st.dataframe(pd.DataFrame(b.overdue).drop(columns=["department_code"], errors="ignore"),
                         hide_index=True, width="stretch")
            st.subheader("Repeat hotspots")
            hs = pd.DataFrame(b.hotspots)
            if not hs.empty:
                st.map(hs, latitude="latitude", longitude="longitude", size="reports", height=260)
            st.dataframe(hs, hide_index=True, width="stretch")
        if b.data_watch:
            st.warning("Some request types dropped sharply in this period. Boston is moving case types to a new 311 "
                       "system, so check the data before reading this as fewer problems.")
            st.dataframe(pd.DataFrame(b.data_watch), hide_index=True)
        st.download_button("Download this brief (HTML)", render(b, text, source, df),
                           file_name=f"311-pulse-{period.lower()}-{b.asof}.html", mime="text/html")

    with back:
        bl = pd.DataFrame(backlog(df, pd.Timestamp(asof)))
        if bl.empty:
            st.write("No open cases on this date.")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Open cases", f"{bl.open_cases.sum():,}")
            c2.metric("Past their target date", f"{bl.past_target.sum():,}")
            c3.metric("Share past target", f"{bl.past_target.sum() / bl.open_cases.sum():.0%}")
            st.dataframe(bl.rename(columns={
                "department": "Department", "open_cases": "Open", "past_target": "Past target",
                "past_target_pct": "Past target (%)", "median_age_days": "Median age (days)",
                "oldest_days": "Oldest (days)"}), hide_index=True, width="stretch")
            st.caption("Every case open on the selected date, per department. Open cases are tracked back 180 days, "
                       "so ages cap there.")

    with ahead:
        rows = predict.outlook(pd.Timestamp(asof))
        if not rows:
            st.write("The outlook needs the model file (data/model.json).")
        else:
            st.write("What usually changes over the next four weeks, from the same weeks in 2024 and 2025.")
            st.dataframe(pd.DataFrame(rows).rename(columns={
                "issue": "Issue", "per_week_next": "Typical per week, next 4 weeks",
                "per_week_recent": "Typical per week, last 4 weeks", "change_pct": "Change (%)"}),
                hide_index=True, width="stretch")
            st.caption("Seasonal averages from past years, so they show the usual pattern, not a forecast of "
                       "this year's weather.")

    with times:
        m = predict.model()
        if not m:
            st.write("Fix times need the model file (data/model.json).")
        else:
            c1, c2 = st.columns(2)
            hood = c1.selectbox("Neighborhood", ["Citywide"] + sorted(
                n for n in df.neighborhood.dropna().unique() if n not in ("Unknown", "Boston")))
            season = c2.selectbox("Season", ["Winter", "Spring", "Summer", "Fall"],
                                  index=["Winter", "Spring", "Summer", "Fall"].index(
                                      predict.season_of(pd.Timestamp(asof).month)))
            month = {"Winter": 1, "Spring": 4, "Summer": 7, "Fall": 10}[season]
            out = []
            for g in issues.ISSUE_GROUPS:
                if g == "Something else":
                    continue
                f = predict.fix_time(g, "" if hood == "Citywide" else hood, pd.Timestamp(2026, month, 1))
                out.append({"Issue": g, "Usually fixed in": f["typical"], "8 in 10 fixed within": f["range"],
                            "Closed by target": f"{f['on_time']:.0%}", "Past cases": f["n"]})
            st.dataframe(pd.DataFrame(out), hide_index=True, width="stretch")
            ev = m["evaluation"]
            with st.expander("How accurate are these predictions?"):
                st.write(
                    f"Built from 2024 alone and checked against {ev['test_cases']:,} real 2025 cases: the 80% range "
                    f"held {ev['share_within_80pct_range']:.0%} of actual fix times, and the typical-time estimate was "
                    f"off by {ev['median_abs_error_days']} days at the median, against {ev['baseline_median_abs_error_days']} "
                    f"days for a one-number citywide guess. Slow cases (over 3 days) are harder: their range held "
                    f"{ev['slow_cases_share_within_80pct_range']:.0%}. Predicted on-time rate "
                    f"{ev['on_time_predicted']:.0%} against {ev['on_time_actual']:.0%} actual.")
                st.caption(f"Trained on {m['cases']:,} resolved cases, {m['trained_on']}.")
