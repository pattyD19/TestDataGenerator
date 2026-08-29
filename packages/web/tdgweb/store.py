"""Job state, in SQLite.

A job outlives the browser tab that created it and the process that served it:
a 64 GB build runs for hours, and the phone that eventually pulls the pack may
connect long after. So state goes on disk, not in memory.

sqlite3 is stdlib. One table is enough — jobs are independent, and the pack
directory holds the real output.
"""
import json
import os
import sqlite3
import threading
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL,       -- the TDG_<job_id>_ prefix in filenames
    token         TEXT NOT NULL,       -- pairing code, also gates pack access
    status        TEXT NOT NULL,       -- queued|running|done|failed|cancelled
    params        TEXT NOT NULL,       -- JSON, exactly what build was asked for
    target_bytes  INTEGER NOT NULL,
    done_bytes    INTEGER NOT NULL DEFAULT 0,
    file_count    INTEGER NOT NULL DEFAULT 0,
    pack_dir      TEXT NOT NULL,
    message       TEXT,
    created_at    REAL NOT NULL,
    started_at    REAL,
    finished_at   REAL
);
"""

STATUSES = ("queued", "running", "done", "failed", "cancelled")


def new_token():
    """A six-digit pairing code. Short enough to read off a screen and type."""
    return f"{uuid.uuid4().int % 1_000_000:06d}"


class Store:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self._lock = threading.Lock()
        # check_same_thread=False because the HTTP handler threads and the
        # build monitor thread all touch it; every access holds self._lock.
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def _row(self, row):
        if row is None:
            return None
        d = dict(row)
        d["params"] = json.loads(d["params"])
        return d

    def create(self, job_id, params, target_bytes, pack_dir):
        rec = {
            "id": uuid.uuid4().hex[:12],
            "job_id": job_id,
            "token": new_token(),
            "status": "queued",
            "params": json.dumps(params),
            "target_bytes": target_bytes,
            "pack_dir": os.path.abspath(pack_dir),
            "created_at": time.time(),
        }
        with self._lock:
            self._db.execute(
                "INSERT INTO jobs (id, job_id, token, status, params, target_bytes,"
                " pack_dir, created_at) VALUES (:id,:job_id,:token,:status,:params,"
                ":target_bytes,:pack_dir,:created_at)", rec)
            self._db.commit()
        return self.get(rec["id"])

    def get(self, jid):
        with self._lock:
            cur = self._db.execute("SELECT * FROM jobs WHERE id = ?", (jid,))
            return self._row(cur.fetchone())

    def by_token(self, token):
        """Find a job by its pairing code — how a phone turns six typed digits
        into a pack. Only completed jobs pair: there is nothing to hand a
        device until the manifest exists."""
        with self._lock:
            cur = self._db.execute(
                "SELECT * FROM jobs WHERE token = ? AND status = 'done' "
                "ORDER BY created_at DESC LIMIT 1", (token,))
            return self._row(cur.fetchone())

    def list(self, limit=100):
        with self._lock:
            cur = self._db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
            return [self._row(r) for r in cur.fetchall()]

    def update(self, jid, **fields):
        if not fields:
            return self.get(jid)
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        fields["id"] = jid
        with self._lock:
            self._db.execute(f"UPDATE jobs SET {sets} WHERE id = :id", fields)
            self._db.commit()
        return self.get(jid)

    def reap_running(self):
        """Mark jobs that were mid-build when the server died.

        Their packs are still on disk with a checkpoint, so they are resumable
        rather than lost — the UI offers exactly that instead of pretending
        they are still running.
        """
        with self._lock:
            self._db.execute(
                "UPDATE jobs SET status = 'failed', message = ? "
                "WHERE status IN ('running','queued')",
                ("interrupted when the server stopped — resume to continue",))
            self._db.commit()
