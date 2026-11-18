"""Small helpers shared by the dashboard pages."""

import re

import streamlit as st

import config as C
import team_builder as T

from . import auth, state

STATUSES = ["Included", "Excluded", "Not reviewed"]


def user():
    return st.session_state["user"]


def can(role):
    return auth.can(user(), role)


def url(value):
    """Return a clickable https URL, or None if the answer isn't a link."""
    v = (value or "").strip().split(";")[0].strip()
    if not v:
        return None
    if re.match(r"^https?://", v, re.I):
        return v
    if re.match(r"^(www\.)?[\w-]+\.[a-z]{2,}(/|$)", v, re.I):
        return "https://" + v
    return None


def status_note():
    if getattr(C, "INCLUDE_BY_DEFAULT", False):
        return "People not yet reviewed are included in teams unless you exclude them."
    return "Only people marked Included go into teams."


def no_data():
    st.markdown("## No applications loaded yet")
    if can("admin"):
        st.write("Fetch them from Tally or upload an applicants.csv on the Admin page.")
        st.page_link(st.session_state["pages"]["admin"], label="Go to Admin")
    else:
        st.write("An admin needs to fetch the applications first.")


def open_profile(key):
    st.session_state["profile_key"] = key
    st.switch_page(st.session_state["pages"]["profile"])


def person_label(A, i):
    return f"{A.names[i]} ({A.country[i]})"


def answer_columns(df):
    """Form questions in order, without the dashboard's own columns."""
    skip = {T.INCLUDE_COL, T.MOTIVATION_COL, T.NOTES_COL, T.CV_TEXT_COL, T.ID_COL}
    return [c for c in df.columns if c not in skip and not c.startswith("_")]


def version():
    return state.data_version()


def people(n):
    return f"{n} person" if n == 1 else f"{n} people"
