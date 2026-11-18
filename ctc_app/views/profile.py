import numpy as np
import pandas as pd
import streamlit as st

import team_builder as T

from .. import charts, state, ui


@st.cache_data(ttl=900, max_entries=20, show_spinner="Loading the CV…")
def _cv_bytes(url):
    import requests
    for headers in ({}, {"Authorization": f"Bearer {state.secret('TALLY_API_KEY', '')}"}):
        try:
            r = requests.get(url, headers=headers, timeout=60)
        except Exception:
            continue
        if r.status_code == 200 and r.content[:4] == b"%PDF":
            return r.content
    return None


def _team_of(key):
    res, _ = state.current_teams(state.data_version(), state.teams_version())
    if not res:
        return None, []
    A = res["A"]
    for t in res["teams"]:
        names = [A.keys[i] for i in t["members"]]
        if key in names:
            return t["id"], [A.names[i] for i in t["members"] if A.keys[i] != key]
    return "Unassigned", []


def render():
    if not state.has_data():
        ui.no_data()
        return
    v = ui.version()
    df, dups = state.people(v)
    A = state.applicants_all(v)
    order = sorted(range(A.n), key=lambda i: A.names[i].lower())
    keys = [A.keys[i] for i in order]
    want = st.query_params.get("person") or st.session_state.get("profile_key")
    start = keys.index(want) if want in keys else 0

    top = st.columns([4, 1, 1])
    pos = top[0].selectbox("Applicant", range(len(keys)), index=start,
                           format_func=lambda p: ui.person_label(A, order[p]), label_visibility="collapsed")
    if top[1].button("Previous", width="stretch", disabled=pos == 0):
        st.session_state["profile_key"] = keys[pos - 1]
        st.rerun()
    if top[2].button("Next", width="stretch", disabled=pos == len(keys) - 1):
        st.session_state["profile_key"] = keys[pos + 1]
        st.rerun()
    i = order[pos]
    key = A.keys[i]
    st.session_state["profile_key"] = key
    row = df.iloc[i]

    st.markdown(f"# {A.names[i]}")
    bits = [A.country[i], T.fmt_offset(A.offset[i])]
    if A.degree_raw[i]:
        bits.append(A.degree_raw[i])
    st.write(", ".join(bits))
    tid, mates = _team_of(key)

    links = st.columns([1, 1, 1, 3])
    li = ui.url(A.linkedin[i])
    cv = ui.url(row.get(T.col("cv_file"), ""))
    if li:
        links[0].link_button("LinkedIn profile", li, width="stretch")
    else:
        links[0].caption(f"LinkedIn: {A.linkedin[i] or 'not given'}")
    if cv:
        links[1].link_button("Open CV", cv, width="stretch")
    if A.emails[i]:
        links[2].link_button("Email", f"mailto:{A.emails[i]}", width="stretch")
    if tid:
        links[3].write(f"Team: **{tid}**" + (f" with {', '.join(mates)}" if mates else ""))

    if any(d["Email"] == A.emails[i] for d in dups):
        st.info("This person applied more than once. You're seeing their latest application.")

    # ---- review controls
    with st.container(border=True):
        r1, r2 = st.columns([2, 1])
        current = row["_status"]
        editable = ui.can("reviewer")
        new_status = r1.segmented_control("Review", ui.STATUSES, default=current, disabled=not editable,
                                          key=f"st_{key}_{v}")
        rating_now = pd.to_numeric(row.get(T.MOTIVATION_COL, ""), errors="coerce")
        rating = r2.select_slider("Motivation rating", options=["None", 1, 2, 3, 4, 5],
                                  value="None" if pd.isna(rating_now) else int(rating_now),
                                  disabled=not editable, key=f"rt_{key}_{v}")
        notes = st.text_area("Notes", value=row.get(T.NOTES_COL, ""), disabled=not editable,
                             key=f"nt_{key}_{v}", height=80)
        if editable:
            dirty = (new_status and new_status != current) or notes != row.get(T.NOTES_COL, "") or \
                (("" if rating == "None" else rating) != ("" if pd.isna(rating_now) else int(rating_now)))
            if st.button("Save review", type="primary", disabled=not dirty):
                state.set_marks([key], ui.user()["email"], status=new_status or current,
                                rating="" if rating == "None" else rating, notes=notes)
                st.session_state["_toast"] = f"Saved review for {A.names[i]}"
                st.rerun()

    tabs = st.tabs(["Application", "Skills and keywords", "Similar applicants", "CV", "All answers"])

    with tabs[0]:
        bio_col = T.cols("interest_text")[0] if T.cols("interest_text") else None
        st.markdown("#### Bio")
        st.write(row.get(bio_col, "") or "No bio given.")
        st.markdown("#### Idea")
        idea_detail = row.get(T.col("idea_status") + T.DETAILS, "") if T.col("idea_status") else ""
        st.write(("Has an idea. " if A.has_idea[i] else "No idea yet. ") + (idea_detail or ""))
        st.markdown("#### Areas of interest")
        st.write(", ".join(A.tags[i]) or "None selected")
        other = [c for c in T.cols("interest_text")[2:] if row.get(c)]
        for c in other:
            st.caption(row.get(c))
        st.markdown("#### Finals in London")
        st.write(A.avail_raw[i] or "Not answered")

    with tabs[1]:
        left, right = st.columns([1.1, 1])
        with left:
            axes = [s.title() for s in T.SKILL_NAMES]
            fig = charts.radar([("This applicant", A.skills[i], charts.GREEN),
                                ("Everyone", A.skills.mean(axis=0), charts.SLATE)],
                               axes, "Skills found in their bio and CV", height=400,
                               hover=charts.skill_hover())
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        with right:
            st.markdown("#### Keywords")
            st.pills("Keywords", A.keywords[i] or ["None found"], label_visibility="collapsed",
                     key=f"kw_{key}", disabled=True)
            st.markdown("#### Background")
            st.write(f"Programme category: {A.degree_cat[i]}")
            st.write(f"Main field from CV and bio: {A.cv_field[i]}")
            st.write("CV text read" if A.has_cv[i] else "No CV text (not a PDF, scanned, or not read yet)")

    with tabs[2]:
        sims = A.S_int[i].copy()
        sims[i] = -1
        best = np.argsort(-sims)[:10]
        rows = []
        for j in best:
            gap = None
            if A.offset[i] is not None and A.offset[j] is not None:
                gap = abs(A.offset[i] - A.offset[j])
                gap = min(gap, 24 - gap)
            rows.append({
                "_key": A.keys[j], "Name": A.names[j], "Similarity": round(float(sims[j]), 2),
                "Shared areas": ", ".join(sorted(set(A.tags[i]) & set(A.tags[j]))),
                "Shared keywords": ", ".join(sorted(set(A.keywords[i]) & set(A.keywords[j]))),
                "Country": A.country[j], "Hours apart": gap,
                "Idea": "Yes" if A.has_idea[j] else "No", "Status": df.iloc[j]["_status"],
            })
        sim = pd.DataFrame(rows)
        st.caption("Closest matches by interests (areas, bio, idea and CV keywords). "
                   "Useful when placing someone by hand.")
        ev = st.dataframe(sim.drop(columns=["_key"]), hide_index=True, on_select="rerun",
                          selection_mode="single-row", key=f"sim_{key}",
                          column_config={"Similarity": st.column_config.ProgressColumn(
                              "Similarity", min_value=0, max_value=1, format="%.2f")})
        if ev.selection.rows:
            j = ev.selection.rows[0]
            if st.button(f"Open {sim.iloc[j]['Name']}'s profile"):
                ui.open_profile(sim.iloc[j]["_key"])

    with tabs[3]:
        if not cv:
            st.write("No CV was uploaded.")
        else:
            show = st.toggle("Show the CV here", key=f"cvt_{key}")
            if show:
                data = _cv_bytes(cv)
                if data is None:
                    st.warning("The CV couldn't be loaded here. The link may have expired; "
                               "an admin can fetch fresh links. You can still try Open CV above.")
                else:
                    try:
                        st.pdf(data, height=720)
                    except Exception:
                        st.download_button("Download CV", data, file_name=f"CV - {A.names[i]}.pdf",
                                           mime="application/pdf")
            text = row.get(T.CV_TEXT_COL, "")
            if text:
                with st.expander("Text extracted from the CV"):
                    st.write(text)

    with tabs[4]:
        for c in ui.answer_columns(df):
            val = row.get(c, "")
            if val:
                st.markdown(f"**{c}**")
                st.write(val)
