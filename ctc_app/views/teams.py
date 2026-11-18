from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import quote

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

import config as C
import team_builder as T

from .. import charts, state, ui

COMP_LABELS = {
    "interest_similarity": "Interests", "timezone_match": "Time zone",
    "background_diversity": "Background mix", "skill_coverage": "Skill coverage",
    "commitment_balance": "Finals and commitment", "idea_mix": "Idea mix",
    "idea_alignment": "Idea alignment",
}


# What each criterion measures, shown as hover help on the sliders, table
# columns and in the "How scoring works" panel.
COMP_HELP = {
    "interest_similarity":
        "How alike the members' interests are: the average similarity over every pair in the team, from 0 "
        "(nothing in common) to 1 (identical). Each pair's similarity blends the areas they ticked (40%), their "
        "bio, idea and 'other' text (40%) and their CV keywords (20%).",
    "timezone_match":
        "How easily members can meet: the average over every pair, where people up to 1 hour apart score 1, "
        "falling evenly to 0 at 6 hours apart (UTC+0 and UTC+3 score 0.6).",
    "background_diversity":
        "How mixed the backgrounds are: distinct programme categories, and distinct main fields from CVs, "
        "divided by team size, then averaged. 1 means every member comes from a different field.",
    "skill_coverage":
        "Whether the team covers all six skill areas (technical, analytical, commercial, financial, research, "
        "communication): for each skill, the strongest member's score (0 to 1, from keywords in bios and CVs), "
        "averaged over the six.",
    "commitment_balance":
        "Spreads out people who can't attend the London finals: 1 when at most one member can't attend, lower "
        "as more of them share a team. Averaged with how similar members' 1-5 motivation ratings are, where at "
        "least two have one. (Separately, a hard rule makes sure every team has someone who can attend.)",
    "idea_mix":
        "How evenly the team splits between people with and without an idea: 1 when exactly half have one, "
        "falling to 0 when all or none do.",
    "idea_alignment":
        "When a team has two or more idea-holders, how similar their ideas are, from the words in their idea "
        "descriptions. Scaled so the closest 10% of idea pairs score 1. A team with a single idea-holder "
        "scores 1, as there's nothing to clash.",
}
WEIGHT_HELP = ("Weight from 0 to 6. The team score adds up each criterion's 0-1 value times its weight, so only "
               "the ratios matter: 4 against 2 means this counts twice as much. 0 ignores it.")
SIZE_HELP = {
    "min": "No team goes below this. People who would leave a team smaller than this stay in it rather than "
           "being moved to Unassigned.",
    "target": "The size the method aims for. It decides how many teams each pool is split into "
              "(pool size divided by this, rounded), and teams that drift from it score slightly lower.",
    "max": "Hard cap. No team is ever built larger than this.",
}


def _how_it_works():
    with st.expander("How scoring works"):
        st.write("Each team gets a score for six criteria, each between 0 and 1. The **team score** is the "
                 "sum of each value times its weight. Teams are formed to make the total across all teams as "
                 "high as possible, with rules always kept where the numbers allow: every team gets at least "
                 "one person with an idea and one without, at least one person who can attend the London "
                 "finals in person, and sizes stay within the limits you set.")
        for k, label in COMP_LABELS.items():
            st.markdown(f"**{label}.** {COMP_HELP[k]}")
        st.write("Before teams are formed, people are grouped into pools of similar interests and time zones, "
                 "and the weakest interest fits (at most 10% of people) can be left in Unassigned. Changing "
                 "the weights changes which teams score best, not who is in each pool.")


def _build_panel(js):
    with st.expander("Build teams", expanded=js is None):
        st.write("Builds teams from everyone currently included. Rebuilding replaces the current teams, "
                 "including any moves made by hand. Hover over the ? next to each setting to see what it does.")
        base = (js or {}).get("weights") or C.WEIGHTS
        cols = st.columns(3)
        weights = {}
        for n, (k, label) in enumerate(COMP_LABELS.items()):
            weights[k] = cols[n % 3].slider(label, 0.0, 6.0, float(base.get(k, C.WEIGHTS.get(k, 1.0))), 0.5,
                                            key=f"w_{k}", help=f"{COMP_HELP[k]}\n\n{WEIGHT_HELP}")
        s1, s2, s3 = st.columns(3)
        sizes0 = (js or {}).get("sizes") or (C.MIN_TEAM_SIZE, C.TARGET_TEAM_SIZE, C.MAX_TEAM_SIZE)
        mn = s1.number_input("Smallest team", 2, 6, int(sizes0[0]), help=SIZE_HELP["min"])
        tg = s2.number_input("Target size", 2, 6, int(sizes0[1]), help=SIZE_HELP["target"])
        mx = s3.number_input("Largest team", 2, 6, int(sizes0[2]), help=SIZE_HELP["max"])
        if not mn <= tg <= mx:
            st.warning("Sizes need to go smallest ≤ target ≤ largest.")
            return
        edits = (js or {}).get("manual_edits", 0)
        label = "Rebuild teams" if js else "Build teams"
        if st.button(label, type="primary"):
            if edits and not st.session_state.get("_confirm_rebuild"):
                st.session_state["_confirm_rebuild"] = True
                st.warning(f"The current teams have {edits} manual change(s). Press {label} again to replace them.")
                return
            st.session_state.pop("_confirm_rebuild", None)
            v = state.data_version()
            A = state.applicants_included(v)
            if A is None:
                st.error("No one is included yet. Include people on the Applicants page first.")
                return
            with st.spinner("Forming teams…"), T.settings(weights=weights, sizes=(mn, tg, mx)):
                try:
                    _, dups = state.people(v)
                    res = T.build_teams(A, dups, log=lambda *_: None)
                except ValueError as e:
                    st.error(str(e))
                    return
            placed = sum(len(t["members"]) for t in res["teams"])
            state.save_teams(res, ui.user()["email"], "Build teams",
                             f"{len(res['teams'])} teams, {placed} placed, {A.n - placed} unassigned",
                             built_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                             built_by=ui.user()["email"], manual_edits=0)
            st.session_state["_toast"] = f"Built {len(res['teams'])} teams"
            st.rerun()


def _move_panel(res, tables, js):
    A = res["A"]
    with st.container(border=True):
        st.markdown("#### Move someone")
        team_of = {i: t["id"] for t in res["teams"] for i in t["members"]}
        people = sorted(range(A.n), key=lambda i: A.names[i].lower())
        c1, c2, c3 = st.columns([2, 1.3, 1])
        i = c1.selectbox("Person", people, format_func=lambda i: f"{A.names[i]} ({team_of.get(i, 'Unassigned')})")
        options = [t["id"] for t in res["teams"]] + ["Unassigned", "New team"]
        here = team_of.get(i, "Unassigned")
        dest = c2.selectbox("Move to", [o for o in options if o != here])
        c3.write("")
        src = next((t for t in res["teams"] if i in t["members"]), None)
        dst = next((t for t in res["teams"] if t["id"] == dest), None)
        problems = []
        if dst is not None and len(dst["members"]) >= C.MAX_TEAM_SIZE:
            problems.append(("block", f"{dest} already has {len(dst['members'])} members, the maximum."))
        if src is not None:
            rest = [x for x in src["members"] if x != i]
            if rest:
                if A.has_idea[i] and not any(A.has_idea[x] for x in rest):
                    problems.append(("warn", f"{here} would be left with no idea-holder."))
                if not A.has_idea[i] and all(A.has_idea[x] for x in rest):
                    problems.append(("warn", f"{here} would be left with everyone already having an idea."))
                if A.can_attend[i] and not any(A.can_attend[x] for x in rest):
                    problems.append(("warn", f"{here} would be left with no one who can attend the finals."))
                if len(rest) < C.MIN_TEAM_SIZE:
                    problems.append(("warn", f"{here} would drop to {len(rest)}, below the minimum of "
                                             f"{C.MIN_TEAM_SIZE}."))
        blocked = any(k == "block" for k, _ in problems)
        for k, msg in problems:
            (st.error if k == "block" else st.warning)(msg)
        ok = not problems or (not blocked and st.checkbox("Move anyway", key=f"anyway_{i}_{dest}"))
        if c3.button("Move", type="primary", width="stretch", disabled=blocked or not ok):
            if dest == "New team":
                dest = T.new_team(res)
            T.move_person(res, i, None if dest == "Unassigned" else dest)
            state.save_teams(res, ui.user()["email"], "Move person", f"{A.names[i]}: {here} -> {dest}",
                             manual_edits=(js or {}).get("manual_edits", 0) + 1)
            st.session_state["_toast"] = f"Moved {A.names[i]} to {dest}"
            st.rerun()
        if dest not in ("Unassigned", "New team"):
            t = next(t for t in res["teams"] if t["id"] == dest)
            st.caption(f"Fit with {dest}: {T.overall_fit(A, i, t['members']):.2f} on interests and time zone "
                       f"({len(t['members'])} members now).")


def render():
    if not state.has_data():
        ui.no_data()
        return
    v, tv = state.data_version(), state.teams_version()
    res, js = state.current_teams(v, tv)

    st.markdown("# Teams")
    if ui.can("admin"):
        _build_panel(js)
    _how_it_works()
    if not res:
        st.write("No teams have been built yet." + ("" if ui.can("admin") else " An admin can build them."))
        return

    tables = state.summarise(res)
    summ, mem, una = tables["Team summary"], tables["Team members"], tables["Unassigned"]
    A = res["A"]
    placed = int(mem.shape[0])
    flagged = int(summ["Check"].fillna("").str.len().gt(0).sum()) if "Check" in summ else 0
    st.caption(f"Built {js.get('built_at') or ''} by {js.get('built_by') or 'unknown'}. "
               f"{js.get('manual_edits', 0)} change(s) made by hand since.")
    if res.get("removed_keys"):
        n = len(res["removed_keys"])
        st.warning(f"{ui.people(n)} in these teams {'is' if n == 1 else 'are'} no longer included and "
                   f"{'has' if n == 1 else 'have'} been taken out. Rebuild, or move people to fill the gaps.")

    c = st.columns(5)
    c[0].metric("Teams", int((summ["Size"] > 0).sum()))
    c[1].metric("Placed", placed)
    c[2].metric("Unassigned", len(una), help="Included people not in a team; see the Unassigned tab for why")
    c[3].metric("Teams with flags", flagged, help="Teams with something in their Check column")
    c[4].metric("Average team size", f"{summ.loc[summ['Size'] > 0, 'Size'].mean():.1f}")

    t1, t2, t3 = st.tabs(["All teams", "Team detail", "Unassigned"])

    with t1:
        left, right = st.columns([1, 1.2], gap="large")
        with left:
            s = summ[summ["Size"] > 0]
            fig = px.scatter(s, x="Time zone match", y="Interest similarity", size="Size", color="Idea mix",
                             hover_name="Team", text="Team", size_max=26,
                             color_continuous_scale=[[0, charts.SKY], [0.5, "#B7C9C0"], [1, charts.AMBER]],
                             title="Each team: shared interests against time-zone closeness")
            fig.update_traces(textposition="top center", textfont_size=10)
            fig.update_layout(height=420, coloraxis_colorbar=dict(title="Idea mix", thickness=10))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        with right:
            col_key = {"Interest similarity": "interest_similarity", "Time zone match": "timezone_match",
                       "Background diversity": "background_diversity", "Skill coverage": "skill_coverage",
                       "Idea mix": "idea_mix", "Idea alignment": "idea_alignment"}
            cfg = {k: st.column_config.ProgressColumn(k, min_value=0, max_value=1, format="%.2f",
                                                      help=COMP_HELP[col_key[k]])
                   for k in col_key if k in summ}
            cfg["Idea-holders"] = st.column_config.NumberColumn("Idea-holders",
                                                                help="Members who said they already have an idea")
            cfg["Can attend finals"] = st.column_config.NumberColumn(
                "Can attend finals", help="Members who said they can attend the London finals in person")
            cfg["Time zones"] = st.column_config.TextColumn("Time zones",
                                                            help="Earliest and latest member time zone, and the gap")
            cfg["Check"] = st.column_config.TextColumn("Check", help="Things worth a look: skill gaps, missing "
                                                       "idea-holders, wide time-zone spread, finals attendance")
            st.dataframe(summ[["Team", "Size", "Idea-holders", "Can attend finals", "Interest similarity",
                               "Idea alignment", "Time zone match", "Background diversity", "Skill coverage",
                               "Idea mix", "Time zones", "Check"]],
                         hide_index=True, column_config=cfg, height=420)

    with t2:
        ids = summ["Team"].tolist()
        tid = st.selectbox("Team", ids, key="team_detail")
        t = next(t for t in res["teams"] if t["id"] == tid)
        m = t["members"]
        row = summ[summ["Team"] == tid].iloc[0]
        if not m:
            st.write("This team is empty. Move people into it below.")
        else:
            with T.settings(weights=res.get("weights")):
                comp = T.components(A, m)
                avg = {k: np.mean([T.components(A, x["members"])[k] for x in res["teams"] if x["members"]])
                       for k in COMP_LABELS}
            left, right = st.columns(2, gap="large")
            with left:
                st.plotly_chart(charts.radar(
                    [(tid, [comp[k] for k in COMP_LABELS], charts.GREEN),
                     ("Average team", [avg[k] for k in COMP_LABELS], charts.SLATE)],
                    list(COMP_LABELS.values()), "How this team scores on each criterion", height=400,
                    hover={COMP_LABELS[k]: COMP_HELP[k] for k in COMP_LABELS}),
                    width="stretch", config={"displayModeBar": False})
            with right:
                st.plotly_chart(charts.radar(
                    [(tid, A.skills[m].max(axis=0), charts.AMBER)],
                    [s.title() for s in T.SKILL_NAMES], "Strongest member on each skill", height=400,
                    hover=charts.skill_hover()),
                    width="stretch", config={"displayModeBar": False})
            st.write(row.get("Why this team", ""))
            if row.get("Check"):
                st.warning(row["Check"])
            tm = mem[mem["Team"] == tid].copy()
            tm["LinkedIn"] = tm["LinkedIn"].map(ui.url)
            st.dataframe(tm[["Name", "Has idea", "Idea", "Country", "Time zone", "Areas", "Keywords", "Top skills",
                             "Can attend finals", "Interest fit", "LinkedIn", "Email"]], hide_index=True,
                         column_config={"LinkedIn": st.column_config.LinkColumn("LinkedIn", display_text="Profile"),
                                        "Interest fit": st.column_config.ProgressColumn(
                                            "Interest fit", min_value=0, max_value=1, format="%.2f",
                                            help="This member's average interest similarity with their "
                                                 "teammates (0 to 1)")})
            emails = [e for e in tm["Email"] if e]
            e1, e2 = st.columns([1, 3])
            if emails:
                e1.link_button("Email this team", "mailto:?bcc=" + quote(",".join(emails)), width="stretch")
                with e2.expander("Copy email addresses"):
                    st.code(", ".join(emails), language=None)
        if ui.can("reviewer"):
            _move_panel(res, tables, js)

    with t3:
        if una.empty:
            st.write("Everyone included has a team.")
        else:
            st.write("People not placed, why, and the team with a spare place they'd fit best.")
            u = una.copy()
            u["LinkedIn"] = u["LinkedIn"].map(ui.url)
            st.dataframe(u.drop(columns=["_key"]), hide_index=True,
                         column_config={"LinkedIn": st.column_config.LinkColumn("LinkedIn", display_text="Profile")})
            if ui.can("reviewer"):
                if st.button("Place everyone in their closest team"):
                    moved = 0
                    for k in una["_key"]:
                        i = A.keys.index(k)
                        room = [t for t in res["teams"] if 0 < len(t["members"]) < C.MAX_TEAM_SIZE]
                        if not room:
                            break
                        best = max(room, key=lambda t: T.overall_fit(A, i, t["members"]))
                        T.move_person(res, i, best["id"])
                        moved += 1
                    state.save_teams(res, ui.user()["email"], "Place unassigned", f"{moved} people placed",
                                     manual_edits=(js or {}).get("manual_edits", 0) + moved)
                    st.session_state["_toast"] = f"Placed {moved} people"
                    st.rerun()

    st.divider()
    buf = BytesIO()
    T.write_workbook(tables, buf)
    d1, d2 = st.columns([1, 1])
    d1.download_button("Download teams (Excel)", buf.getvalue(), file_name="teams.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")
    d2.download_button("Download team members (CSV)", mem.drop(columns=["_key"]).to_csv(index=False).encode(),
                       file_name="team_members.csv", mime="text/csv", width="stretch")
