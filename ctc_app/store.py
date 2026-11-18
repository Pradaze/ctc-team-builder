"""Where the dashboard keeps its data.

Two interchangeable stores with the same small interface:

- LocalStore: files in a folder (data/ by default). Used on your own computer.
- PostgresStore: a Postgres database, e.g. a free Neon project. Used when the
  app is hosted somewhere whose own disk is wiped on restart (Streamlit
  Community Cloud, Render's free plan). Chosen automatically when a
  DATABASE_URL secret or environment variable is set.

Each "file" (applicants.csv, reviews.json, teams.json) is stored whole, with
a version number that goes up on every write; the dashboard's caches are keyed
on those versions. The activity log is append-only.
"""

import csv
import io
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

ACTIVITY_COLUMNS = ["When (UTC)", "Who", "Action", "Details"]


class LocalStore:
    kind = "files on this computer"

    def __init__(self, folder):
        self.dir = Path(folder)
        self._lock = threading.RLock()

    def _p(self, name):
        return self.dir / name

    def read(self, name):
        p = self._p(name)
        return p.read_bytes() if p.exists() else None

    def write(self, name, data):
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self._p(name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, self._p(name))

    def version(self, name):
        p = self._p(name)
        return p.stat().st_mtime_ns if p.exists() else 0

    def versions(self, names):
        return tuple(self.version(n) for n in names)

    def updated_at(self, name):
        p = self._p(name)
        return datetime.fromtimestamp(p.stat().st_mtime, timezone.utc) if p.exists() else None

    def log(self, who, action, details):
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            p = self._p("activity.csv")
            new = not p.exists()
            with open(p, "a", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(ACTIVITY_COLUMNS)
                w.writerow([datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"), who, action, details])

    def activity(self, limit):
        p = self._p("activity.csv")
        if not p.exists():
            return []
        with open(p, newline="") as f:
            rows = list(csv.reader(f))[1:]
        return [dict(zip(ACTIVITY_COLUMNS, r)) for r in reversed(rows)][:limit]


class PostgresStore:
    kind = "Postgres database"

    def __init__(self, url):
        self.url = url
        self._ready = False
        self._lock = threading.RLock()

    def _conn(self):
        import psycopg
        return psycopg.connect(self.url, autocommit=True, connect_timeout=15)

    def _setup(self):
        if self._ready:
            return
        with self._lock, self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS ctc_files (
                           name text PRIMARY KEY,
                           content bytea NOT NULL,
                           version bigint NOT NULL DEFAULT 1,
                           updated_at timestamptz NOT NULL DEFAULT now())""")
            c.execute("""CREATE TABLE IF NOT EXISTS ctc_activity (
                           id bigserial PRIMARY KEY,
                           at timestamptz NOT NULL DEFAULT now(),
                           who text, action text, details text)""")
        self._ready = True

    def read(self, name):
        self._setup()
        with self._conn() as c:
            row = c.execute("SELECT content FROM ctc_files WHERE name = %s", (name,)).fetchone()
        return bytes(row[0]) if row else None

    def write(self, name, data):
        self._setup()
        with self._conn() as c:
            c.execute("""INSERT INTO ctc_files (name, content) VALUES (%s, %s)
                         ON CONFLICT (name) DO UPDATE
                         SET content = EXCLUDED.content, version = ctc_files.version + 1,
                             updated_at = now()""", (name, data))

    def version(self, name):
        self._setup()
        with self._conn() as c:
            row = c.execute("SELECT version FROM ctc_files WHERE name = %s", (name,)).fetchone()
        return int(row[0]) if row else 0

    def versions(self, names):
        """Several versions in one round trip (used on every page load)."""
        self._setup()
        with self._conn() as c:
            rows = c.execute("SELECT name, version FROM ctc_files WHERE name = ANY(%s)", (list(names),)).fetchall()
        got = {n: int(v) for n, v in rows}
        return tuple(got.get(n, 0) for n in names)

    def updated_at(self, name):
        self._setup()
        with self._conn() as c:
            row = c.execute("SELECT updated_at FROM ctc_files WHERE name = %s", (name,)).fetchone()
        return row[0] if row else None

    def log(self, who, action, details):
        self._setup()
        with self._conn() as c:
            c.execute("INSERT INTO ctc_activity (who, action, details) VALUES (%s, %s, %s)",
                      (who, action, details))

    def activity(self, limit):
        self._setup()
        with self._conn() as c:
            rows = c.execute("SELECT at, who, action, details FROM ctc_activity ORDER BY id DESC LIMIT %s",
                             (limit,)).fetchall()
        return [dict(zip(ACTIVITY_COLUMNS, (a.strftime("%Y-%m-%d %H:%M"), w, ac, d))) for a, w, ac, d in rows]


def make_store(database_url, folder):
    if database_url:
        return PostgresStore(database_url)
    return LocalStore(folder)


def csv_bytes(df):
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")
