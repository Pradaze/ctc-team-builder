#!/usr/bin/env python3
"""
LBS Cleantech Challenge 2027 - team builder.

Commands:
  python team_builder.py fetch [--cvs]          pull applications from Tally into applicants.csv
  python team_builder.py cvs applicants.csv     read CV PDFs into a cv_text column
  python team_builder.py check applicants.csv   confirm config.py matches your data
  python team_builder.py build applicants.csv   form teams and write teams.xlsx

Method (settings in config.py):
  0. Clean-up: name untitled columns, keep each person's latest submission.
  1. Features: interests (tags + bio + CV keywords), time zone, background,
     skills and commitment, with keywords extracted by TF-IDF.
  2. Pools: hierarchical clustering on interest + time-zone similarity.
  3. Seeding: every team gets one idea-holder and one person without an idea.
  4. Greedy fill, then swaps/moves between teams while the total score rises.
  5. Leave-out: the weakest interest fits (capped) go to an Unassigned list.
  6. Output: Excel workbook for human review.
"""

import argparse
import json
import math
import os
import re
import sys
import threading
import time
import unicodedata
import warnings
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402
from countries import COUNTRIES  # noqa: E402

INCLUDE_COL = "include"
MOTIVATION_COL = "motivation_rating"
ID_COL = "Submission ID"
CV_TEXT_COL = "cv_text"
NOTES_COL = "notes"
MANUAL_COLS = (INCLUDE_COL, MOTIVATION_COL, NOTES_COL, CV_TEXT_COL)   # kept across fetches
FALSY = {"no", "n", "0", "false", "exclude", "excluded"}
DETAILS = " (details)"
TRUTHY = {"yes", "y", "1", "true", "x", "✓", "✔"}
SKILL_NAMES = list(C.SKILLS.keys())
API_BASE = "https://api.tally.so"


# ===========================================================================
# Helpers
# ===========================================================================
def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def fold(s):
    """Lower-case and strip accents: 'España' -> 'espana'."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return norm(s.lower().replace("&", " and ").replace("–", "-").replace("—", "-"))


def norm_key(s):
    return re.sub(r"\s+", "", fold(s))


def phrase_regex(kw):
    return r"(?<!\w)" + re.escape(fold(kw)) + r"(?!\w)"


def keyword_hits(text, keywords):
    text = fold(text)
    if not text:
        return 0
    return sum(1 for kw in keywords if re.search(phrase_regex(kw), text))


def first_match(text, categories, default="Other"):
    for cat, kws in categories.items():
        if keyword_hits(text, kws):
            return cat
    return default


def most_hits(text, categories, default="Other"):
    best, best_n = default, 0
    for cat, kws in categories.items():
        n = keyword_hits(text, kws)
        if n > best_n:
            best, best_n = cat, n
    return best


def split_tags(val):
    val = norm(val)
    if not val:
        return []
    sep = ";" if ";" in val else ","
    return [t.strip() for t in val.split(sep) if t.strip()]


def col(name):
    return C.COLUMNS.get(name)


def cols(name):
    v = C.COLUMNS.get(name)
    if not v:
        return []
    return v if isinstance(v, list) else [v]


def get(df, name, default=""):
    c = col(name)
    if c and c in df.columns:
        return df[c].fillna("").astype(str)
    return pd.Series([default] * len(df), index=df.index)


def joined(df, names, sep=" \n "):
    parts = [df[c].fillna("").astype(str) for c in names if c and c in df.columns]
    if not parts:
        return pd.Series([""] * len(df), index=df.index)
    out = parts[0]
    for p in parts[1:]:
        out = out + sep + p
    return out.str.strip()


def fmt_offset(h):
    if h is None or (isinstance(h, float) and np.isnan(h)):
        return "unknown"
    sign = "+" if h >= 0 else "-"
    h = abs(h)
    hh, mm = int(h), int(round((h - int(h)) * 60))
    return f"UTC{sign}{hh}" + (f":{mm:02d}" if mm else "")


# ===========================================================================
# 0. Clean-up: column names and repeat submissions
# ===========================================================================
def looks_like_id(c):
    return bool(re.fullmatch(r"[A-Za-z0-9]{6}", c)) and (
        any(ch.isdigit() for ch in c) or (c != c.lower() and c != c.upper()))


def tidy_columns(df):
    """Rename untitled Tally fields (shown as IDs like 'DXoxW5') after the
    question they follow: '<previous question> (details)'."""
    new, prev = [], None
    for c in df.columns:
        if looks_like_id(c) and prev:
            name = prev + DETAILS
            k = 2
            while name in new:
                name = f"{prev} (details {k})"
                k += 1
            new.append(name)
        else:
            new.append(c)
            if c not in (INCLUDE_COL, MOTIVATION_COL):
                prev = c
    df.columns = new
    return df


def load_csv(path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    return tidy_columns(df)


def include_status(mark):
    m = norm(mark).lower()
    if m in TRUTHY:
        return "Included"
    if m in FALSY:
        return "Excluded"
    return "Not reviewed"


def is_included(mark):
    st = include_status(mark)
    return st == "Included" or (st == "Not reviewed" and getattr(C, "INCLUDE_BY_DEFAULT", False))


def person_key(df):
    email = get(df, "email").str.strip().str.lower()
    name = get(df, "name").map(fold)
    return np.where(email != "", email, "name:" + name)


def dedupe(df):
    """Keep each person's latest submission (matched by email). Their include
    mark counts if it's on any of their submissions."""
    if df.empty:
        return df, []
    df = df.copy()
    df["_key"] = person_key(df)
    ts = col("submitted_at")
    df["_ts"] = df[ts] if ts in df.columns else ""
    df["_order"] = range(len(df))
    df = df.sort_values(["_ts", "_order"])
    report, keep_rows = [], []
    for key, g in df.groupby("_key", sort=False):
        last = g.iloc[-1].copy()
        if len(g) > 1:
            for c in (INCLUDE_COL, MOTIVATION_COL, NOTES_COL, CV_TEXT_COL):
                if c in g.columns and not norm(last[c]):
                    filled = [v for v in g[c] if norm(v)]
                    if filled:
                        last[c] = filled[-1]
            report.append({
                "Name": last.get(col("name"), ""), "Email": last.get(col("email"), ""),
                "Submissions": len(g),
                "Kept (latest)": last.get(ID_COL, ""),
                "Dropped": ", ".join(str(x) for x in g[ID_COL].iloc[:-1]) if ID_COL in g else "",
            })
        keep_rows.append(last)
    out = pd.DataFrame(keep_rows).sort_values("_order").drop(columns=["_key", "_ts", "_order"])
    return out.reset_index(drop=True), report


# ===========================================================================
# Time zones
# ===========================================================================
def _build_alias_index():
    exact, searchable = {}, []
    for code, (name, offset, aliases) in COUNTRIES.items():
        for a in [name, code.lower()] + aliases:
            f = fold(a)
            exact.setdefault(f, code)
            if len(f) > 3:
                searchable.append((f, code))
    searchable.sort(key=lambda x: -len(x[0]))
    return exact, searchable


ALIAS_EXACT, ALIAS_SEARCH = _build_alias_index()


def resolve_location(text, code=""):
    """-> (country name, UTC offset or None, how it was found)."""
    f = fold(text)
    overrides = {fold(k): v for k, v in getattr(C, "TIMEZONE_OVERRIDES", {}).items()}
    if f in overrides:
        v = overrides[f]
        if isinstance(v, (int, float)):
            return norm(text), float(v), "override"
        if str(v).upper() in COUNTRIES:
            name, off, _ = COUNTRIES[str(v).upper()]
            return name, float(off), "override"
    if f:
        if f in ALIAS_EXACT:
            name, off, _ = COUNTRIES[ALIAS_EXACT[f]]
            return name, float(off), "answer"
        for alias, c in ALIAS_SEARCH:
            if re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", f):
                name, off, _ = COUNTRIES[c]
                return name, float(off), "answer"
    code = norm(code).upper()
    if code in COUNTRIES:
        name, off, _ = COUNTRIES[code]
        return name, float(off), "country code"
    return norm(text) or "Unknown", None, "not recognised"


def tz_similarity(offsets):
    o = np.array([np.nan if v is None else v for v in offsets], dtype=float)
    gap = np.abs(o[:, None] - o[None, :])
    gap = np.minimum(gap, 24 - gap)
    full, zero = C.TZ_FULL_MATCH_HOURS, C.TZ_ZERO_MATCH_HOURS
    S = np.clip(1 - (gap - full) / max(zero - full, 1e-9), 0, 1)
    S = np.where(np.isnan(gap), C.TZ_UNKNOWN_SCORE, S)
    np.fill_diagonal(S, 1.0)
    return S


# ===========================================================================
# 1. FETCH from Tally, and CV reading
# ===========================================================================
def render_answer(answer, formatted):
    if answer is None:
        return ""
    if isinstance(answer, list):
        if answer and isinstance(answer[0], dict):
            urls = [a.get("url") for a in answer if isinstance(a, dict) and a.get("url")]
            if urls:
                return "; ".join(urls)
            return "; ".join(norm(a.get("name") or a.get("text") or json.dumps(a)) for a in answer)
        return "; ".join(norm(a) for a in answer)
    if formatted:
        return norm(formatted)
    if isinstance(answer, dict):
        return json.dumps(answer)
    return norm(answer)


def pdf_text(content, max_chars=30000):
    import logging
    from io import BytesIO
    from pypdf import PdfReader
    # pypdf reports every small formatting fault it repairs in a PDF; these are
    # harmless (the text still comes out), so only show genuine errors.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = PdfReader(BytesIO(content))
    text = " ".join((page.extract_text() or "") for page in reader.pages)
    return norm(text)[:max_chars]


def download_cv(url, api_key=None):
    """-> (text, status). Tries the signed link as-is, then with the API key."""
    import requests
    tries = [{}]
    if api_key:
        tries.append({"Authorization": f"Bearer {api_key}"})
    last = "failed"
    for headers in tries:
        try:
            r = requests.get(url, headers=headers, timeout=60)
        except Exception as e:
            last = f"error: {e.__class__.__name__}"
            continue
        if r.status_code in (401, 403):
            last = f"refused ({r.status_code})"
            continue
        if r.status_code != 200:
            return "", f"HTTP {r.status_code}"
        if r.content[:4] != b"%PDF" and "pdf" not in r.headers.get("content-type", ""):
            return "", "not a PDF"
        try:
            text = pdf_text(r.content)
        except Exception as e:
            return "", f"unreadable PDF ({e.__class__.__name__})"
        return text, "ok" if text else "no text (scanned image?)"
    return "", last


def read_cvs(df, refresh=False, log=print, progress=None):
    cv_col = col("cv_file")
    if not cv_col or cv_col not in df.columns:
        log("No CV column configured/found; nothing to read.")
        return df, Counter()
    if CV_TEXT_COL not in df.columns:
        df[CV_TEXT_COL] = ""
    key = os.environ.get("TALLY_API_KEY")
    todo = [i for i in df.index if norm(df.at[i, cv_col]) and (refresh or not norm(df.at[i, CV_TEXT_COL]))]
    log(f"Reading {len(todo)} CVs (in memory only, nothing saved to disk)...")
    stats = Counter()
    for n, i in enumerate(todo, 1):
        url = split_tags(df.at[i, cv_col])[0]
        text, status = download_cv(url, key)
        stats[status.split(" (")[0]] += 1
        if text:
            df.at[i, CV_TEXT_COL] = text
        if progress:
            progress(n, len(todo))
        elif n % 20 == 0:
            log(f"  {n}/{len(todo)}")
        time.sleep(0.2)
    log("CV results: " + ", ".join(f"{k}: {v}" for k, v in stats.most_common()))
    if stats.get("refused", 0) > len(todo) / 2:
        log("Most CV links were refused - they may have expired. Run `fetch --cvs` for fresh links.")
    return df, stats


def fetch_from_tally(form_id, key, log=print):
    """Download every completed submission -> DataFrame (one row per submission)."""
    import requests
    if not key:
        raise RuntimeError("TALLY_API_KEY is not set.")
    if not form_id or form_id == "your-form-id":
        raise RuntimeError("Set TALLY_FORM_ID in config.py.")
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {key}", "Accept": "application/json"})
    questions, submissions, page = {}, [], 1
    while True:
        r = s.get(f"{API_BASE}/forms/{form_id}/submissions",
                  params={"page": page, "limit": 500, "filter": "completed"}, timeout=60)
        if r.status_code in (401, 403):
            raise RuntimeError("Tally rejected the API key (check it, and that it can access this form).")
        if r.status_code == 404:
            raise RuntimeError(f"Form {form_id} not found.")
        r.raise_for_status()
        data = r.json()
        for q in data.get("questions", []):
            if not q.get("isDeleted"):
                questions.setdefault(q["id"], q)
        submissions.extend(data.get("submissions", []))
        log(f"  page {page}: {len(submissions)} submissions so far")
        if not data.get("hasMore"):
            break
        page += 1
        time.sleep(0.6)

    titles, used, prev = {}, set(), None
    for qid, q in questions.items():
        t = norm(q.get("title"))
        if not t:
            t = (prev + DETAILS) if prev else qid
        else:
            prev = t
        base, k = t, 2
        while t in used:
            t = f"{base} ({k})"
            k += 1
        used.add(t)
        titles[qid] = t

    rows = []
    for sub in submissions:
        row = {ID_COL: sub.get("id"), "Submitted at": sub.get("submittedAt", "")}
        for resp in sub.get("responses", []):
            if resp.get("questionId") in titles:
                row[titles[resp["questionId"]]] = render_answer(resp.get("answer"), resp.get("formattedAnswer"))
        rows.append(row)
    df = pd.DataFrame(rows, columns=[ID_COL, "Submitted at"] + list(titles.values())).fillna("")
    df.insert(0, MOTIVATION_COL, "")
    df.insert(0, INCLUDE_COL, "")
    return df


def merge_previous(df, out, log=print):
    """Carry the manual columns (include, rating, notes, CV text) over from an
    earlier applicants.csv, matched by submission ID."""
    out = Path(out)
    if not out.exists():
        return df
    old = load_csv(out)
    if ID_COL not in old.columns:
        return df
    for c in MANUAL_COLS:
        if c in old.columns:
            keep = dict(zip(old[ID_COL], old[c]))
            if c not in df.columns:
                df[c] = ""
            prev_vals = df[ID_COL].map(keep)
            df[c] = np.where(prev_vals.notna() & (prev_vals != ""), prev_vals, df[c])
    new_ids = set(df[ID_COL]) - set(old[ID_COL])
    log(f"Kept your marks and notes; {len(new_ids)} new submission(s).")
    return df


def cmd_fetch(args):
    try:
        df = fetch_from_tally(args.form_id or C.TALLY_FORM_ID, os.environ.get("TALLY_API_KEY"))
    except RuntimeError as e:
        sys.exit(str(e))
    df = merge_previous(df, args.out)
    if args.cvs:
        df, _ = read_cvs(df)
    df.to_csv(args.out, index=False)
    _, dups = dedupe(df)
    print(f"Saved {len(df)} submissions to {args.out}.")
    if dups:
        print(f"{len(dups)} people submitted more than once; the latest of each is used.")
    print(f"Next: review the '{INCLUDE_COL}' column, then run `check` and `build`.")


def cmd_cvs(args):
    df = load_csv(args.csv)
    df, _ = read_cvs(df, refresh=args.refresh)
    out = args.out or args.csv
    df.to_csv(out, index=False)
    print(f"Saved {out}")


# ===========================================================================
# 2. CHECK
# ===========================================================================
def cmd_check(args):
    raw = load_csv(args.csv)
    df, dups = dedupe(raw)
    print(f"{len(raw)} submissions from {len(df)} people, {len(raw.columns)} columns.\n")
    print("Columns:")
    for c in raw.columns:
        print(f"  - {c[:110]}")
    print("\nconfig.py mapping:")
    problems = 0
    for key, val in C.COLUMNS.items():
        for v in (val if isinstance(val, list) else [val]):
            if not v:
                print(f"  {key:18s} (not used)")
            elif v in raw.columns:
                filled = (raw[v].str.strip() != "").mean()
                print(f"  {key:18s} OK       {v[:70]!r} ({filled:.0%} answered)")
            else:
                problems += 1
                print(f"  {key:18s} MISSING  {v[:70]!r}")
    if dups:
        print(f"\nRepeat submissions: {len(dups)} people - the latest of each will be used:")
        for d in dups:
            print(f"  {d['Name']} ({d['Submissions']} submissions)")
    if INCLUDE_COL in df.columns:
        st = Counter(include_status(v) for v in df[INCLUDE_COL])
        n_inc = sum(is_included(v) for v in df[INCLUDE_COL])
        default = "included" if getattr(C, "INCLUDE_BY_DEFAULT", False) else "left out"
        print(f"\nReview: {st['Included']} included, {st['Excluded']} excluded, "
              f"{st['Not reviewed']} not reviewed ({default} by default) -> {n_inc} will be teamed.")
    if CV_TEXT_COL in df.columns:
        print(f"CV text available for {(df[CV_TEXT_COL].str.strip() != '').sum()} of {len(df)} people.")
    else:
        print("No CV text yet - run `python team_builder.py cvs <file>` to read the CVs.")

    locs = [resolve_location(t, c) for t, c in zip(get(df, "country"), get(df, "country_code"))]
    by_tz = Counter(fmt_offset(o) for _, o, _ in locs)
    print("\nTime zones: " + ", ".join(f"{k} x{v}" for k, v in sorted(by_tz.items(), key=lambda x: str(x[0]))))
    fallback = [(t, n) for (n, o, s), t in zip(locs, get(df, "country")) if s == "country code"]
    unknown = [t for (n, o, s), t in zip(locs, get(df, "country")) if s == "not recognised"]
    if fallback:
        print("Read from the country code (answer not recognised): "
              + "; ".join(f"'{t}' -> {n}" for t, n in fallback))
    if unknown:
        print("NOT recognised - add to TIMEZONE_OVERRIDES: " + "; ".join(repr(t) for t in unknown))

    for key in ("idea_status", "availability"):
        c = col(key)
        if c in df.columns:
            print(f"\nAnswers to '{c[:70]}':")
            for v, n in df[c].value_counts().items():
                extra = ("-> has idea" if keyword_hits(v, C.IDEA_YES_KEYWORDS) else "-> no idea") \
                    if key == "idea_status" else f"-> {availability_score(v):.2f}"
                print(f"  {n:4d} x {v[:60]!r} {extra}")
    print("\nAll columns found." if not problems else f"\n{problems} column(s) missing: fix them in config.py.")


# ===========================================================================
# 3. Features, including keyword extraction
# ===========================================================================
EXTRA_STOP = {
    # CV and bio boilerplate
    "curriculum", "vitae", "resume", "cv", "email", "phone", "tel", "mobile", "address",
    "linkedin", "github", "www", "http", "https", "com", "org", "references", "reference",
    "available", "request", "page", "date", "birth", "nationality", "gender", "married",
    "single", "hobbies", "languages", "language", "english", "french", "fluent", "native",
    "proficient", "present", "current", "currently", "year", "years", "month", "months",
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug",
    "sep", "sept", "oct", "nov", "dec", "university", "student", "students", "studying",
    "degree", "bachelor", "master", "masters", "msc", "bsc", "ba", "ma", "mba", "phd",
    "school", "college", "institute", "graduate", "programme", "program", "skills",
    "skill", "experience", "experienced", "work", "worked", "working", "role", "team",
    "teams", "member", "project", "projects", "responsible", "responsibilities",
    "including", "include", "various", "also", "well", "strong", "good", "excellent",
    "able", "ability", "passionate", "passion", "interested", "interest", "interests",
    "like", "love", "want", "would", "looking", "hope", "believe", "currently", "based",
    "challenge", "cleantech", "competition", "idea", "ideas", "bio", "yes", "using",
    "use", "new", "help", "make", "making", "within", "across", "through", "key",
    # generic verbs, adjectives and filler common in bios and CVs
    "join", "joined", "joining", "build", "building", "bring", "bringing", "eager", "keen",
    "excited", "impactful", "impact", "successful", "successfully", "participate",
    "participated", "participating", "previously", "previous", "completed", "completing",
    "complete", "gained", "gaining", "gain", "graduated", "graduating", "honour", "honours",
    "honor", "gap", "gaps", "deadline", "mindset", "related", "relevant", "used", "solution",
    "solutions", "solve", "solving", "complex", "problem", "problems", "practical", "concept",
    "background", "minded", "driven", "dedicated", "motivated", "committed", "hardworking",
    "self", "various", "several", "multiple", "different", "diverse", "multidisciplinary",
    "interdisciplinary", "opportunity", "opportunities", "aim", "aiming", "seek", "seeking",
    "contribute", "contributing", "contribution", "learn", "learning", "grow", "growth",
    "develop", "developed", "developing", "development", "create", "created", "creating",
    "focus", "focused", "focusing", "led", "lead", "leading", "leader", "leadership", "manage",
    "managed", "managing", "support", "supported", "supporting", "provide", "provided",
    "providing", "ensure", "improve", "improved", "improving", "achieve", "achieved",
    "deliver", "delivered", "delivering", "involved", "involving", "apply", "applied",
    "applying", "deploying", "deploy", "real", "world", "global", "local", "life", "people",
    "person", "way", "ways", "thing", "things", "area", "field", "fields", "level", "high",
    "strong", "great", "best", "better", "big", "large", "small", "long", "term", "short",
    "time", "day", "days", "today", "future", "first", "second", "one", "two", "three",
    "may", "can", "could", "also", "however", "etc", "eg", "ie", "per", "via", "based",
    "role", "roles", "position", "intern", "internship", "assistant", "officer", "volunteer",
    "certificate", "certification", "certified", "course", "courses", "training", "trained",
    "award", "awards", "awarded", "scholarship", "scholar", "member", "membership", "club",
    "society", "association", "activities", "activity", "objective", "profile", "summary",
    "contact", "personal", "information", "details", "name", "full",
    "currently", "recently", "recent", "now", "since", "ago", "nd", "rd", "th", "st",
    "africa", "african", "asia", "asian", "europe", "european", "america", "american",
    "nigerian", "kenyan", "ghanaian", "ugandan", "malawian", "indian", "pakistani",
    "iranian", "italian", "french", "british", "south", "north", "east", "west", "western",
    "eastern", "northern", "southern", "country", "countries", "region", "regional",
    "city", "state", "national", "nationally", "internationally", "international",
    # French / Spanish function words
    "les", "des", "une", "est", "dans", "pour", "par", "sur", "avec", "qui", "que", "pas",
    "plus", "son", "ses", "aux", "ont", "sont", "nous", "vous", "leur", "cette", "ces",
    "del", "los", "las", "por", "para", "con", "una", "que", "como", "mas", "sus", "esta",
    "este", "entre", "sobre",
}


def _stop_words():
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
    return set(ENGLISH_STOP_WORDS) | EXTRA_STOP


STOP = None


def _singular(w):
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("s") and not w.endswith(("ss", "us", "is", "os", "ics")):
        return w[:-1]
    return w


def clean_text(t):
    t = fold(t)
    t = re.sub(r"https?://\S+|www\.\S+|\S+@\S+", " ", t)
    t = re.sub(r"[\d+()\-]{6,}", " ", t)
    return t


def _stop():
    global STOP
    if STOP is None:
        STOP = _stop_words()
        for name, _, aliases in COUNTRIES.values():   # places are covered by time zone
            for a in [name] + aliases:
                STOP.update(fold(a).split())
    return STOP


def tokenize(t):
    stop = _stop()
    words = re.findall(r"[a-z][a-z\-]+[a-z]", t)
    return [_singular(w) for w in words if len(w) >= 3 and w not in stop and _singular(w) not in stop]


def candidate_phrases(doc, max_words=3):
    """RAKE-style candidates: runs of content words between stop words and
    punctuation, plus their sub-phrases of up to `max_words` words."""
    stop = _stop()
    out = []
    for segment in re.split(r"[.,;:!?()\[\]{}/|\n\r\t\"•·–—]+", clean_text(doc)):
        run = []
        for w in re.findall(r"[a-z][a-z\-]*[a-z]|[a-z]", segment) + [None]:
            sw = _singular(w) if w else None
            if w is None or len(w) < 3 or w in stop or sw in stop:
                for size in range(1, max_words + 1):
                    for k in range(len(run) - size + 1):
                        out.append(" ".join(run[k:k + size]))
                run = []
            else:
                run.append(sw)
    return out


def tfidf(docs, ngrams=(1, 2), min_df=1, max_df=1.0):
    from sklearn.feature_extraction.text import TfidfVectorizer
    vec = TfidfVectorizer(analyzer=lambda d: candidate_phrases(d, ngrams[1]),
                          min_df=min_df, max_df=max_df, sublinear_tf=True)
    X = vec.fit_transform([d or "" for d in docs])
    return X, np.array(vec.get_feature_names_out())


def cosine(X):
    S = (X @ X.T).toarray()
    np.fill_diagonal(S, 1.0)
    return np.clip(S, 0, 1)


def extract_keywords(docs, names, k):
    """Top distinctive phrases per person (TF-IDF over all applicants)."""
    n = len(docs)
    has = np.array([bool(norm(d)) for d in docs])
    if has.sum() < 3:
        return [[] for _ in docs]
    min_df = C.KEYWORD_MIN_APPLICANTS if n >= 10 else 1
    try:
        X, vocab = tfidf(docs, ngrams=(1, 3), min_df=min_df, max_df=C.KEYWORD_MAX_SHARE)
    except ValueError:  # vocabulary empty after filtering
        X, vocab = tfidf(docs, ngrams=(1, 3), min_df=1)
    out = []
    for i in range(n):
        own = {_singular(w) for w in re.findall(r"[a-z]+", fold(names[i]))}
        row = X.getrow(i)
        order = np.argsort(-row.data)
        chosen = []
        for j in order:
            term = vocab[row.indices[j]]
            if set(term.split()) & own:
                continue
            if any(term in c or c in term for c in chosen):
                continue
            chosen.append(term)
            if len(chosen) >= k:
                break
        out.append(chosen)
    return out


def availability_score(val):
    v = norm_key(val)
    if not v:
        return np.nan
    for opt, score in C.AVAILABILITY_SCORES.items():
        if norm_key(opt) == v:
            return float(score)
    m = re.search(r"\d+(\.\d+)?", v)
    return min(1.0, float(m.group()) / 10.0) if m else np.nan


class Applicants:
    def __init__(self, df):
        self.df = d = df.reset_index(drop=True)
        n = self.n = len(d)
        self.keys = list(person_key(d))
        self.names = [v.strip() or f"Applicant {i + 1}" for i, v in enumerate(get(d, "name"))]
        self.emails = get(d, "email").tolist()
        self.linkedin = get(d, "linkedin").tolist()
        cv_text = d[CV_TEXT_COL].fillna("").tolist() if CV_TEXT_COL in d.columns else [""] * n
        extra = joined(d, cols("extra_keyword_text")).tolist()
        self.cv_docs = [norm(a + " " + b) for a, b in zip(cv_text, extra)]
        self.has_cv = np.array([bool(x) for x in self.cv_docs])

        # Criterion 2 - interests
        ignore = {fold(t) for t in getattr(C, "INTEREST_TAGS_IGNORE", [])}
        self.tags = [[t for t in split_tags(v) if fold(t) not in ignore] for v in get(d, "interest_tags")]
        self.interest_docs = joined(d, cols("interest_text")).tolist()
        S_tag, has_tag = self._tag_similarity(self.tags)
        S_text, has_text = self._text_similarity(self.interest_docs)
        S_cv, has_cv = self._plain_similarity(self.cv_docs)
        sh = C.INTEREST_SHARES
        self.S_int = self._blend([(S_tag, has_tag, sh["tags"]), (S_text, has_text, sh["text"]),
                                  (S_cv, has_cv, sh["cv"])])

        # Keywords (NLP) from everything written: bio, idea, CV, extra text
        profile_docs = [norm(a + " " + b) for a, b in zip(self.interest_docs, self.cv_docs)]
        self.keywords = extract_keywords(profile_docs, self.names, C.KEYWORDS_PER_PERSON)

        # Criterion 3 - idea status
        self.idea_raw = get(d, "idea_status").tolist()
        self.has_idea = np.array([keyword_hits(v, C.IDEA_YES_KEYWORDS) > 0 for v in self.idea_raw])

        # Criterion 4 - time zone
        self.country_raw = get(d, "country").tolist()
        locs = [resolve_location(t, c) for t, c in zip(self.country_raw, get(d, "country_code"))]
        self.country = [x[0] for x in locs]
        self.offset = [x[1] for x in locs]
        self.loc_source = [x[2] for x in locs]
        self.S_tz = tz_similarity(self.offset)

        # Criterion 5 - background: programme category + main field in CV
        self.degree_raw = get(d, "degree").tolist()
        self.degree_cat = [first_match(v, C.BACKGROUND_CATEGORIES) for v in self.degree_raw]
        bio = joined(d, cols("interest_text")[:1]).tolist()
        self.cv_field = [most_hits(cv or b, C.BACKGROUND_CATEGORIES) for cv, b in zip(self.cv_docs, bio)]
        self.bg_dims = []
        if col("degree") in d.columns:
            self.bg_dims.append(self.degree_cat)
        if any(self.has_cv) or any(bio):
            self.bg_dims.append(self.cv_field)
        self.n_bg_cats = len(C.BACKGROUND_CATEGORIES) + 1

        # Criterion 6 - skills
        skill_tags = get(d, "skill_tags").tolist()
        skill_text = joined(d, cols("skill_text")).tolist()
        self.skills = np.zeros((n, len(SKILL_NAMES)))
        for i in range(n):
            for k, sk in enumerate(SKILL_NAMES):
                kws = C.SKILLS[sk]
                score = max(1.0 if keyword_hits(skill_tags[i], kws) else 0.0,
                            0.4 * keyword_hits(skill_text[i], kws) + 0.25 * keyword_hits(self.cv_docs[i], kws))
                self.skills[i, k] = min(1.0, score)

        # Criterion 7 - commitment
        self.avail_raw = get(d, "availability").tolist()
        avail = np.array([availability_score(v) for v in self.avail_raw])
        motiv = pd.to_numeric(d[MOTIVATION_COL] if MOTIVATION_COL in d.columns else pd.Series([""] * n),
                              errors="coerce").to_numpy(dtype=float)
        motiv = np.clip((motiv - 1) / 4.0, 0, 1)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            commit = np.nanmean(np.vstack([avail, motiv]), axis=0)
        self.commitment = np.where(np.isnan(commit), 0.5, commit)
        self.low_avail = np.array([not np.isnan(a) and a < 0.5 for a in avail])

        a = C.POOL_INTEREST_SHARE
        self.P = a * self.S_int + (1 - a) * self.S_tz

    @staticmethod
    def _tag_similarity(tags):
        n = len(tags)
        vocab = sorted({fold(t) for ts in tags for t in ts})
        has = np.array([len(ts) > 0 for ts in tags])
        if not vocab:
            return np.zeros((n, n)), has
        idx = {t: j for j, t in enumerate(vocab)}
        M = np.zeros((n, len(vocab)))
        for i, ts in enumerate(tags):
            for t in ts:
                M[i, idx[fold(t)]] = 1
        inter = M @ M.T
        sizes = M.sum(1)
        union = sizes[:, None] + sizes[None, :] - inter
        with np.errstate(divide="ignore", invalid="ignore"):
            S = np.where(union > 0, inter / union, 0.0)
        np.fill_diagonal(S, 1.0)
        return S, has

    @staticmethod
    def _plain_similarity(docs):
        n = len(docs)
        has = np.array([bool(norm(t)) for t in docs])
        if has.sum() < 2:
            return np.zeros((n, n)), has
        try:
            X, _ = tfidf(docs, ngrams=(1, 2), min_df=2 if n >= 10 else 1, max_df=C.KEYWORD_MAX_SHARE)
        except ValueError:
            return np.zeros((n, n)), has
        return cosine(X), has

    @staticmethod
    def _text_similarity(docs):
        n = len(docs)
        has = np.array([bool(norm(t)) for t in docs])
        if has.sum() < 2:
            return np.zeros((n, n)), has
        if C.USE_EMBEDDINGS:
            try:
                from sentence_transformers import SentenceTransformer
                model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
                E = model.encode([t or "" for t in docs], normalize_embeddings=True)
                S = np.clip(E @ E.T, 0, 1)
                S[~has, :] = 0
                S[:, ~has] = 0
                np.fill_diagonal(S, 1.0)
                return S, has
            except Exception as e:
                print(f"Embeddings unavailable ({e}); using keyword similarity instead.")
        return Applicants._plain_similarity(docs)

    @staticmethod
    def _blend(sources):
        n = sources[0][0].shape[0]
        num, den = np.zeros((n, n)), np.zeros((n, n))
        for S, has, share in sources:
            both = has[:, None] & has[None, :]
            num += np.where(both, share * S, 0.0)
            den += np.where(both, share, 0.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            S = np.where(den > 0, num / den, 0.0)
        np.fill_diagonal(S, 1.0)
        return S


# ===========================================================================
# 4. Scoring
# ===========================================================================
W = C.WEIGHTS


def mean_pairwise(S, m):
    k = len(m)
    if k < 2:
        return 0.0
    sub = S[np.ix_(m, m)]
    return float((sub.sum() - np.trace(sub)) / (k * (k - 1)))


def components(A, m):
    m = list(m)
    k = len(m)
    comp = {
        "interest_similarity": mean_pairwise(A.S_int, m),
        "timezone_match": mean_pairwise(A.S_tz, m),
        "skill_coverage": float(A.skills[m].max(axis=0).mean()) if k else 0.0,
        "background_diversity": float(np.mean(
            [len({dim[i] for i in m}) / min(k, A.n_bg_cats) for dim in A.bg_dims])) if A.bg_dims and k else 0.0,
    }
    c = A.commitment[m] if k else np.array([0.5])
    comp["commitment_balance"] = float(1 - (c.max() - c.min()))
    share = float(A.has_idea[m].mean()) if k else 0.5
    comp["idea_mix"] = 1 - abs(share - 0.5) * 2   # 1.0 = half have an idea
    return comp


def team_score(A, m):
    if not m:
        return 0.0
    return sum(W.get(k, 0) * v for k, v in components(A, m).items())


def team_value(A, m):
    """Optimiser objective: score x size (every person counts equally), minus a
    small penalty for drifting from the target size."""
    k = len(m)
    return k * team_score(A, m) - 0.5 * (k - C.TARGET_TEAM_SIZE) ** 2 if k else 0.0


def interest_fit(A, i, others):
    others = [j for j in others if j != i]
    return float(A.S_int[i, others].mean()) if others else 0.0


def overall_fit(A, i, others):
    others = [j for j in others if j != i]
    if not others:
        return 0.0
    wi, wt = W["interest_similarity"], W["timezone_match"]
    return float((wi * A.S_int[i, others].mean() + wt * A.S_tz[i, others].mean()) / (wi + wt))


# ===========================================================================
# 5. Pools
# ===========================================================================
def n_teams(size):
    if size < C.MIN_TEAM_SIZE:
        return 0
    t = max(1, round(size / C.TARGET_TEAM_SIZE))
    t = max(t, math.ceil(size / C.MAX_TEAM_SIZE))
    t = min(t, size // C.MIN_TEAM_SIZE)
    return max(1, t)


def split_cluster(A, idx, k):
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform
    idx = list(idx)
    if len(idx) <= 2 or k <= 1:
        return [idx]
    D = 1 - A.P[np.ix_(idx, idx)]
    D = np.clip((D + D.T) / 2, 0, None)
    np.fill_diagonal(D, 0)
    labels = fcluster(linkage(squareform(D, checks=False), method="average"), t=k, criterion="maxclust")
    groups = {}
    for i, lab in zip(idx, labels):
        groups.setdefault(lab, []).append(i)
    return list(groups.values())


def make_pools(A):
    pools = split_cluster(A, range(A.n), max(1, round(A.n / C.POOL_TARGET_SIZE)))
    changed = True
    while changed:
        changed = False
        for p in list(pools):
            if len(p) > C.POOL_MAX_SIZE:
                parts = split_cluster(A, p, max(2, round(len(p) / C.POOL_TARGET_SIZE)))
                if len(parts) > 1:
                    pools.remove(p)
                    pools.extend(parts)
                    changed = True
    while len(pools) > 1:
        pools.sort(key=len)
        small = pools[0]
        if len(small) >= C.POOL_MIN_SIZE:
            break
        best = max(range(1, len(pools)), key=lambda j: float(A.P[np.ix_(small, pools[j])].mean()))
        pools[best] = pools[best] + small
        pools.pop(0)
    return [sorted(p) for p in pools]


def rebalance(A, pools, flag, borrowed):
    for _ in range(500):
        need = [n_teams(len(p)) for p in pools]
        have = [int(flag[p].sum()) for p in pools]
        moved = False
        for j in [j for j in range(len(pools)) if have[j] < need[j]]:
            best = None
            for d in [d for d in range(len(pools)) if d != j and have[d] > need[d]]:
                for i in pools[d]:
                    if flag[i]:
                        s = float(A.P[i, pools[j]].mean())
                        if best is None or s > best[0]:
                            best = (s, i, d)
            if best:
                _, i, d = best
                pools[d].remove(i)
                pools[j].append(i)
                borrowed.add(i)
                moved = True
                break
        if not moved:
            break
    return pools


# ===========================================================================
# 6. Teams inside a pool
# ===========================================================================
def valid(A, team, req):
    need_idea, need_non = req
    k_idea = int(A.has_idea[team].sum()) if team else 0
    return (not need_idea or k_idea >= 1) and (not need_non or len(team) - k_idea >= 1)


def form_teams(A, pool, rng):
    t = n_teams(len(pool))
    if t == 0:
        return [], [], list(pool)
    holders = sorted([i for i in pool if A.has_idea[i]], key=lambda i: -A.commitment[i])
    non = [i for i in pool if not A.has_idea[i]]
    teams = [[] for _ in range(t)]
    unplaced = set(pool)
    for ti, i in enumerate(holders[:t]):
        teams[ti].append(i)
        unplaced.discard(i)
    for ti in range(t):
        cands = [i for i in non if i in unplaced]
        if not cands:
            break
        best = max(cands, key=lambda i: team_value(A, teams[ti] + [i]) - team_value(A, teams[ti]))
        teams[ti].append(best)
        unplaced.discard(best)
    reqs = [(any(A.has_idea[i] for i in tm), any(not A.has_idea[i] for i in tm)) for tm in teams]

    order = sorted(unplaced)
    rng.shuffle(order)
    remaining = set(order)
    while remaining:
        below = [ti for ti in range(t) if len(teams[ti]) < C.MIN_TEAM_SIZE]
        room = below or [ti for ti in range(t) if len(teams[ti]) < C.MAX_TEAM_SIZE]
        if not room:
            break
        base = {ti: team_value(A, teams[ti]) for ti in room}
        best, best_gain = None, -1e18
        for i in order:
            if i not in remaining:
                continue
            for ti in room:
                g = team_value(A, teams[ti] + [i]) - base[ti]
                if g > best_gain:
                    best, best_gain = (i, ti), g
        i, ti = best
        teams[ti].append(i)
        remaining.discard(i)
    return local_search(A, teams, reqs), reqs, sorted(remaining)


def local_search(A, teams, reqs, max_passes=50):
    t = len(teams)
    scores = [team_value(A, tm) for tm in teams]
    for _ in range(max_passes):
        improved = False
        for a in range(t):
            for b in range(a + 1, t):
                done = False
                for i in list(teams[a]):
                    for j in list(teams[b]):
                        ta = [x for x in teams[a] if x != i] + [j]
                        tb = [x for x in teams[b] if x != j] + [i]
                        if not (valid(A, ta, reqs[a]) and valid(A, tb, reqs[b])):
                            continue
                        sa, sb = team_value(A, ta), team_value(A, tb)
                        if sa + sb > scores[a] + scores[b] + 1e-9:
                            teams[a], teams[b], scores[a], scores[b] = ta, tb, sa, sb
                            improved = done = True
                            break
                    if done:
                        break
                for src, dst in ((a, b), (b, a)):
                    if len(teams[src]) <= C.MIN_TEAM_SIZE or len(teams[dst]) >= C.MAX_TEAM_SIZE:
                        continue
                    for i in list(teams[src]):
                        ts = [x for x in teams[src] if x != i]
                        td = teams[dst] + [i]
                        if not (valid(A, ts, reqs[src]) and valid(A, td, reqs[dst])):
                            continue
                        s1, s2 = team_value(A, ts), team_value(A, td)
                        if s1 + s2 > scores[src] + scores[dst] + 1e-9:
                            teams[src], teams[dst], scores[src], scores[dst] = ts, td, s1, s2
                            improved = True
                            break
        if not improved:
            break
    return teams


# ===========================================================================
# 7. Leave-out and explanations
# ===========================================================================
def leave_out(A, all_teams):
    """Drop the weakest INTEREST fits (capped). -> ([(person, ex-teammates)], median)."""
    fits = {i: interest_fit(A, i, tm["members"]) for tm in all_teams for i in tm["members"]}
    if not fits:
        return [], 0.0
    median = float(np.median(list(fits.values())))
    cap = int(math.floor(C.MAX_UNASSIGNED_SHARE * A.n))
    threshold = C.LEAVE_OUT_RELATIVE_FIT * median
    removed = []
    for i in sorted(fits, key=fits.get):
        if len(removed) >= cap or fits[i] >= threshold:
            break
        tm = next(t for t in all_teams if i in t["members"])
        rest = [x for x in tm["members"] if x != i]
        if len(rest) < C.MIN_TEAM_SIZE or not valid(A, rest, tm["req"]):
            continue
        tm["members"] = rest
        removed.append((i, rest))
    return removed, median


def shared_items(lists, top=3):
    c = Counter(x for lst in lists for x in dict.fromkeys(lst))
    return [(x, n) for x, n in c.most_common() if n >= 2][:top]


def tz_summary(A, m):
    offs = [A.offset[i] for i in m if A.offset[i] is not None]
    if not offs:
        return "Time zones unknown"
    lo, hi = min(offs), max(offs)
    gap = hi - lo
    if gap > 12:
        gap = 24 - gap
    span = fmt_offset(lo) if lo == hi else f"{fmt_offset(lo)} to {fmt_offset(hi)}"
    return f"{span} ({gap:g}h apart)"


def describe_team(A, m, borrowed):
    parts = []
    tags = shared_items([A.tags[i] for i in m])
    if tags:
        parts.append("Shared areas: " + ", ".join(f"{t} ({n})" for t, n in tags))
    kws = shared_items([A.keywords[i] for i in m], top=4)
    if kws:
        parts.append("Common keywords: " + ", ".join(t for t, _ in kws))
    countries = Counter(A.country[i] for i in m)
    parts.append("Countries: " + ", ".join(f"{c} x{n}" if n > 1 else c for c, n in countries.most_common()))
    ideas = [A.names[i] for i in m if A.has_idea[i]]
    parts.append("Idea-holders: " + (", ".join(ideas) if ideas else "none"))
    cover = A.skills[m].max(axis=0)
    have = [SKILL_NAMES[k] for k in range(len(SKILL_NAMES)) if cover[k] >= 0.5]
    parts.append("Skills: " + (", ".join(have) if have else "none detected"))
    if A.bg_dims:
        parts.append("Backgrounds: " + ", ".join(sorted({A.degree_cat[i] for i in m})))

    flags = []
    if not ideas:
        flags.append("No idea-holder")
    if len(ideas) == len(m):
        flags.append("Everyone already has an idea")
    gaps = [SKILL_NAMES[k] for k in range(len(SKILL_NAMES)) if cover[k] < 0.5]
    if gaps:
        flags.append("Skill gaps: " + ", ".join(gaps))
    offs = [A.offset[i] for i in m if A.offset[i] is not None]
    if offs:
        gap = max(offs) - min(offs)
        gap = 24 - gap if gap > 12 else gap
        if gap >= C.TZ_FLAG_GAP_HOURS:
            flags.append(f"Wide time-zone spread ({gap:g}h)")
    low = [A.names[i] for i in m if A.low_avail[i]]
    if low:
        flags.append(f"{C.LOW_AVAILABILITY_LABEL}: " + ", ".join(low))
    b = [A.names[i] for i in m if i in borrowed]
    if b:
        flags.append("Moved in from a neighbouring pool: " + ", ".join(b))
    return " | ".join(parts), "; ".join(flags)


def describe_unassigned(A, i, former, pool, median):
    reasons = []
    others = [j for j in pool if j != i]
    mine = {fold(t) for t in A.tags[i]}
    if mine and not any(mine & {fold(t) for t in A.tags[j]} for j in others):
        reasons.append("no one else in their pool picked their areas (" + ", ".join(A.tags[i]) + ")")
    my_kw = set(A.keywords[i])
    if my_kw and not any(my_kw & set(A.keywords[j]) for j in former):
        reasons.append("no keywords in common with their would-be teammates")
    if not reasons:
        reasons.append("weaker interest match than the rest of their team")
    text = "; ".join(reasons)
    return text[0].upper() + text[1:] + f" (interest fit {interest_fit(A, i, former):.2f} vs median {median:.2f})"


# ===========================================================================
# 8. BUILD
# ===========================================================================
_SETTINGS_LOCK = threading.RLock()


@contextmanager
def settings(weights=None, sizes=None):
    """Temporarily override scoring weights and team sizes (thread-safe), so a
    dashboard user can try different settings without touching config.py."""
    global W
    with _SETTINGS_LOCK:
        saved_w = W
        saved_sizes = (C.MIN_TEAM_SIZE, C.TARGET_TEAM_SIZE, C.MAX_TEAM_SIZE)
        try:
            if weights:
                W = {**saved_w, **weights}
            if sizes:
                C.MIN_TEAM_SIZE, C.TARGET_TEAM_SIZE, C.MAX_TEAM_SIZE = sizes
            yield
        finally:
            W = saved_w
            C.MIN_TEAM_SIZE, C.TARGET_TEAM_SIZE, C.MAX_TEAM_SIZE = saved_sizes


def check_columns(raw):
    missing = [col(k) for k in ("name", "idea_status") if col(k) and col(k) not in raw.columns]
    if not (col("interest_tags") in raw.columns or any(c in raw.columns for c in cols("interest_text"))):
        missing.append("an interest column (interest_tags or interest_text)")
    return missing


def people_table(raw):
    """Raw submissions -> one row per person (latest submission), plus repeats."""
    return dedupe(raw)


def included(df):
    if INCLUDE_COL not in df.columns:
        return df.iloc[0:0] if not getattr(C, "INCLUDE_BY_DEFAULT", False) else df
    return df[[is_included(v) for v in df[INCLUDE_COL]]]


def load_included(path, include_all):
    raw = load_csv(path)
    missing = check_columns(raw)
    if missing:
        sys.exit("These columns from config.py aren't in the data: " + "; ".join(map(str, missing))
                 + f"\nRun `python team_builder.py check {path}` to see the available columns.")
    df, dups = people_table(raw)
    if dups:
        print(f"{len(raw)} submissions -> {len(df)} people ({len(dups)} repeat submitters, latest kept).")
    if include_all:
        return df, dups
    inc = included(df)
    if len(inc) == 0:
        sys.exit(f"No one is included. Review the '{INCLUDE_COL}' column, or use --include-all.")
    print(f"{len(inc)} of {len(df)} people included.")
    return inc, dups


def build_teams(A, dups=None, log=print):
    """Run the full team-forming method on an Applicants object -> result dict."""
    if A.n < C.MIN_TEAM_SIZE:
        raise ValueError(f"At least {C.MIN_TEAM_SIZE} included people are needed to form a team.")
    rng = np.random.default_rng(C.RANDOM_SEED)
    pools = make_pools(A)
    borrowed = set()
    for _ in range(3):
        pools = rebalance(A, pools, A.has_idea, borrowed)
        pools = rebalance(A, pools, ~A.has_idea, borrowed)
    pools = [sorted(p) for p in pools]
    log(f"{len(pools)} pools: sizes {sorted(len(p) for p in pools)}")

    all_teams, unplaced = [], []
    for pi, pool in enumerate(pools):
        teams, reqs, rest = form_teams(A, pool, rng)
        unplaced += [{"i": i, "former": [j for j in pool if j != i], "pool": pi, "reason": None} for i in rest]
        all_teams += [{"members": tm, "req": rq, "pool": pi} for tm, rq in zip(teams, reqs)]

    removed, median = leave_out(A, all_teams)
    pool_of = {i: pi for pi, p in enumerate(pools) for i in p}
    unplaced = [{"i": i, "former": f, "pool": pool_of[i], "reason": None} for i, f in removed] + unplaced
    by_pool = {}
    for tm in all_teams:
        by_pool.setdefault(tm["pool"], []).append(tm)
    for tms in by_pool.values():
        new = local_search(A, [t["members"] for t in tms], [t["req"] for t in tms])
        for t, m in zip(tms, new):
            t["members"] = m

    all_teams = [t for t in all_teams if t["members"]]
    all_teams.sort(key=lambda t: (t["pool"], -team_score(A, t["members"])))
    for n, t in enumerate(all_teams, 1):
        t["id"] = f"T{n:02d}"
    return {"A": A, "teams": all_teams, "unassigned": unplaced, "pools": pools,
            "borrowed": borrowed, "median": median, "dups": dups or [],
            "weights": dict(W), "sizes": (C.MIN_TEAM_SIZE, C.TARGET_TEAM_SIZE, C.MAX_TEAM_SIZE)}


# ---- saving and restoring a set of teams (used by the dashboard) ----------
def result_to_json(res, **meta):
    A = res["A"]
    k = A.keys
    return {
        **meta,
        "weights": res.get("weights", dict(W)),
        "sizes": list(res.get("sizes", (C.MIN_TEAM_SIZE, C.TARGET_TEAM_SIZE, C.MAX_TEAM_SIZE))),
        "median": res["median"],
        "pools": [[k[i] for i in p] for p in res["pools"]],
        "borrowed": [k[i] for i in res["borrowed"]],
        "teams": [{"id": t["id"], "pool": t["pool"], "req": list(t["req"]),
                   "members": [k[i] for i in t["members"]]} for t in res["teams"]],
        "unassigned": [{"key": k[u["i"]], "pool": u["pool"], "reason": u.get("reason"),
                        "former": [k[j] for j in u["former"]]} for u in res["unassigned"]],
    }


def result_from_json(js, A):
    """Rebuild a result for the current Applicants. People no longer included
    drop out; people added since appear as unassigned."""
    idx = {key: i for i, key in enumerate(A.keys)}
    pools = [[idx[k] for k in p if k in idx] for p in js.get("pools", [])]
    teams = [{"id": t["id"], "pool": t.get("pool", 0), "req": tuple(t.get("req", (True, True))),
              "members": [idx[k] for k in t["members"] if k in idx]} for t in js.get("teams", [])]
    placed = {i for t in teams for i in t["members"]}
    unassigned, seen = [], set()
    for u in js.get("unassigned", []):
        if u["key"] in idx and idx[u["key"]] not in placed:
            i = idx[u["key"]]
            seen.add(i)
            unassigned.append({"i": i, "pool": u.get("pool", 0), "reason": u.get("reason"),
                               "former": [idx[k] for k in u.get("former", []) if k in idx]})
    in_pool = {i for p in pools for i in p}
    for i in range(A.n):
        if i not in placed and i not in seen:
            unassigned.append({"i": i, "pool": 0, "former": [], "reason": "Added since these teams were built"})
            if i not in in_pool and pools:
                pools[0].append(i)
    removed = [k for t in js.get("teams", []) for k in t["members"] if k not in idx]
    return {"A": A, "teams": [t for t in teams], "unassigned": unassigned, "pools": pools,
            "borrowed": {idx[k] for k in js.get("borrowed", []) if k in idx},
            "median": js.get("median", 0.0), "dups": [], "removed_keys": removed,
            "weights": js.get("weights", dict(W)), "sizes": tuple(js.get("sizes", (3, 5, 6)))}


def move_person(res, i, team_id):
    """Manually move person i to team_id (or None = unassigned)."""
    for t in res["teams"]:
        if i in t["members"]:
            t["members"] = [x for x in t["members"] if x != i]
            former = list(t["members"])
            break
    else:
        former = None
    res["unassigned"] = [u for u in res["unassigned"] if u["i"] != i]
    if team_id is None:
        res["unassigned"].append({"i": i, "pool": 0, "former": former or [], "reason": "Moved out manually"})
    else:
        next(t for t in res["teams"] if t["id"] == team_id)["members"].append(i)
    return res


def new_team(res):
    used = {t["id"] for t in res["teams"]}
    n = len(res["teams"]) + 1
    while f"T{n:02d}" in used:
        n += 1
    res["teams"].append({"id": f"T{n:02d}", "pool": 0, "req": (False, False), "members": []})
    return f"T{n:02d}"


def cmd_build(args):
    df, dups = load_included(args.csv, args.include_all)
    A = Applicants(df)
    print(f"Features built for {A.n} people ({A.has_idea.sum()} with an idea, {(~A.has_idea).sum()} without; "
          f"CV text for {A.has_cv.sum()}).")
    unknown = sum(1 for o in A.offset if o is None)
    if unknown:
        print(f"Note: {unknown} location(s) not recognised - see `check` and TIMEZONE_OVERRIDES.")
    try:
        res = build_teams(A, dups)
    except ValueError as e:
        sys.exit(str(e))
    tables = summarise(res)
    write_workbook(tables, args.out)
    placed = sum(len(t["members"]) for t in res["teams"])
    print(f"\n{len(res['teams'])} teams, {placed} placed, {A.n - placed} unassigned.")
    no_idea = sum(1 for t in res["teams"] if not A.has_idea[t["members"]].any())
    if no_idea:
        print(f"Note: {no_idea} team(s) have no idea-holder - see the Check column.")
    print(f"Saved {args.out}")


# ===========================================================================
# 9. OUTPUT
# ===========================================================================
def pool_label(A, pool):
    if not pool:
        return "Manual"
    tags = Counter(t for i in pool for t in A.tags[i])
    top = ", ".join(t for t, _ in tags.most_common(2)) if tags else "mixed interests"
    offs = [A.offset[i] for i in pool if A.offset[i] is not None]
    tz = (fmt_offset(min(offs)) if min(offs) == max(offs) else f"{fmt_offset(min(offs))} to {fmt_offset(max(offs))}") if offs else "unknown"
    return f"{top} ({tz})"


def top_skills(A, i):
    return [SKILL_NAMES[k] for k in np.argsort(-A.skills[i]) if A.skills[i, k] >= 0.5]


def summarise(res):
    """Result -> dict of DataFrames (the same tables as the Excel workbook)."""
    A, pools, borrowed, median = res["A"], res["pools"], res["borrowed"], res["median"]
    labels = {pi: pool_label(A, p) for pi, p in enumerate(pools)}
    team_id, summary, members = {}, [], []
    for tm in res["teams"]:
        tid, m = tm["id"], tm["members"]
        for i in m:
            team_id[i] = tid
        if not m:
            summary.append({"Team": tid, "Pool": labels.get(tm["pool"], ""), "Size": 0})
            continue
        comp = components(A, m)
        why, flags = describe_team(A, m, borrowed)
        summary.append({
            "Team": tid, "Pool": labels.get(tm["pool"], ""), "Size": len(m),
            "Team score": round(team_score(A, m), 2),
            "Interest similarity": round(comp["interest_similarity"], 2),
            "Time zone match": round(comp["timezone_match"], 2),
            "Background diversity": round(comp["background_diversity"], 2),
            "Skill coverage": round(comp["skill_coverage"], 2),
            "Commitment balance": round(comp["commitment_balance"], 2),
            "Idea mix": round(comp["idea_mix"], 2),
            "Idea-holders": int(A.has_idea[m].sum()),
            "Time zones": tz_summary(A, m),
            "Why this team": why, "Check": flags,
        })
        for i in sorted(m, key=lambda x: (not A.has_idea[x], A.names[x])):
            members.append({
                "Team": tid, "Name": A.names[i], "Email": A.emails[i], "LinkedIn": A.linkedin[i],
                "Has idea": "Yes" if A.has_idea[i] else "No",
                "Country": A.country[i], "Time zone": fmt_offset(A.offset[i]),
                "Areas": ", ".join(A.tags[i]), "Keywords": ", ".join(A.keywords[i]),
                "Programme category": A.degree_cat[i], "Main field (CV/bio)": A.cv_field[i],
                "Top skills": ", ".join(top_skills(A, i)),
                "Can attend finals": A.avail_raw[i],
                "Interest fit": round(interest_fit(A, i, m), 2),
                "_key": A.keys[i],
            })

    unassigned = []
    with_room = [t for t in res["teams"] if 0 < len(t["members"]) < C.MAX_TEAM_SIZE]
    for u in res["unassigned"]:
        i = u["i"]
        best = max(with_room, key=lambda t: overall_fit(A, i, t["members"]), default=None)
        pool = pools[u["pool"]] if u["pool"] < len(pools) else []
        reason = u.get("reason") or describe_unassigned(A, i, u["former"], pool, median)
        unassigned.append({
            "Name": A.names[i], "Email": A.emails[i], "LinkedIn": A.linkedin[i],
            "Has idea": "Yes" if A.has_idea[i] else "No",
            "Country": A.country[i], "Time zone": fmt_offset(A.offset[i]),
            "Areas": ", ".join(A.tags[i]), "Keywords": ", ".join(A.keywords[i]),
            "Reason": reason,
            "Closest team with room": best["id"] if best else "",
            "Fit to that team": round(overall_fit(A, i, best["members"]), 2) if best else "",
            "_key": A.keys[i],
        })

    pool_of = {i: pi for pi, p in enumerate(pools) for i in p}
    applicants = []
    for i in range(A.n):
        row = {"Name": A.names[i], "Email": A.emails[i], "Team": team_id.get(i, "Unassigned"),
               "Pool": labels.get(pool_of.get(i), ""), "Idea answer": A.idea_raw[i],
               "Read as idea": "Yes" if A.has_idea[i] else "No",
               "Country answer": A.country_raw[i], "Read as": A.country[i],
               "Time zone": fmt_offset(A.offset[i]), "Found from": A.loc_source[i],
               "Programme answer": A.degree_raw[i], "Programme category": A.degree_cat[i],
               "Main field (CV/bio)": A.cv_field[i], "CV read": "Yes" if A.has_cv[i] else "No",
               "Keywords": ", ".join(A.keywords[i])}
        for k, sk in enumerate(SKILL_NAMES):
            row[f"Skill: {sk}"] = round(float(A.skills[i, k]), 2)
        row["Finals answer"] = A.avail_raw[i]
        row["Commitment"] = round(float(A.commitment[i]), 2)
        applicants.append(row)

    weights = res.get("weights", W)
    n_assigned = sum(len(t["members"]) for t in res["teams"])
    settings_rows = [
        ("Generated", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("People included", A.n), ("Placed in teams", n_assigned),
        ("Unassigned", A.n - n_assigned), ("Teams", len([t for t in res["teams"] if t["members"]])),
        ("Pools", len(pools)), ("Repeat submitters merged", len(res.get("dups", []))),
        ("People with CV text", int(A.has_cv.sum())),
        ("Team size (min / target / max)", " / ".join(map(str, res.get("sizes", (C.MIN_TEAM_SIZE, C.TARGET_TEAM_SIZE, C.MAX_TEAM_SIZE))))),
        ("Time zone: full match within / zero at (hours)", f"{C.TZ_FULL_MATCH_HOURS} / {C.TZ_ZERO_MATCH_HOURS}"),
        ("Median interest fit", round(median, 2)),
        ("Leave-out threshold (share of median interest fit)", C.LEAVE_OUT_RELATIVE_FIT),
        ("Max share unassigned", C.MAX_UNASSIGNED_SHARE),
    ] + [(f"Weight: {k}", v) for k, v in weights.items()] + [
        (f"Interest share: {k}", v) for k, v in C.INTEREST_SHARES.items()] + [
        ("Keyword method", "RAKE-style candidate phrases scored by TF-IDF, kept if shared by at least "
                           f"{C.KEYWORD_MIN_APPLICANTS} applicants"),
        ("How to read", "Scores run 0-1. Team score is the weighted sum of the components. "
                        "'Interest fit' is a member's interest similarity with teammates. "
                        "Use the Applicants tab to check how answers were read."),
    ]
    return {
        "Team summary": pd.DataFrame(summary),
        "Team members": pd.DataFrame(members),
        "Unassigned": pd.DataFrame(unassigned, columns=["Name", "Email", "LinkedIn", "Has idea", "Country",
                                                         "Time zone", "Areas", "Keywords", "Reason",
                                                         "Closest team with room", "Fit to that team", "_key"]),
        "Applicants": pd.DataFrame(applicants),
        "Repeat submissions": pd.DataFrame(res.get("dups", []),
                                           columns=["Name", "Email", "Submissions", "Kept (latest)", "Dropped"]),
        "Settings": pd.DataFrame(settings_rows, columns=["Setting", "Value"]),
    }


def write_workbook(tables, out):
    """Write the tables to a formatted Excel file (path or file-like object)."""
    from io import BytesIO
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, t in tables.items():
            t.drop(columns=[c for c in t.columns if str(c).startswith("_")]).to_excel(xw, sheet_name=name, index=False)
    buf.seek(0)
    wb = load_workbook(buf)
    head_fill = PatternFill("solid", fgColor="1F5C3A")
    band_fill = PatternFill("solid", fgColor="E8F3EC")
    wide = {"Why this team": 70, "Check": 45, "Reason": 60, "How to read": 80, "Value": 45,
            "Setting": 45, "Areas": 35, "Keywords": 45, "Pool": 32, "Email": 28, "Name": 24,
            "LinkedIn": 30, "Time zones": 22, "Country answer": 18, "Programme answer": 40}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                c.font = Font(name="Arial", size=10, bold=c.row == 1,
                              color="FFFFFF" if c.row == 1 else "000000")
                c.alignment = Alignment(vertical="top", wrap_text=True)
                if c.row == 1:
                    c.fill = head_fill
        for j, cell in enumerate(ws[1], 1):
            ws.column_dimensions[get_column_letter(j)].width = wide.get(
                cell.value, max(12, min(22, len(str(cell.value)) + 2)))
        ws.freeze_panes = "A2"
        if ws.max_row > 1:
            ws.auto_filter.ref = ws.dimensions
    if "Team members" in wb.sheetnames:
        ws = wb["Team members"]
        last, band = None, False
        for r in range(2, ws.max_row + 1):
            tid = ws.cell(r, 1).value
            if tid != last:
                band, last = not band, tid
            if band:
                for c in ws[r]:
                    c.fill = band_fill
    wb.save(out)


# ===========================================================================
def main():
    ap = argparse.ArgumentParser(description="LBS Cleantech Challenge team builder")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download applications from Tally")
    f.add_argument("--out", default="applicants.csv")
    f.add_argument("--form-id", default=None)
    f.add_argument("--cvs", action="store_true", help="also read the PDF CVs")
    v = sub.add_parser("cvs", help="read the PDF CVs into a cv_text column")
    v.add_argument("csv")
    v.add_argument("--out", default=None, help="write to a new file instead of updating in place")
    v.add_argument("--refresh", action="store_true", help="re-read CVs that already have text")
    c = sub.add_parser("check", help="check config.py against your data")
    c.add_argument("csv")
    b = sub.add_parser("build", help="form teams")
    b.add_argument("csv")
    b.add_argument("--out", default="teams.xlsx")
    b.add_argument("--include-all", action="store_true", help="ignore the include column")
    args = ap.parse_args()
    {"fetch": cmd_fetch, "cvs": cmd_cvs, "check": cmd_check, "build": cmd_build}[args.cmd](args)


if __name__ == "__main__":
    main()
