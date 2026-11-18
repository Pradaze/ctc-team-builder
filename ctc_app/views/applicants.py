import pandas as pd
import streamlit as st

import team_builder as T

from .. import state, ui


def _table(df, A):
    rows = []
    for i in range(A.n):
        r = df.iloc[i]
        rows.append({
            "_key": A.keys[i],
            "Status": r["_status"],
            "Rating": pd.to_numeric(r.get(T.MOTIVATION_COL, ""), errors="coerce"),
            "Notes": r.get(T.NOTES_COL, ""),
            "Name": A.names[i],
            "Country": A.country[i],
            "Time zone": T.fmt_offset(A.offset[i]),
            "_offset": A.offset[i],
            "Idea": "Yes" if A.has_idea[i] else "No",
            "Areas": ", ".join(A.tags[i]),
            "Keywords": ", ".join(A.keywords[i]),
            "Programme": A.degree_raw[i],
            "CV read": bool(A.has_cv[i]),
            "Finals": A.avail_raw[i],
            "LinkedIn": ui.url(A.linkedin[i]),
            "CV": ui.url(r.get(T.col("cv_file"), "")),
            "Email": A.emails[i],
        })
    return pd.DataFrame(rows)


def _bulk(keys, status, current):
    """Apply a status to everyone shown straight away, keeping their previous
    statuses so the change can be undone."""
    st.session_state["_undo"] = {"prev": {k: current[k] for k in keys}, "status": status}
    state.set_marks(keys, ui.user()["email"], status=status)
    st.session_state["_toast"] = f"{ui.people(len(keys))} set to {status}"
    st.rerun()


def _undo_bar():
    undo = st.session_state.get("_undo")
    if not undo:
        return
    with st.container(border=True):
        c1, c2, c3 = st.columns([3, 1, 1], vertical_alignment="center")
        c1.write(f"Set {ui.people(len(undo['prev']))} to {undo['status']}.")
        if c2.button("Undo", width="stretch"):
            by_status = {}
            for k, s in undo["prev"].items():
                by_status.setdefault(s, []).append(k)
            for s, ks in by_status.items():
                state.set_marks(ks, ui.user()["email"], status=s)
            st.session_state.pop("_undo")
            st.session_state["_toast"] = "Change undone"
            st.rerun()
        if c3.button("Dismiss", width="stretch"):
            st.session_state.pop("_undo")
            st.rerun()


def render():
    if not state.has_data():
        ui.no_data()
        return
    v = ui.version()
    df, _ = state.people(v)
    A = state.applicants_all(v)
    full = _table(df, A)

    st.markdown("# Applicants")
    st.caption(ui.status_note())

    f1, f2, f3 = st.columns([2, 1.3, 1.3])
    q = f1.text_input("Search names, emails, countries or keywords", placeholder="e.g. solar, Kenya, battery")
    stat = f2.pills("Status", ui.STATUSES, selection_mode="multi")
    idea = f3.pills("Idea", ["Yes", "No"], selection_mode="multi")
    g1, g2, g3 = st.columns([2, 1.3, 1.3])
    areas = sorted({t for ts in A.tags for t in ts})
    pick = g1.multiselect("Areas of interest", areas)
    offs = [o for o in A.offset if o is not None]
    lo, hi = (min(offs), max(offs)) if offs else (0.0, 0.0)
    tz = g2.slider("Time zone (UTC offset)", float(lo), float(hi), (float(lo), float(hi)), step=0.5) \
        if offs and lo < hi else None
    flags = g3.pills("Show only", ["CV read", "No CV text", "Can't attend finals"], selection_mode="multi")

    t = full
    if q:
        ql = q.lower()
        hay = (t["Name"] + " " + t["Email"] + " " + t["Country"] + " " + t["Keywords"] + " " + t["Areas"]).str.lower()
        t = t[hay.str.contains(ql, regex=False)]
    if stat:
        t = t[t["Status"].isin(stat)]
    if idea:
        t = t[t["Idea"].isin(idea)]
    if pick:
        t = t[t["Areas"].apply(lambda s: any(a in s.split(", ") for a in pick))]
    if tz:
        t = t[t["_offset"].isna() | t["_offset"].between(tz[0], tz[1])]
    if flags:
        if "CV read" in flags:
            t = t[t["CV read"]]
        if "No CV text" in flags:
            t = t[~t["CV read"]]
        if "Can't attend finals" in flags:
            t = t[t["Finals"].str.lower().str.startswith("no")]

    if ui.can("reviewer"):
        _undo_bar()
    st.write(f"Showing {len(t)} of {len(full)} people.")
    editable = ui.can("reviewer")
    shown = t.drop(columns=["_offset"]).set_index("_key")
    cfg = {
        "Status": st.column_config.SelectboxColumn("Status", options=ui.STATUSES, required=True, width="small"),
        "Rating": st.column_config.NumberColumn("Rating", min_value=1, max_value=5, step=1, width="small",
                                                help="Optional 1-5 motivation rating; feeds the commitment score"),
        "Notes": st.column_config.TextColumn("Notes", width="medium"),
        "Name": st.column_config.TextColumn("Name", width="medium"),
        "CV read": st.column_config.CheckboxColumn("CV read", width="small"),
        "LinkedIn": st.column_config.LinkColumn("LinkedIn", display_text="Profile", width="small"),
        "CV": st.column_config.LinkColumn("CV", display_text="Open CV", width="small"),
        "Keywords": st.column_config.TextColumn("Keywords", width="large"),
        "Areas": st.column_config.TextColumn("Areas", width="medium"),
    }
    locked = [c for c in shown.columns if c not in ("Status", "Rating", "Notes")]
    edited = st.data_editor(shown, column_config=cfg, disabled=locked if editable else True,
                            hide_index=True, height=520, key=f"ed_{v}_{len(shown)}")

    if editable:
        changed = []
        for key in shown.index:
            a, b = shown.loc[key], edited.loc[key]
            ra = None if pd.isna(a["Rating"]) else int(a["Rating"])
            rb = None if pd.isna(b["Rating"]) else int(b["Rating"])
            if a["Status"] != b["Status"] or ra != rb or (a["Notes"] or "") != (b["Notes"] or ""):
                changed.append((key, b["Status"], rb, b["Notes"] or ""))
        c1, c2, c3, c4 = st.columns([1.2, 1, 1, 1])
        if c1.button(f"Save {len(changed)} change(s)" if changed else "No unsaved changes",
                     type="primary", disabled=not changed):
            for key, s, r, n in changed:
                state.set_marks([key], ui.user()["email"], status=s, rating=r if r is not None else "", notes=n)
            st.session_state["_toast"] = f"Saved changes for {ui.people(len(changed))}"
            st.rerun()
        keys = list(shown.index)
        current = shown["Status"].to_dict()
        for col_, label, status in ((c2, "Include all shown", "Included"), (c3, "Exclude all shown", "Excluded"),
                                    (c4, "Clear review for shown", "Not reviewed")):
            if col_.button(label, disabled=not keys):
                _bulk(keys, status, current)

    st.divider()
    o1, o2 = st.columns([3, 1])
    if len(t):
        names = dict(zip(t["_key"], t["Name"] + " (" + t["Country"] + ")"))
        k = o1.selectbox("Open a full profile", list(names), format_func=names.get)
        o2.write("")
        if o2.button("Open profile", width="stretch"):
            ui.open_profile(k)
    st.download_button("Download this list (CSV)",
                       t.drop(columns=["_offset", "_key"]).to_csv(index=False).encode("utf-8"),
                       file_name="applicants_filtered.csv", mime="text/csv")
