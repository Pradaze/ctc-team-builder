import pandas as pd
import streamlit as st

import config as C
import team_builder as T

from .. import auth, state, ui


def _fetch(read_cvs):
    key = state.secret("TALLY_API_KEY")
    form = state.secret("TALLY_FORM_ID") or C.TALLY_FORM_ID
    with st.status("Fetching applications from Tally…", expanded=True) as box:
        try:
            df = T.fetch_from_tally(form, key, log=box.write)
        except Exception as e:
            box.update(label="Fetch failed", state="error")
            st.error(str(e))
            return
        df = T.merge_previous(df, state.APPLICANTS, log=box.write)
        if read_cvs:
            bar = st.progress(0.0, text="Reading new CVs…")
            df, stats = T.read_cvs(df, log=box.write,
                                   progress=lambda n, total: bar.progress(n / max(total, 1), text=f"CV {n} of {total}"))
            bar.empty()
        state.save_raw(df, ui.user()["email"], "Fetch", f"{len(df)} submissions" + (", CVs read" if read_cvs else ""))
        box.update(label=f"Fetched {len(df)} submissions", state="complete", expanded=False)
    st.session_state["_toast"] = "Applications updated"
    st.rerun()


def _read_cvs(refresh):
    df = T.load_csv(state.APPLICANTS)
    bar = st.progress(0.0, text="Reading CVs…")
    with st.status("Reading CVs…", expanded=True) as box:
        df, stats = T.read_cvs(df, refresh=refresh, log=box.write,
                               progress=lambda n, total: bar.progress(n / max(total, 1), text=f"CV {n} of {total}"))
        box.update(label="CVs read", state="complete", expanded=False)
    bar.empty()
    state.save_raw(df, ui.user()["email"], "Read CVs",
                   ", ".join(f"{k}: {v}" for k, v in stats.most_common()) or "nothing new")
    st.session_state["_toast"] = "CVs read"
    st.rerun()


def render():
    st.markdown("# Admin")
    if not ui.can("admin"):
        st.write("Only admins can use this page.")
        return

    st.markdown("## Applications")
    if state.has_data():
        raw = state.raw(state.data_version())
        df, dups = state.people(state.data_version())
        cv_n = int((raw.get(T.CV_TEXT_COL, pd.Series(dtype=str)).fillna("") != "").sum())
        st.write(f"{len(raw)} submissions from {len(df)} people. CV text for {cv_n}. "
                 f"Last updated {state.last_updated():%d %b %Y at %H:%M}.")
    else:
        st.write("No applications yet.")

    form = state.secret("TALLY_FORM_ID") or C.TALLY_FORM_ID
    have_key = bool(state.secret("TALLY_API_KEY"))
    with st.container(border=True):
        st.markdown("#### Fetch from Tally")
        if not have_key or form in (None, "", "your-form-id"):
            st.warning("Add TALLY_API_KEY and TALLY_FORM_ID to the app's secrets to fetch from here.")
        st.write("Downloads every completed submission. Review marks, ratings, notes and CV text you already "
                 "have are kept.")
        cvs = st.checkbox("Also read CVs for new applicants", value=True)
        if st.button("Fetch new applications", type="primary", disabled=not have_key):
            _fetch(cvs)

    with st.container(border=True):
        st.markdown("#### Read CVs")
        st.write("Downloads each PDF CV and extracts its text for keywords and skills. CVs are read in memory; "
                 "only the text is stored.")
        c1, c2 = st.columns(2)
        if c1.button("Read CVs not yet read", disabled=not state.has_data()):
            _read_cvs(False)
        if c2.button("Re-read every CV", disabled=not state.has_data()):
            _read_cvs(True)

    with st.container(border=True):
        st.markdown("#### Upload or download the data")
        up = st.file_uploader("Upload an applicants.csv (for example the one from the command-line tool)",
                              type=["csv"])
        if up is not None and st.button("Use this file", type="primary"):
            new = T.tidy_columns(pd.read_csv(up, dtype=str, keep_default_na=False))
            missing = T.check_columns(new)
            if missing:
                st.error("This file is missing columns from config.py: " + "; ".join(map(str, missing)))
            else:
                if T.ID_COL in new.columns:
                    new = T.merge_previous(new, state.APPLICANTS, log=lambda *_: None)
                state.save_raw(new, ui.user()["email"], "Upload", f"{len(new)} rows from {up.name}")
                st.session_state["_toast"] = f"Loaded {len(new)} rows"
                st.rerun()
        if state.has_data():
            st.download_button("Download applicants.csv", state.APPLICANTS.read_bytes(),
                               file_name="applicants.csv", mime="text/csv",
                               help="Includes personal data and CV text. Store it securely.")

    st.markdown("## Access")
    acc = auth._access()
    st.write(f"You're signed in as {ui.user()['email']} ({ui.user()['role']}). "
             "Change who can get in by editing the [access] section of the app's secrets.")
    rows = []
    for role in ("admin", "reviewer", "viewer"):
        for e in acc.get(role + "s", []) or []:
            rows.append({"Who": e, "Role": role})
        for d in acc.get(role + "_domains", []) or []:
            rows.append({"Who": f"anyone @{d}", "Role": role})
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True)

    st.markdown("## Activity")
    st.dataframe(state.activity(), hide_index=True, height=320)
