"""Cleantech Challenge 2027 organiser dashboard.

Run locally:   streamlit run app.py
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctc_app import auth, state, ui  # noqa: E402
from ctc_app.views import admin, applicants, insights, overview, profile, teams  # noqa: E402

st.set_page_config(page_title="Cleantech Challenge 2027 teams", page_icon="🌱", layout="wide")

st.markdown("""
<style>
h1 { letter-spacing: -0.02em; line-height: 1.05; }
[data-testid="stMetricValue"] { font-family: "Bricolage Grotesque", sans-serif; }
[data-testid="stSidebarNav"] a span { font-size: 0.95rem; }
</style>
""", unsafe_allow_html=True)

user = auth.require_user()
st.session_state["user"] = user

pages = {
    "overview": st.Page(overview.render, title="Overview", icon=":material/space_dashboard:", default=True),
    "applicants": st.Page(applicants.render, title="Applicants", icon=":material/groups:", url_path="applicants"),
    "profile": st.Page(profile.render, title="Profile", icon=":material/person:", url_path="profile"),
    "insights": st.Page(insights.render, title="Insights", icon=":material/insights:", url_path="insights"),
    "teams": st.Page(teams.render, title="Teams", icon=":material/workspaces:", url_path="teams"),
    "admin": st.Page(admin.render, title="Admin", icon=":material/settings:", url_path="admin"),
}
st.session_state["pages"] = pages

nav = {
    "Review": [pages["overview"], pages["applicants"], pages["profile"]],
    "Analyse": [pages["insights"], pages["teams"]],
}
if auth.can(user, "admin"):
    nav["Manage"] = [pages["admin"]]

with st.sidebar:
    st.markdown("### Cleantech Challenge 2027")
    st.caption("Team builder")

current = st.navigation(nav)

with st.sidebar:
    st.divider()
    st.write(user["name"])
    st.caption(f"{user['email']}, {user['role']}")
    auth.sign_out_button(user)

if "_toast" in st.session_state:
    st.toast(st.session_state.pop("_toast"))

current.run()
