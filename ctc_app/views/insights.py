from collections import Counter

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

import team_builder as T

from .. import charts, state, ui


def _groups(A, df, by):
    if by == "Area of interest":
        return [A.tags[i][0] if A.tags[i] else "None given" for i in range(A.n)]
    if by == "Idea":
        return ["Has an idea" if x else "No idea yet" for x in A.has_idea]
    if by == "Time zone":
        def band(o):
            if o is None:
                return "Unknown"
            if o < -1:
                return "Americas (UTC-6 to -3)"
            if o <= 1:
                return "UTC-1 to +1"
            if o <= 3.5:
                return "UTC+2 to +3:30"
            if o <= 6:
                return "UTC+4 to +6"
            return "UTC+6:30 and east"
        return [band(o) for o in A.offset]
    if by == "Review status":
        return list(df["_status"])
    if by == "Main field":
        return list(A.cv_field)
    if by == "Team":
        res, _ = state.current_teams(state.data_version(), state.teams_version())
        if not res:
            return ["No teams yet"] * A.n
        tid = {res["A"].keys[i]: t["id"] for t in res["teams"] for i in t["members"]}
        return [tid.get(k, "Unassigned") for k in A.keys]
    return ["All"] * A.n


def render():
    if not state.has_data():
        ui.no_data()
        return
    v = ui.version()
    df, _ = state.people(v)
    A = state.applicants_all(v)

    st.markdown("# Insights")
    st.caption("Everything here comes from the applications themselves: the areas people picked, and keywords "
               "extracted from their bios, idea descriptions and CVs.")

    # ---- applicant landscape
    st.markdown("## The applicant landscape")
    st.write("Each dot is an applicant. People with similar interests sit close together, so clusters are "
             "natural team pools and isolated dots are people who'll be harder to place. "
             "Select dots with a click, box or lasso to list them.")
    by = st.segmented_control("Colour by", ["Area of interest", "Idea", "Time zone", "Main field",
                                            "Review status", "Team"], default="Area of interest")
    xy = state.landscape(v)
    groups = _groups(A, df, by or "Area of interest")
    land = pd.DataFrame({"x": xy[:, 0], "y": xy[:, 1], "Group": groups, "Name": A.names,
                         "Country": A.country, "Keywords": [", ".join(k[:5]) for k in A.keywords],
                         "_key": A.keys})
    cmap = charts.STATUS_COLORS if by == "Review status" else (charts.IDEA_COLORS if by == "Idea" else None)
    fig = px.scatter(land, x="x", y="y", color="Group", hover_name="Name", color_discrete_map=cmap,
                     hover_data={"x": False, "y": False, "Group": True, "Country": True, "Keywords": True},
                     custom_data=["_key"])
    fig.update_traces(marker=dict(size=11, line=dict(width=1, color="white"), opacity=0.9))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    fig.update_layout(height=600, dragmode="lasso", margin=dict(l=0, r=0, t=10, b=0),
                      legend=dict(orientation="h", x=0, y=1.02, yanchor="bottom", title_text=""))
    ev = st.plotly_chart(fig, width="stretch", on_select="rerun", key=f"land_{by}",
                         selection_mode=("points", "box", "lasso"))
    pts = ev.selection.points if ev and ev.selection else []
    if pts:
        picked = [p["customdata"][0] for p in pts if p.get("customdata")]
        idx = [A.keys.index(k) for k in picked]
        sel = pd.DataFrame({"_key": picked, "Name": [A.names[i] for i in idx],
                            "Country": [A.country[i] for i in idx],
                            "Areas": [", ".join(A.tags[i]) for i in idx],
                            "Keywords": [", ".join(A.keywords[i]) for i in idx]})
        st.write(f"{len(sel)} selected")
        st.dataframe(sel.drop(columns=["_key"]), hide_index=True)
        c1, c2 = st.columns([3, 1])
        k = c1.selectbox("Open a profile", sel["_key"], format_func=dict(zip(sel["_key"], sel["Name"])).get)
        c2.write("")
        if c2.button("Open profile", width="stretch"):
            ui.open_profile(k)

    st.divider()

    # ---- keywords
    st.markdown("## What people are talking about")
    areas = sorted({t for ts in A.tags for t in ts})
    left, right = st.columns([1, 1.2], gap="large")
    with left:
        area = st.selectbox("Keywords among", ["Everyone"] + areas)
        who = range(A.n) if area == "Everyone" else [i for i in range(A.n) if area in A.tags[i]]
        kc = Counter(k for i in who for k in A.keywords[i]).most_common(25)
        if kc:
            kd = pd.DataFrame(kc, columns=["Keyword", "People"])
            st.plotly_chart(charts.bar(kd, "People", "Keyword", f"Top keywords ({len(list(who))} people)",
                                       orientation="h", height=620), width="stretch",
                            config={"displayModeBar": False})
    with right:
        top = [k for k, _ in Counter(k for ks in A.keywords for k in ks).most_common(22)]
        if top and areas:
            z = [[sum(1 for i in range(A.n) if a in A.tags[i] and k in A.keywords[i]) for a in areas]
                 for k in top]
            st.plotly_chart(charts.heatmap(z, areas, top, "Keywords by area of interest", height=680),
                            width="stretch", config={"displayModeBar": False})

    st.divider()

    # ---- skills
    st.markdown("## Skills across the cohort")
    st.write("Each axis is the average strength of one skill area (0 to 1), found from keywords in bios, "
             "programmes and CVs. Compare groups to see what each brings.")
    g = st.segmented_control("Compare", ["Idea", "Area of interest", "Time zone", "Review status"],
                             default="Idea", key="skill_cmp")
    labels = _groups(A, df, g or "Idea")
    axes = [s.title() for s in T.SKILL_NAMES]
    counts = Counter(labels)
    series = []
    for n, (lab, _) in enumerate(counts.most_common(6)):
        m = [i for i in range(A.n) if labels[i] == lab]
        color = charts.IDEA_COLORS.get(lab) or charts.STATUS_COLORS.get(lab) or charts.COLORWAY[n % 12]
        series.append((f"{lab} ({len(m)})", A.skills[m].mean(axis=0), color))
    left, right = st.columns([1.3, 1], gap="large")
    with left:
        st.plotly_chart(charts.radar(series, axes, "Average skill profile", height=480, fill=len(series) <= 3,
                                     hover=charts.skill_hover()),
                        width="stretch", config={"displayModeBar": False})
    with right:
        share = pd.DataFrame({"Skill": axes, "Share": (A.skills >= 0.5).mean(axis=0)})
        share["Label"] = (share["Share"] * 100).round().astype(int).astype(str) + "%"
        fig = charts.bar(share, "Share", "Skill", "Share of applicants strong in each skill",
                         orientation="h", height=460, text="Label")
        fig.update_xaxes(range=[0, 1.1], tickformat=".0%")
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    st.divider()

    # ---- connectedness
    st.markdown("## Who will be hard to place")
    st.write("For each applicant, how similar their closest match is. Low values mean few people share "
             "their interests, so check these people first when reviewing teams.")
    S = A.S_int.copy()
    np.fill_diagonal(S, -1)
    best = S.max(axis=1)
    left, right = st.columns([1, 1.3], gap="large")
    with left:
        fig = px.histogram(pd.DataFrame({"Closest match": best}), x="Closest match", nbins=20,
                           title="Similarity to each person's closest match")
        fig.update_traces(marker_color=charts.GREEN, marker_line_width=0)
        fig.update_layout(height=340, bargap=0.08)
        fig.update_yaxes(title=None)
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    with right:
        low = np.argsort(best)[:10]
        st.dataframe(pd.DataFrame({
            "Name": [A.names[i] for i in low], "Closest match": [round(float(best[i]), 2) for i in low],
            "Country": [A.country[i] for i in low], "Areas": [", ".join(A.tags[i]) for i in low]}),
            hide_index=True, height=340)
