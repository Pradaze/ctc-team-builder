"""Data access for the dashboard: storage, caching, saving, activity log.

Data lives in a store (see store.py): local files, or a Postgres database when
DATABASE_URL is set. Three things are stored:

- applicants.csv  the Tally submissions plus CV text (large, changes on fetch)
- reviews.json    include/exclude, rating and notes per person (small, changes often)
- teams.json      the current teams

Keeping reviews separate means a review click rewrites a few kilobytes, not
the whole applications file.
"""

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

import team_builder as T

from .store import csv_bytes, make_store

ROOT = Path(__file__).resolve().parents[1]


def engine_version():
    """Changes whenever team_builder.py or config.py changes, so objects cached
    by an older version of the code are never reused after an update. Checked
    on every page load (a cheap file-time lookup)."""
    h = hashlib.md5()
    for f in ("team_builder.py", "config.py"):
        st_ = (ROOT / f).stat()
        h.update(f"{f}:{st_.st_mtime_ns}:{st_.st_size}".encode())
    return h.hexdigest()[:10]
APPLICANTS, REVIEWS, TEAMS = "applicants.csv", "reviews.json", "teams.json"
_lock = threading.RLock()


# ---------------------------------------------------------------------------
# Secrets: st.secrets first, then environment variables
# ---------------------------------------------------------------------------
def secret(name, default=None):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, default)


@st.cache_resource
def _store(url, folder):
    return make_store(url, folder)


def store():
    return _store(secret("DATABASE_URL"), os.environ.get("CTC_DATA_DIR", str(ROOT / "data")))


# ---------------------------------------------------------------------------
# Versions: caches are keyed on these, so a change anywhere refreshes them
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3, show_spinner=False)
def _versions():
    # one round trip for all three; cached briefly so a page that asks several
    # times doesn't query the database each time (writes clear it at once)
    return store().versions([APPLICANTS, REVIEWS, TEAMS])


def data_version():
    return _versions()[:2] + (engine_version(),)


def teams_version():
    return _versions()[2]


def has_data():
    return data_version()[0] != 0


def last_updated():
    t = store().updated_at(APPLICANTS)
    return t.astimezone() if t else None


def _read_csv(data):
    df = pd.read_csv(StringIO(data.decode("utf-8")), dtype=str, keep_default_na=False)
    return T.tidy_columns(df)


def reviews():
    data = store().read(REVIEWS)
    return json.loads(data) if data else {}


def _apply_reviews(df, rv):
    for c in (T.INCLUDE_COL, T.MOTIVATION_COL, T.NOTES_COL):
        if c not in df.columns:
            df[c] = ""
    if rv:
        keys = T.person_key(df)
        for col, field in ((T.INCLUDE_COL, "include"), (T.MOTIVATION_COL, "rating"), (T.NOTES_COL, "notes")):
            vals = [rv[k][field] if k in rv and field in rv[k] else None for k in keys]
            df[col] = [v if v is not None else old for v, old in zip(vals, df[col])]
    return df


def load_applicants(with_reviews=True):
    """Fresh (uncached) copy of all submissions."""
    data = store().read(APPLICANTS)
    if data is None:
        return None
    df = _read_csv(data)
    return _apply_reviews(df, reviews()) if with_reviews else df


@st.cache_data(show_spinner=False, max_entries=3)
def raw(version):
    return load_applicants()


@st.cache_data(show_spinner=False, max_entries=3)
def people(version):
    df, dups = T.people_table(raw(version))
    df = df.copy()
    df["_key"] = T.person_key(df)
    df["_status"] = [T.include_status(v) for v in df[T.INCLUDE_COL]]
    df["_included"] = [T.is_included(v) for v in df[T.INCLUDE_COL]]
    return df, dups


def _features_frame(df):
    return df.drop(columns=[c for c in df.columns if c.startswith("_")])


@st.cache_resource(show_spinner="Reading applications…", max_entries=3)
def applicants_all(version):
    df, _ = people(version)
    return T.Applicants(_features_frame(df))


@st.cache_resource(show_spinner="Preparing the included applicants…", max_entries=3)
def applicants_included(version):
    df, _ = people(version)
    inc = df[df["_included"]]
    return T.Applicants(_features_frame(inc)) if len(inc) else None


@st.cache_data(show_spinner="Mapping the applicants…", max_entries=3)
def landscape(version):
    """2-D map of applicants where nearby means similar interests (t-SNE on the
    interest-similarity matrix; keeps local neighbourhoods, so clusters show)."""
    import warnings
    from sklearn.manifold import TSNE
    A = applicants_all(version)
    if A.n < 5:
        return np.zeros((A.n, 2))
    D = np.clip(1 - A.S_int, 0, 1)
    D = (D + D.T) / 2
    np.fill_diagonal(D, 0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tsne = TSNE(n_components=2, metric="precomputed", init="random", random_state=7,
                    perplexity=max(5, min(20, (A.n - 1) // 4)))
        return tsne.fit_transform(D)


def clear_caches():
    _versions.clear()
    raw.clear()
    people.clear()
    landscape.clear()


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def save_applicants(df, user, action, detail=""):
    """Save submissions. Review marks found in the file are moved into
    reviews.json for anyone who hasn't been reviewed in the dashboard yet."""
    with _lock:
        rv = reviews()
        changed = False
        for key, row in zip(T.person_key(df), df.to_dict("records")):
            if key in rv:
                continue
            marks = {f: row.get(c, "") for f, c in (("include", T.INCLUDE_COL), ("rating", T.MOTIVATION_COL),
                                                     ("notes", T.NOTES_COL))}
            if any(marks.values()):
                rv[key] = marks
                changed = True
        store().write(APPLICANTS, csv_bytes(df))
        if changed:
            store().write(REVIEWS, json.dumps(rv).encode())
        log(user, action, detail)
    clear_caches()


def set_marks(keys, user, status=None, rating=None, notes=None):
    """Update review fields for people (by person key)."""
    with _lock:
        rv = reviews()
        for k in keys:
            r = rv.setdefault(k, {})
            if status is not None:
                r["include"] = {"Included": "yes", "Excluded": "no", "Not reviewed": ""}[status]
            if rating is not None:
                r["rating"] = "" if rating in ("", None) else str(int(rating))
            if notes is not None:
                r["notes"] = notes
        store().write(REVIEWS, json.dumps(rv).encode())
    what = ", ".join(x for x, v in (("status " + str(status), status), ("rating", rating), ("notes", notes))
                     if v is not None)
    log(user, "Review", f"{len(keys)} people: {what}")
    clear_caches()


def applicants_csv_bytes():
    """The full applications file with current review marks, for download."""
    df = load_applicants()
    return csv_bytes(df) if df is not None else b""


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------
def load_teams():
    data = store().read(TEAMS)
    if not data:
        return None
    try:
        return json.loads(data)
    except Exception:
        return None


def save_teams(res, user, action, detail="", **meta):
    old = load_teams() or {}
    js = T.result_to_json(res, **{
        "built_at": old.get("built_at"), "built_by": old.get("built_by"),
        "manual_edits": old.get("manual_edits", 0), **meta})
    with _lock:
        store().write(TEAMS, json.dumps(js).encode())
        log(user, action, detail)
    _versions.clear()
    return js


def current_teams(version, tversion):
    """The saved teams rebuilt against the current included applicants."""
    js = load_teams()
    A = applicants_included(version)
    if js is None or A is None:
        return None, js
    with T.settings(weights=js.get("weights"), sizes=tuple(js.get("sizes", (3, 5, 6)))):
        res = T.result_from_json(js, A)
    return res, js


def summarise(res):
    with T.settings(weights=res.get("weights"), sizes=tuple(res.get("sizes", (3, 5, 6)))):
        return T.summarise(res)


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------
def log(user, action, detail=""):
    try:
        store().log(user, action, detail)
    except Exception:
        pass  # never block a save because the log failed


def activity(limit=500):
    from .store import ACTIVITY_COLUMNS
    return pd.DataFrame(store().activity(limit), columns=ACTIVITY_COLUMNS)


def last_action(action):
    df = activity(2000)
    hit = df[df["Action"] == action]
    return None if hit.empty else hit.iloc[0].to_dict()


def storage_label():
    return store().kind
