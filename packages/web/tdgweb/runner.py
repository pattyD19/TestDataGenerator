"""Run builds and report what they are doing.

Each build is a `tdg build` **subprocess**, not an in-process call. That buys
three things a thread could not:

  * cancel is a real kill. A CPU-bound encode loop cannot be interrupted
    cooperatively from another Python thread.
  * a crashed or OOM-killed build takes the server down with it — it doesn't.
  * resume comes free. The generator already checkpoints, so re-running the
    same command continues; cancel then resume is the same code path.

Progress is read from that checkpoint rather than scraped from stdout. The
checkpoint knows the exact bytes committed so far, which is what a progress
bar wants, and it stays accurate across a resume where a line count would not.
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
GENERATOR = os.path.join(REPO, "packages", "generator")
sys.path.insert(0, GENERATOR)

from tdg import checkpoint          # noqa: E402
from tdg import sizing              # noqa: E402

POLL_SECONDS = 0.4


# Lines the build prints while succeeding. None of them is ever the reason it
# failed, so reporting "photos 40 total 120 MB" as the error helps nobody.
_NOISE = ("Building", "  photo", "  video", "  pad", "  trim", "  resuming",
          "  job", "  files", "  target", "  actual", "  elapsed", "  manifest")


def _explain(tail, code):
    """The most useful line of a failed build's output.

    Two shapes to tell apart. A traceback ends with the exception, so its
    *last* line is the point. A deliberate SystemExit — "no seed pool at X,
    run bootstrap-seeds" — leads with the problem and follows with the
    remedy, so its *first* line is the point; taking the last would report
    the tail of a suggestion as though it were the error.
    """
    lines = [ln.rstrip() for ln in tail if ln.strip()]
    if not lines:
        return f"build exited with code {code}"
    if any(ln.startswith("Traceback (") for ln in lines):
        return lines[-1].strip()
    # Walk back over the trailing block that is not routine progress, and
    # report where that block starts.
    start = len(lines)
    while start > 0 and not lines[start - 1].startswith(_NOISE):
        start -= 1
    if start < len(lines):
        return lines[start].strip()
    return f"build exited with code {code}"


class Runner:
    """Owns the running builds and the listeners watching them."""

    def __init__(self, store, seeds_dir, python=None):
        self.store = store
        self.seeds_dir = seeds_dir
        self.python = python or sys.executable
        self._procs = {}                      # job id -> Popen
        self._subs = {}                       # job id -> [Queue]
        self._lock = threading.Lock()

    # -- events -------------------------------------------------------------

    def subscribe(self, jid):
        q = queue.Queue(maxsize=256)
        with self._lock:
            self._subs.setdefault(jid, []).append(q)
        return q

    def unsubscribe(self, jid, q):
        with self._lock:
            subs = self._subs.get(jid, [])
            if q in subs:
                subs.remove(q)

    def publish(self, jid, event):
        with self._lock:
            subs = list(self._subs.get(jid, []))
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass                          # a slow reader must not stall a build

    # -- running ------------------------------------------------------------

    def command(self, job):
        p = job["params"]
        cmd = [self.python, "-m", "tdg.cli", "build",
               "--size", str(p["size"]),
               "--out", job["pack_dir"],
               "--job", job["job_id"],
               "--profile", p.get("profile", "iphone-15-pro"),
               "--photo-fraction", str(p.get("photo_fraction", 0.70)),
               "--seed", str(p.get("seed", 1)),
               "--preset", p.get("preset", "ultrafast"),
               "--photo-format", p.get("photo_format", "jpeg"),
               "--video-codec", p.get("video_codec", "h264"),
               "--seeds", self.seeds_dir]
        if p.get("edge_cases"):
            cmd.append("--edge-cases")
        if p.get("since"):
            cmd += ["--since", p["since"]]
        if p.get("until"):
            cmd += ["--until", p["until"]]
        if p.get("min_clip") is not None:
            cmd += ["--min-clip", str(p["min_clip"])]
        if p.get("max_clip") is not None:
            cmd += ["--max-clip", str(p["max_clip"])]
        if p.get("jobs"):
            cmd += ["--jobs", str(p["jobs"])]
        return cmd

    def start(self, jid):
        job = self.store.get(jid)
        if job is None:
            raise KeyError(jid)
        with self._lock:
            if jid in self._procs:
                return job                    # already running
        os.makedirs(job["pack_dir"], exist_ok=True)
        # Unbuffered, or the child's stdout sits in a 8 KB block buffer while
        # stderr goes straight through: progress lines arrive in bursts and a
        # traceback lands *before* the banner that preceded it, so the last
        # line of output is not the error.
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        proc = subprocess.Popen(
            self.command(job), cwd=GENERATOR, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, start_new_session=True)
        with self._lock:
            self._procs[jid] = proc
        self.store.update(jid, status="running", started_at=time.time(),
                          message="building")
        self.publish(jid, {"type": "status", "status": "running"})
        threading.Thread(target=self._watch, args=(jid, proc), daemon=True).start()
        return self.store.get(jid)

    def cancel(self, jid):
        with self._lock:
            proc = self._procs.get(jid)
        if proc is None:
            return False
        try:
            os.killpg(os.getpgid(proc.pid), 15)
        except (ProcessLookupError, PermissionError):
            return False
        return True

    def is_running(self, jid):
        with self._lock:
            return jid in self._procs

    # -- the watcher --------------------------------------------------------

    def _watch(self, jid, proc):
        job = self.store.get(jid)
        stop = threading.Event()
        mon = threading.Thread(target=self._monitor, args=(jid, job, stop), daemon=True)
        mon.start()

        tail = []
        try:
            for line in proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                tail.append(line)
                del tail[:-40]
                self.publish(jid, {"type": "log", "line": line})
        finally:
            code = proc.wait()
            stop.set()
            mon.join(timeout=2)
            with self._lock:
                self._procs.pop(jid, None)
            self._finish(jid, code, tail)

    def _finish(self, jid, code, tail):
        manifest_path = os.path.join(self.store.get(jid)["pack_dir"], "manifest.json")
        if code == 0 and os.path.exists(manifest_path):
            with open(manifest_path) as fh:
                doc = json.load(fh)
            self.store.update(jid, status="done", finished_at=time.time(),
                              done_bytes=doc["total_bytes"],
                              file_count=doc["file_count"],
                              message=f"{doc['file_count']} files, "
                                      f"{sizing.human(doc['total_bytes'])}")
            self.publish(jid, {"type": "done", "file_count": doc["file_count"],
                               "total_bytes": doc["total_bytes"]})
            return

        # A cancel arrives as a signal, which shows up as a negative code. The
        # partial pack and its checkpoint are deliberately left in place: that
        # is what makes the job resumable rather than wasted.
        cancelled = code is not None and code < 0
        status = "cancelled" if cancelled else "failed"
        why = "cancelled — resume to continue" if cancelled else \
              _explain(tail, code)
        self.store.update(jid, status=status, finished_at=time.time(), message=why)
        self.publish(jid, {"type": status, "message": why})

    def _monitor(self, jid, job, stop):
        """Poll the build's checkpoint and publish byte-accurate progress."""
        ck = checkpoint.Checkpoint(job["pack_dir"])
        last = -1
        while not stop.is_set():
            head, items = ck.read()
            if head is not None:
                done = sum(i["bytes"] for i in items)
                if done != last:
                    last = done
                    self.store.update(jid, done_bytes=done, file_count=len(items))
                    self.publish(jid, {
                        "type": "progress",
                        "done_bytes": done,
                        "target_bytes": job["target_bytes"],
                        "file_count": len(items),
                        "phase": head.get("phase"),
                    })
            stop.wait(POLL_SECONDS)
