"""Crash-safe build state, so a long build can be resumed rather than restarted.

A 64 GB pack is one to two hours of CPU. Losing it to a closed laptop, a full
disk or a stray Ctrl-C is the difference between a tool people use and one they
schedule around. `--job` + `--seed` reproduce a pack from scratch, which is not
the same thing.

Resuming an *exact-size* build needs more than the list of files already
written. The planner learns as it goes — the real bytes-per-second of the video
encoder, the largest photo seen so far — and it draws from a seeded RNG whose
position determines every subsequent file. Restoring the file list alone would
produce a different pack than an uninterrupted run.

So the checkpoint carries three things: the items completed, the scalar state
the planner learned, and the RNG position.

Layout, two files in the output directory:

  .tdg-build.jsonl   append-only, one completed item per line
  .tdg-build.json    header: parameter fingerprint, scalar state, RNG, count

The count in the header is what makes this crash-safe. Items are appended and
flushed *before* the header is rewritten, so a kill at any moment leaves either
a header that names fewer items than the log holds (extra lines are ignored) or
a header that matches. It can never name more items than exist.
"""
import hashlib
import json
import os

STATE_VERSION = 1
LOG_NAME = ".tdg-build.jsonl"
HEAD_NAME = ".tdg-build.json"


def fingerprint(**params):
    """Identify the build these files belong to.

    Resuming into a checkpoint from different parameters would silently produce
    a pack that matches neither run, so the fingerprint covers everything that
    changes the output.
    """
    blob = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class Checkpoint:
    def __init__(self, out_dir):
        self.log_path = os.path.join(out_dir, LOG_NAME)
        self.head_path = os.path.join(out_dir, HEAD_NAME)
        self._log = None
        self._count = 0

    # -- reading ------------------------------------------------------------

    def exists(self):
        return os.path.exists(self.head_path) and os.path.exists(self.log_path)

    def read(self):
        """Return (head, items), or (None, []) if there is nothing usable."""
        if not self.exists():
            return None, []
        try:
            with open(self.head_path) as fh:
                head = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None, []
        if head.get("state_version") != STATE_VERSION:
            return None, []

        items = []
        want = head.get("item_count", 0)
        with open(self.log_path) as fh:
            for line in fh:
                if len(items) >= want:
                    break                    # written after the last header
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    break                    # torn final line
        if len(items) != want:
            return None, []                  # log is shorter than claimed
        return head, items

    def verify(self, out_dir, items):
        """Names whose file is missing or the wrong size.

        Cheap on purpose: sizes, not checksums. A 64 GB re-hash on every resume
        would cost more than the encoding that was lost.
        """
        bad = []
        for it in items:
            path = os.path.join(out_dir, it["name"])
            try:
                if os.path.getsize(path) != it["bytes"]:
                    bad.append(it["name"])
            except OSError:
                bad.append(it["name"])
        return bad

    # -- writing ------------------------------------------------------------

    def open_log(self, existing_count=0):
        self._count = existing_count
        self._log = open(self.log_path, "a")

    def append(self, items):
        for it in items:
            self._log.write(json.dumps(it) + "\n")
            self._count += 1
        self._log.flush()
        os.fsync(self._log.fileno())

    def commit(self, **state):
        """Publish the header. Only items already flushed to the log count."""
        head = dict(state)
        head["state_version"] = STATE_VERSION
        head["item_count"] = self._count
        tmp = self.head_path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(head, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.head_path)

    def close(self):
        if self._log:
            self._log.close()
            self._log = None

    def clear(self):
        self.close()
        for p in (self.log_path, self.head_path, self.head_path + ".tmp"):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass


def rng_state_to_json(state):
    version, internal, gauss = state
    return [version, list(internal), gauss]


def rng_state_from_json(blob):
    version, internal, gauss = blob
    return (version, tuple(internal), gauss)


def sweep_orphans(out_dir, job_id, keep_names):
    """Delete files from the interrupted batch that no checkpoint accounts for.

    An interrupted run leaves half-written JPEGs and MP4s behind. They are not
    in the manifest, so they would never be loaded — but they occupy the
    filename slots the resumed build is about to reuse, and they inflate the
    directory for anyone eyeballing it.
    """
    prefix = f"TDG_{job_id}_"
    removed = 0
    for name in os.listdir(out_dir):
        if name.startswith(prefix) and name not in keep_names:
            try:
                os.remove(os.path.join(out_dir, name))
                removed += 1
            except OSError:
                pass
    return removed
