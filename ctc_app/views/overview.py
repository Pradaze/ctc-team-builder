from collections import Counter

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import team_builder as T
from countries import COUNTRIES, ISO3

from .. import charts, state, ui


def _iso3(name):
    for code, (n, _, _) in COUNTRIES.items():
        if n == name:
            return ISO3.get(code)
    return None


def render():
    if not state.has_data():
        ui.no_data()
        return
    v = ui.version()
    df, dups = state.people(v)
    A = state.applicants_all(v)
    status = Counter(df["_status"])
    n_countries = len({c for c, o in zip(A.country, A.offset) if o is not None})

    st.markdown(f"# {A.n} people from {n_countries} countries")
    updated = state.last_updated()
    fetch = state.last_action("Fetch")
    sub = f"Data last updated {updated:%d %b %Y at %H:%M}" if updated else ""
    if fetch:
        sub += f", fetched by {fetch['Who']}"
    if dups:
        sub += f". {len(dups)} people applied more than once; their latest application is used."
    st.caption(sub)

    c = st.columns(6)
    c[0].metric("Included", status["Included"])
    c[1].metric("Excluded", status["Excluded"])
    c[2].metric("Not reviewed", status["Not reviewed"])
    c[3].metric("Have an idea", int(A.has_idea.sum()))
    c[4].metric("CV read", int(A.has_cv.sum()), help="CVs whose text could be extracted for keywords")
    c[5].metric("Can't attend finals", int(A.low_avail.sum()))

    # review progress as one thin stacked bar
    st.write(f"Review progress: {status['Included']} included, {status['Excluded']} excluded, "
             f"{status['Not reviewed']} not reviewed. {ui.status_note()}")
    fig = go.Figure()
    for s in ui.STATUSES:
        if status[s]:
            fig.add_trace(go.Bar(y=[""], x=[status[s]], name=s, orientation="h",
                                 marker_color=charts.STATUS_COLORS[s], text=[f"{s} {status[s]}"],
                                 textposition="inside", insidetextanchor="middle",
                                 hovertemplate=f"{s}: %{{x}}<extra></extra>"))
    fig.update_layout(barmode="stack", height=56, margin=dict(l=0, r=0, t=0, b=0), showlegend=False,
                      xaxis=dict(visible=False), yaxis=dict(visible=False),
                      uniformtext=dict(minsize=11, mode="hide"))
    fig.update_traces(textfont=dict(color="white"))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    left, right = st.columns(2, gap="large")
    with left:
        fig = charts.cumulative(df.get(T.col("submitted_at"), []), "Applications over time")
        if fig:
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    with right:
        tz = pd.DataFrame({"Time zone": [T.fmt_offset(o) for o in A.offset],
                           "offset": [o if o is not None else 99 for o in A.offset]})
        tz = tz.groupby(["offset", "Time zone"]).size().reset_index(name="People").sort_values("offset")
        fig = charts.bar(tz, "Time zone", "People", "Where people are, by time zone", text="People")
        fig.update_xaxes(categoryorder="array", categoryarray=tz["Time zone"].tolist())
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    left, right = st.columns(2, gap="large")
    with left:
        cnt = Counter(c for c, o in zip(A.country, A.offset) if o is not None)
        mp = pd.DataFrame([{"Country": k, "People": n, "iso3": _iso3(k)} for k, n in cnt.items()]).dropna()
        if not mp.empty:
            st.plotly_chart(charts.world(mp, "Countries of residence"), width="stretch",
                            config={"displayModeBar": False})
    with right:
        rows = [{"Area": t, "Idea": "Has an idea" if A.has_idea[i] else "No idea yet"}
                for i in range(A.n) for t in A.tags[i]]
        if rows:
            ar = pd.DataFrame(rows).groupby(["Area", "Idea"]).size().reset_index(name="People")
            fig = charts.bar(ar, "People", "Area", "Areas of interest", color="Idea",
                             orientation="h", height=340, color_map=charts.IDEA_COLORS)
            fig.update_layout(barmode="stack")
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    left, right = st.columns(2, gap="large")
    with left:
        bg = pd.Series(A.degree_cat).value_counts().rename_axis("Category").reset_index(name="People")
        st.plotly_chart(charts.bar(bg, "People", "Category", "Programme, as stated on the form",
                                   orientation="h"), width="stretch", config={"displayModeBar": False})
    with right:
        bg = pd.Series(A.cv_field).value_counts().rename_axis("Field").reset_index(name="People")
        st.plotly_chart(charts.bar(bg, "People", "Field", "Main field, from CV and bio keywords",
                                   orientation="h"), width="stretch", config={"displayModeBar": False})
