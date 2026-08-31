"""Reclaiming disk space, which nothing else in the system does.

A pack is the only thing here that grows without bound. A 64 GB job leaves
64 GB on disk; the row that describes it is a few hundred bytes. So the two are
removed separately, and the distinction is the whole point of this module:

  * **prune** deletes the media and keeps the row. The job stays in the list
    with what was asked for, what it produced and when — but its status says
    `pruned`, so it stops pairing and stops serving instead of letting a phone
    discover the absence by failing halfway through a transfer.
  * **delete** removes both.

Neither one touches receipts. The pack is the *source*; the receipt written at
load time is what a wipe reads, and it lives on the device (or under
~/.tdg/receipts for CLI loads), never inside the pack. A device stays fully
cleanable long after the pack it came from is gone — which is the property that
makes reclaiming space safe at all.

A pruned job can still be rebuilt: the job id and seed are in the row, and the
generator is deterministic, so re-running it reproduces the same pack byte for
byte. Pruning costs build time, never information.
"""
import os
import shutil

from .runner import sizing

# Statuses whose pack is finished with. A running build is excluded here as
# well as by the live check in prune_job — the status alone is not proof, since
# a job that died with the server is left marked `failed` while its files stay.
PRUNABLE = ("done", "failed", "cancelled", "pruned")

# Written by tdg.checkpoint. Their presence means a partial build that `resume`
# could still continue, so deleting the pack throws away real work.
CHECKPOINT = (".tdg-build.json", ".tdg-build.jsonl")


class PruneError(Exception):
    """Refused. `code` is the HTTP status the API should answer with."""

    def __init__(self, message, code=409):
        super().__init__(message)
        self.code = code


def dir_size(path):
    """Bytes on disk, counting what the manifest does not — LICENSES.csv, the
    checkpoint, and anything else that came along."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass                     # vanished mid-walk; it frees nothing
    return total


def has_checkpoint(pack_dir):
    return all(os.path.exists(os.path.join(pack_dir, n)) for n in CHECKPOINT)


def _inside(packs_dir, pack_dir):
    """pack_dir comes out of the database and this module deletes recursively.

    A row written when the server ran with a different --packs, or edited by
    hand, must never make rmtree run somewhere else on the disk. Containment is
    checked against real paths so a symlink cannot step outside either, and the
    packs root itself is rejected: pruning one job must not empty all of them.
    """
    root = os.path.realpath(packs_dir)
    target = os.path.realpath(pack_dir)
    if target == root:
        return False
    try:
        return os.path.commonpath([root, target]) == root
    except ValueError:                   # different drives, on Windows
        return False


def _blocker(job, packs_dir, runner, include_partial):
    """Why this job's pack cannot be pruned, or None if it can."""
    if runner is not None and runner.is_running(job["id"]):
        return "still building"
    if job["status"] not in PRUNABLE:
        return f"status is {job['status']}"
    pack_dir = job["pack_dir"]
    if not _inside(packs_dir, pack_dir):
        return "pack directory is outside the packs root"
    if not os.path.isdir(pack_dir):
        return "nothing on disk"
    if has_checkpoint(pack_dir) and not include_partial:
        return "partial build — resuming it would restart from zero"
    return None


def survey(store, packs_dir, runner=None, include_partial=False):
    """Every job, what its pack costs, and whether it can be reclaimed.

    Walks each pack directory, so this is the on-demand endpoint rather than
    something the job list calls on every poll.
    """
    rows = []
    for job in store.list(limit=10_000):
        pack_dir = job["pack_dir"]
        present = os.path.isdir(pack_dir)
        blocker = _blocker(job, packs_dir, runner, include_partial)
        rows.append({
            "id": job["id"],
            "job_id": job["job_id"],
            "status": job["status"],
            "label": job["params"].get("label") or "",
            "pack_dir": pack_dir,
            "present": present,
            "bytes": dir_size(pack_dir) if present else 0,
            "resumable": present and has_checkpoint(pack_dir),
            "eligible": blocker is None,
            "reason": blocker,
        })
    return rows


def reclaimable(rows):
    return sum(r["bytes"] for r in rows if r["eligible"])


def prune_job(store, packs_dir, jid, drop_row=False, force=False, runner=None):
    """Delete one job's pack. With drop_row, the job row goes too.

    Deleting the files before the row matters: if the process dies between the
    two, the row is left pointing at an empty directory, which the next survey
    reports as "nothing on disk" and the UI can still clear. The reverse order
    would orphan gigabytes with nothing left to name them.
    """
    job = store.get(jid)
    if job is None:
        raise KeyError(jid)
    if runner is not None and runner.is_running(jid):
        raise PruneError("job is still building — cancel it first")
    if job["status"] not in PRUNABLE:
        raise PruneError(f"cannot prune a job whose status is {job['status']}")

    pack_dir = job["pack_dir"]
    if not _inside(packs_dir, pack_dir):
        raise PruneError(
            f"pack directory {pack_dir} is outside {packs_dir} — refusing to "
            "delete it", 400)

    freed, resumable = 0, False
    if os.path.isdir(pack_dir):
        resumable = has_checkpoint(pack_dir)
        if resumable and not force:
            raise PruneError(
                "this is a partial build — pruning discards its checkpoint, so "
                "resuming would restart from zero. Prune with force to discard it")
        freed = dir_size(pack_dir)
        shutil.rmtree(pack_dir)

    result = {"id": jid, "job_id": job["job_id"], "freed_bytes": freed,
              "was_resumable": resumable}
    if drop_row:
        store.delete(jid)
        result["row"] = "deleted"
        return result

    note = (f"pack pruned — {sizing.human(freed)} reclaimed" if freed
            else "pack was already gone")
    store.update(jid, status="pruned", message=note)
    result["row"] = "kept"
    result["message"] = note
    return result


def prune_all(store, packs_dir, runner=None, drop_rows=False, force=False):
    """Prune every eligible job, reporting what was skipped and why.

    One job that refuses must not abandon the rest — the point of a bulk prune
    is to get the disk back, so a blocked job is recorded and skipped.
    """
    pruned, skipped, freed = [], [], 0
    for row in survey(store, packs_dir, runner, include_partial=force):
        if not row["eligible"]:
            skipped.append({"id": row["id"], "job_id": row["job_id"],
                            "reason": row["reason"]})
            continue
        try:
            done = prune_job(store, packs_dir, row["id"], drop_rows, force, runner)
        except (PruneError, KeyError) as exc:
            skipped.append({"id": row["id"], "job_id": row["job_id"],
                            "reason": str(exc)})
            continue
        freed += done["freed_bytes"]
        pruned.append(done)
    return {"pruned": pruned, "skipped": skipped, "freed_bytes": freed,
            "freed_human": sizing.human(freed)}


def main(argv=None):
    """`python3 -m tdgweb.prune --packs ./packs` — the same thing without a browser."""
    import argparse
    from .store import Store

    p = argparse.ArgumentParser(prog="tdgweb.prune", description=__doc__.split("\n")[0])
    p.add_argument("--packs", default="./packs", help="where built packs live")
    p.add_argument("--db", default=None, help="default: <packs>/jobs.sqlite3")
    p.add_argument("--job", help="prune one job by id; default every eligible job")
    p.add_argument("--drop-rows", action="store_true",
                   help="remove the job from the list too, not just its files")
    p.add_argument("--force", action="store_true",
                   help="also prune partial builds, discarding their checkpoints")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would go and how much it would free")
    a = p.parse_args(argv)

    packs = os.path.abspath(a.packs)
    store = Store(a.db or os.path.join(packs, "jobs.sqlite3"))

    if a.dry_run or not a.job:
        rows = survey(store, packs, include_partial=a.force)
        for r in rows:
            mark = "prune" if r["eligible"] else "keep "
            why = "" if r["eligible"] else f"   ({r['reason']})"
            print(f"  {mark}  {r['job_id']}  {r['status']:<9} "
                  f"{sizing.human(r['bytes']):>9}{why}")
        print(f"\n  reclaimable: {sizing.human(reclaimable(rows))}")
        if a.dry_run:
            return 0

    if a.job:
        out = prune_job(store, packs, a.job, a.drop_rows, a.force)
        print(f"  {out['job_id']}: freed {sizing.human(out['freed_bytes'])}, "
              f"row {out['row']}")
        return 0

    out = prune_all(store, packs, drop_rows=a.drop_rows, force=a.force)
    print(f"\n  pruned {len(out['pruned'])} pack(s), freed {out['freed_human']}")
    for s in out["skipped"]:
        print(f"  skipped {s['job_id']}: {s['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
