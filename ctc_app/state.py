"""Data access for the dashboard: files on disk, caching, saving, activity log."""

import csv
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

import team_builder as T

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("CTC_DATA_DIR", ROOT / "data"))
APPLICANTS = DATA_DIR / "applicants.csv"
TEAMS = DATA_DIR / "teams.json"
AUDIT = DATA_DIR / "activity.csv"
_lock = threading.RLock()


# ---------------------------------------------------------------------------
# Secrets: st.secrets first, then environment variables
# ---------------------------------------------------------------------------
def secrets():
    try:
        return st.secrets
    except Exception:
        return {}


def secret(name, default=None):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, default)


# ---------------------------------------------------------------------------
# Versioning: caches are keyed on file modification times
# ---------------------------------------------------------------------------
def data_version():
    return APPLICANTS.stat().st_mtime_ns if APPLICANTS.exists() else 0


def teams_version():
    return TEAMS.stat().st_mtime_ns if TEAMS.exists() else 0


def has_data():
    return APPLICANTS.exists()


def last_updated():
    if not APPLICANTS.exists():
        return None
    return datetime.fromtimestamp(APPLICANTS.stat().st_mtime)


@st.cache_data(show_spinner=False, max_entries=3)
def raw(version):
    return T.load_csv(APPLICANTS)


@st.cache_data(show_spinner=False, max_entries=3)
def people(version):
    df, dups = T.people_table(raw(version))
    df = df.copy()
    df["_key"] = T.person_key(df)
    df["_status"] = [T.include_status(v) for v in df.get(T.INCLUDE_COL, [""] * len(df))]
    df["_included"] = [T.is_included(v) for v in df.get(T.INCLUDE_COL, [""] * len(df))]
    return df, dups


@st.cache_resource(show_spinner="Reading applications…", max_entries=3)
def applicants_all(version):
    df, _ = people(version)
    return T.Applicants(df.drop(columns=[c for c in df.columns if c.startswith("_")]))


@st.cache_resource(show_spinner="Preparing the included applicants…", max_entries=3)
def applicants_included(version):
    df, _ = people(version)
    inc = df[df["_included"]]
    if len(inc) == 0:
        return None
    return T.Applicants(inc.drop(columns=[c for c in inc.columns if c.startswith("_")]))


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
    raw.clear()
    people.clear()
    landscape.clear()


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def _atomic_write_csv(df, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)


def save_raw(df, user, action, detail=""):
    with _lock:
        _atomic_write_csv(df, APPLICANTS)
        log(user, action, detail)
    clear_caches()


def set_marks(keys, user, status=None, rating=None, notes=None):
    """Update review fields for people (all of their submissions)."""
    keys = set(keys)
    with _lock:
        df = T.load_csv(APPLICANTS)
        for c in (T.INCLUDE_COL, T.MOTIVATION_COL, T.NOTES_COL):
            if c not in df.columns:
                df[c] = ""
        rows = np.array([k in keys for k in T.person_key(df)])
        if status is not None:
            df.loc[rows, T.INCLUDE_COL] = {"Included": "yes", "Excluded": "no", "Not reviewed": ""}[status]
        if rating is not None:
            df.loc[rows, T.MOTIVATION_COL] = "" if rating in ("", None) else str(int(rating))
        if notes is not None:
            df.loc[rows, T.NOTES_COL] = notes
        _atomic_write_csv(df, APPLICANTS)
    what = ", ".join(x for x, v in (("status " + str(status), status), ("rating", rating), ("notes", notes))
                     if v is not None)
    log(user, "Review", f"{len(keys)} people: {what}")
    clear_caches()


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------
def load_teams():
    if not TEAMS.exists():
        return None
    try:
        return json.loads(TEAMS.read_text())
    except Exception:
        return None


def save_teams(res, user, action, detail="", **meta):
    old = load_teams() or {}
    js = T.result_to_json(res, **{
        "built_at": old.get("built_at"), "built_by": old.get("built_by"),
        "manual_edits": old.get("manual_edits", 0), **meta})
    with _lock:
        TEAMS.parent.mkdir(parents=True, exist_ok=True)
        tmp = TEAMS.with_suffix(".tmp")
        tmp.write_text(json.dumps(js, indent=1))
        os.replace(tmp, TEAMS)
        log(user, action, detail)
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
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    new = not AUDIT.exists()
    with _lock, open(AUDIT, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["When (UTC)", "Who", "Action", "Details"])
        w.writerow([datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"), user, action, detail])


def activity(limit=500):
    if not AUDIT.exists():
        return pd.DataFrame(columns=["When (UTC)", "Who", "Action", "Details"])
    df = pd.read_csv(AUDIT, dtype=str, keep_default_na=False)
    return df.iloc[::-1].head(limit)


def last_action(action):
    df = activity(2000)
    hit = df[df["Action"] == action]
    return None if hit.empty else hit.iloc[0].to_dict()
