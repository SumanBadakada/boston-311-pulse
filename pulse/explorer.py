"""Shared case search and dashboard presentation data."""
import pandas as pd

from . import issues
from .engine import overdue_mask


def search(df, today, *, case="", address="", types=(), departments=(), neighborhoods=(),
           statuses=(), overdue="Any", dates=()):
    keep = pd.Series(True, index=df.index)
    for column, query in (("case_id", case), ("address", address)):
        if query.strip():
            values = df.get(column, pd.Series("", index=df.index))
            keep &= values.astype("string").str.contains(query.strip(), case=False, regex=False, na=False)
    for column, selected in (("request_type", types), ("department", departments),
                             ("neighborhood", neighborhoods), ("case_status", statuses)):
        if selected:
            keep &= df[column].isin(selected)
    if dates:
        keep &= df.open_date.between(pd.Timestamp(dates[0]), pd.Timestamp(dates[-1]))
    late = df.case_status.eq("Open") & overdue_mask(df, today)
    if overdue != "Any":
        keep &= late if overdue == "Overdue" else ~late
    return df.loc[keep].assign(overdue=late).sort_values("open_date", ascending=False, kind="stable")


def nearby(df, row, today, radius=150):
    """Return recent nearby cases, with likely matches distinguished from proximity alone."""
    if not issues.in_boston(row.latitude, row.longitude):
        return df.iloc[:0]
    matches = issues.find_matches(df, row.latitude, row.longitude, "Something else",
                                  radius_m=radius, today=today, top=len(df))
    group = issues.group_of(row.request_type)
    same_type = matches.request_type.eq(row.request_type)
    same_group = matches.group.eq(group) & (group != "Something else")
    match = (same_type | same_group).map({True: "Likely duplicate", False: "Nearby"})
    return matches.loc[matches.index != row.name].assign(match=match)
