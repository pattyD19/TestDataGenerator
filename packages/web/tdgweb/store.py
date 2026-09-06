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

-- A copy of what a loader wrote onto a device.
--
-- The device holds the authoritative receipt; this is the backup that makes
-- the wipe promise survive the app. Uninstall the loader, reset the phone, or
-- lose the app any other way, and the localIdentifiers or content URIs that
-- name 64 GB of assets are gone with it — the media stays, and nothing can
-- name it precisely enough to remove it. "A test you cannot undo is a test you
-- run once" only holds if the receipt outlives the app that wrote it.
--
-- Keyed on (job, device) because one pack can be loaded onto several handsets
-- and each carries its own set of handles.
CREATE TABLE IF NOT EXISTS receipts (
    job          TEXT NOT NULL,       -- jobs.id
    device       TEXT NOT NULL,       -- stable per-device id from the loader
    platform     TEXT,                -- android | ios | cli
    device_name  TEXT,                -- human-readable, for choosing between them
    entries      TEXT NOT NULL,       -- JSON {file name: device-side handle}
    count        INTEGER NOT NULL,
    updated_at   REAL NOT NULL,
    PRIMARY KEY (job, device)
);
"""

# `pruned` is terminal like `done`, but means the row outlived its pack: the
# media was deleted to reclaim disk. Keeping it distinct from `done` is what
# stops a phone pairing with a job that has nothing left to serve.
STATUSES = ("queued", "running", "done", "failed", "cancelled", "pruned")


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

    def by_token_any(self, token):
        """Find a job by its code whatever state it is in.

        by_token() deliberately matches only finished packs, which is right for
        pairing but leaves the caller unable to tell "no such code" from "that
        pack was pruned". This answers the second question so the API can say
        which it is.
        """
        with self._lock:
            cur = self._db.execute(
                "SELECT * FROM jobs WHERE token = ? "
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

    def delete(self, jid):
        """Forget a job entirely, receipts included. The caller removes its pack
        first — see tdgweb.prune, which owns that order.

        Dropping the receipts here is deliberate and is why the API refuses to
        delete a job any device still carries unless forced: this is the one
        operation that can strand assets on a phone with nothing left to name
        them. Prune, the routine way to reclaim disk, never touches them.
        """
        with self._lock:
            self._db.execute("DELETE FROM receipts WHERE job = ?", (jid,))
            cur = self._db.execute("DELETE FROM jobs WHERE id = ?", (jid,))
            self._db.commit()
        return cur.rowcount > 0

    # -- receipts -----------------------------------------------------------

    def save_receipt(self, job, device, entries, platform=None, device_name=None):
        """Upsert one device's receipt for one job.

        Whole-receipt replacement rather than per-entry merge: the device is
        the source of truth and always sends its complete record, so a partial
        write here could only ever be a worse copy of it.
        """
        rec = {
            "job": job, "device": device,
            "platform": platform, "device_name": device_name,
            "entries": json.dumps(entries),
            "count": len(entries),
            "updated_at": time.time(),
        }
        with self._lock:
            self._db.execute(
                "INSERT INTO receipts (job, device, platform, device_name, entries,"
                " count, updated_at) VALUES (:job,:device,:platform,:device_name,"
                ":entries,:count,:updated_at) "
                "ON CONFLICT(job, device) DO UPDATE SET "
                "platform=excluded.platform, device_name=excluded.device_name,"
                "entries=excluded.entries, count=excluded.count,"
                "updated_at=excluded.updated_at", rec)
            self._db.commit()
        return self.get_receipt(job, device)

    def get_receipt(self, job, device):
        with self._lock:
            cur = self._db.execute(
                "SELECT * FROM receipts WHERE job = ? AND device = ?", (job, device))
            row = cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["entries"] = json.loads(d["entries"])
        return d

    def list_receipts(self, job):
        """Every device carrying this pack — without the entries, which can be
        hundreds of kilobytes and are not needed to choose between them."""
        with self._lock:
            cur = self._db.execute(
                "SELECT job, device, platform, device_name, count, updated_at "
                "FROM receipts WHERE job = ? ORDER BY updated_at DESC", (job,))
            return [dict(r) for r in cur.fetchall()]

    def delete_receipt(self, job, device):
        """Called when a device reports it has wiped. The record is only useful
        while assets are still on the device."""
        with self._lock:
            cur = self._db.execute(
                "DELETE FROM receipts WHERE job = ? AND device = ?", (job, device))
            self._db.commit()
        return cur.rowcount > 0

    def receipt_counts(self):
        """job id -> devices carrying it, for the job list.

        One grouped query rather than a COUNT per row: the list is polled every
        few seconds and most jobs have no receipts at all.
        """
        with self._lock:
            cur = self._db.execute(
                "SELECT job, COUNT(*) AS n FROM receipts GROUP BY job")
            return {r["job"]: r["n"] for r in cur.fetchall()}

    def count_receipts(self, job):
        with self._lock:
            cur = self._db.execute(
                "SELECT COUNT(*) FROM receipts WHERE job = ?", (job,))
            return cur.fetchone()[0]

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
