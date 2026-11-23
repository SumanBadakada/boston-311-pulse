"""Streamlit case explorer and brief evidence views."""
import pandas as pd
import streamlit as st

from . import issues, predict
from .engine import DEPT_NAMES, brief_evidence, case_times
from .explorer import nearby, search
from .live import public_photo


CASE_COLUMNS = {
    "case_id": "Case number", "request_type": "Issue type", "address": "Address",
    "neighborhood": "Neighborhood", "department": "Department", "open_date": "Opened",
    "case_status": "Status", "target_date": "Target", "overdue": "Overdue",
    "distance_m": "Distance (m)", "match": "Relationship",
}


def case_table(rows):
    display = rows.reindex(
        columns=[key for key in CASE_COLUMNS if key in rows])
    display = display.copy()
    display["department"] = display.department.replace(DEPT_NAMES)
    return display.rename(columns=CASE_COLUMNS)


def show_explorer(df, today, escalation_form):
    st.subheader("Explore service requests")
    st.caption("Browse the last six weeks and open cases reported within the last 180 days in the city snapshot.")
    with st.form("case_filters"):
        case, address = st.columns(2)
        case_query = case.text_input("Case number", placeholder="101… or BCS-…")
        address_query = address.text_input("Address", placeholder="Street or intersection")
        a, b, c = st.columns(3)
        types = a.multiselect("Issue type", sorted(df.request_type.unique()))
        departments = b.multiselect("Department", sorted(df.department.unique()),
                                    format_func=lambda d: DEPT_NAMES.get(d, d))
        neighborhoods = c.multiselect("Neighborhood", sorted(df.neighborhood.unique()))
        dates = a.date_input("Opened between", value=(), min_value=df.open_date.min().date(),
                             max_value=df.open_date.max().date())
        statuses = b.multiselect("Status", sorted(df.case_status.unique()))
        late = c.selectbox("Overdue status", ["Any", "Overdue", "Not overdue"])
        st.form_submit_button("Apply filters", type="primary")

    rows = search(df, today, case=case_query, address=address_query, types=types, departments=departments,
                  neighborhoods=neighborhoods, statuses=statuses, overdue=late, dates=dates)
    if rows.empty:
        st.info("No requests match these filters.")
        return

    summary = st.columns(3)
    summary[0].metric("Matching requests", f"{len(rows):,}")
    summary[1].metric("Open", f"{rows.case_status.eq('Open').sum():,}")
    summary[2].metric("Overdue", f"{rows.overdue.sum():,}")
    mapped = rows[rows.latitude.between(42.2, 42.45) & rows.longitude.between(-71.25, -70.95)]
    if not mapped.empty:
        points = mapped[["latitude", "longitude"]].assign(
            color=["#a12b16" if overdue else "#2373ab" if status == "Open" else "#607180"
                   for overdue, status in zip(mapped.overdue, mapped.case_status)])
        st.map(points, color="color", size=17, height=380)
        st.caption(f"{len(mapped):,} requests with mappable coordinates. Red: overdue · Blue: open · Gray: closed.")

    table = case_table(rows)
    page_size = 100
    max_page = (len(rows) - 1) // page_size + 1
    page = 1
    if max_page > 1:
        page = st.number_input("Results page", min_value=1, max_value=max_page, value=1, step=1)
    start = (page - 1) * page_size
    visible = table.iloc[start:start + page_size]
    event = st.dataframe(visible, hide_index=True, width="stretch", on_select="rerun",
                         selection_mode="single-row", key=f"case_table_{int(rows.index[0])}_{len(rows)}_{page}")
    st.caption(f"Showing {start + 1:,}–{start + len(visible):,} of {len(rows):,}. Select a row to inspect the case.")
    if event.selection.rows:
        show_details(df, rows.iloc[start + event.selection.rows[0]], today, escalation_form)
    elif len(rows) == 1:
        show_details(df, rows.iloc[0], today, escalation_form)


def show_details(df, row, today, escalation_form):
    case = issues.case_details(row, today)
    st.subheader(f"{case['request_type']} · {case['case_id']}")
    st.write(f"{case['address'] or case['neighborhood']} · {case['neighborhood']} · "
             f"{DEPT_NAMES.get(case['department'], case['department'])}")
    st.write(f"**{case['status']}**" + (" · **Overdue**" if case["overdue"] else ""))
    if case["council_district"]:
        st.caption(f"Council District {case['council_district']}"
                   + (f" · {case['councilor']}" if case["councilor"] else ""))
    if case["link"]:
        st.link_button("View on BOS:311", case["link"])
    details, related, actions = st.tabs(["Timeline and photos", "Nearby cases", "Follow up"])
    with details:
        opened, closed = case_times(df.loc[[row.name]])
        events = [
            ("Opened", opened.iloc[0]),
            ("Open311 record updated", pd.to_datetime(row.get("open311_updated_at"), errors="coerce")),
            ("Closed", closed.iloc[0]),
        ]
        for label, date in sorted(((label, date) for label, date in events if pd.notna(date)),
                                  key=lambda event: event[1]):
            st.write(f"**{label}** · {date:%b %d, %Y at %I:%M %p}")
        target = pd.to_datetime(row.get("target_date"), errors="coerce")
        st.caption(f"Service target · {target:%b %d, %Y at %I:%M %p}" if pd.notna(target)
                   else "No service target date in the public record.")
        if not pd.notna(row.get("opened_at")):
            st.caption("Older snapshots record the opening hour. Closing times are estimated from the reported duration.")
        note = row.get("closure_note")
        st.write("**City closure note**")
        st.text(str(note) if pd.notna(note) and str(note).strip() else "No closure note in the public data.")
        photos = [(label, public_photo(row.get(field))) for label, field in (
            ("Submitted photo", "submitted_photo"), ("Closure photo", "closed_photo"))]
        for label, url in photos:
            if url:
                st.image(url, caption=label, width=360)
        if not any(url for _, url in photos):
            st.caption("No public photo is attached to this snapshot record.")

    radius = related.select_slider("Search radius", options=[75, 150, 400], value=150,
                                   format_func=lambda meters: f"{meters} m", key=f"radius_{case['case_id']}")
    matches = nearby(df, row, today, radius=radius)
    with related:
        st.caption("Open or recently closed requests. Likely duplicates share an issue type or issue family.")
        if not matches.empty:
            st.dataframe(case_table(matches), hide_index=True, width="stretch")
        else:
            st.write("No comparable records for this location and radius.")

    with actions:
        if case["status"] == "Open":
            case["fix"] = predict.fix_time(case["request_type"], case["neighborhood"], case["opened_ts"])
            case["nearby_reports"] = int((matches.match.eq("Likely duplicate") &
                                          matches.case_status.eq("Open")).sum()) if not matches.empty else 0
            escalation_form(case, f"explore_{case['case_id']}")
        else:
            st.write("If the problem remains, create a new report and include this case number.")
            st.link_button("Open BOS:311", issues.REPORT_URL)


def show_evidence(df, brief):
    with st.expander("Check the numbers against case records"):
        selection = st.selectbox("Finding", [
            "Request volume", "Surges", "Falling behind", "Behind expected", "Repeat hotspots", "Data-change alerts"
        ])
        findings = {
            "Request volume": [{}], "Surges": brief.spikes, "Falling behind": brief.overdue,
            "Behind expected": brief.behind, "Repeat hotspots": brief.hotspots, "Data-change alerts": brief.data_watch,
        }[selection]
        if not findings:
            st.write("No findings for the selected period.")
            return
        if selection == "Falling behind":
            findings = [{"label": "All late open cases"}, *findings]
        finding = st.selectbox("Records to inspect", findings, format_func=lambda item: item.get("label") or
                               " · ".join(str(item[key]) for key in
                                          ("department", "request_type", "neighborhood", "latitude", "longitude")
                                          if key in item))
        rows, explanation = brief_evidence(df, brief, selection, finding)
        st.write(explanation)
        st.caption(f"{len(rows):,} source rows. The table previews up to 100.")
        st.dataframe(rows.head(100), hide_index=True, width="stretch")
        st.download_button("Download source rows (CSV)", rows.to_csv(index=False),
                           f"311-evidence-{brief.asof}.csv", "text/csv")
