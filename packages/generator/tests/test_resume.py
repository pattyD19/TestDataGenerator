#!/usr/bin/env python3
"""Tests for interrupted builds.

A 64 GB pack is one to two hours of CPU, so losing it to a closed laptop is
expensive. The bar is not just "it finishes" — a resumed build must produce
*the same pack* an uninterrupted run would, because a failing test that cannot
be reproduced byte for byte is not much of a test.

The interesting cases are the failure modes: a half-written file from the
batch that was in flight, a checkpoint from different settings, and a
checkpoint whose files someone has since deleted.

Run it:

    python3 packages/generator/tests/test_resume.py
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GENERATOR = os.path.dirname(HERE)
sys.path.insert(0, GENERATOR)

from tdg import checkpoint                    # noqa: E402
from tdg.cli import main as cli                # noqa: E402

FAILURES = []
COMMON = ["--since", "2023-01-01", "--until", "2024-01-01", "--photo-fraction", "1.0"]


def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILURES.append(label)


def build(out, size="120MB", job="rjob", seed="5", extra=()):
    cli(["build", "--size", size, "--out", out, "--job", job, "--seed", seed,
         *COMMON, *extra, "--quiet"])
    with open(os.path.join(out, "manifest.json")) as fh:
        return json.load(fh)


def key(doc):
    return [(i["name"], i["sha256"], i["taken_at"], i["bytes"]) for i in doc["items"]]


def kill_mid_build(out, size, job, seed, ready=None, args=None, timeout=180):
    """Run a build in a subprocess and SIGKILL it part-way through.

    SIGKILL rather than SIGINT on purpose: no cleanup handler runs, so this is
    the worst case — whatever is on disk is what a yanked power cord leaves.

    The moment to kill is chosen by watching the checkpoint rather than by
    sleeping a fixed time: a 4K clip takes far longer to encode than a JPEG,
    and a machine slower or faster than this one would otherwise be killed in
    the wrong phase, or before anything had been written at all.
    """
    ready = ready or (lambda head, items: len(items) >= 3)
    proc = subprocess.Popen(
        [sys.executable, "-m", "tdg.cli", "build", "--size", size, "--out", out,
         "--job", job, "--seed", seed, *(args if args is not None else COMMON)],
        cwd=GENERATOR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    ck = checkpoint.Checkpoint(out)
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            if proc.poll() is not None:
                break                       # finished before we could kill it
            head, items = ck.read()
            if head is not None and ready(head, items):
                break
            time.sleep(0.2)
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=30)


def main():
    tmp = tempfile.mkdtemp(prefix="tdg-resume-")
    ref = os.path.join(tmp, "ref")
    cut = os.path.join(tmp, "cut")

    print("reference build")
    ref_doc = build(ref, size="120MB")
    check(ref_doc["total_bytes"] == ref_doc["target_bytes"], "reference lands exactly")
    check(not os.path.exists(os.path.join(ref, checkpoint.HEAD_NAME)),
          "a completed build leaves no checkpoint behind")

    print("interrupted build")
    kill_mid_build(cut, "120MB", "rjob", "5")
    partial = [f for f in os.listdir(cut) if f.startswith("TDG_")]
    ck = checkpoint.Checkpoint(cut)
    check(ck.exists(), "a checkpoint survives SIGKILL")
    check(not os.path.exists(os.path.join(cut, "manifest.json")),
          "no manifest is written for an incomplete build")
    check(0 < len(partial) < ref_doc["file_count"],
          f"the build really was cut short ({len(partial)} of "
          f"{ref_doc['file_count']} files)")

    head, done = ck.read()
    check(head is not None and len(done) <= len(partial),
          "the checkpoint never claims more files than exist on disk")

    print("resume")
    resumed = build(cut, size="120MB")
    check(key(resumed) == key(ref_doc),
          "a resumed pack is byte-for-byte the pack an uninterrupted run makes")
    check(resumed["total_bytes"] == resumed["target_bytes"],
          "and still lands on the exact target")
    check(not os.path.exists(os.path.join(cut, checkpoint.HEAD_NAME)),
          "and clears its checkpoint when it finishes")

    # ---- orphans ----------------------------------------------------------
    print("orphaned files from the interrupted batch")
    orph = os.path.join(tmp, "orph")
    kill_mid_build(orph, "120MB", "rjob", "5")
    head, done = checkpoint.Checkpoint(orph).read()
    known = {i["name"] for i in done}
    # Plant a file the checkpoint does not account for, exactly as a killed
    # encoder would leave behind.
    planted = os.path.join(orph, f"TDG_rjob_{99998:05d}.jpg")
    with open(planted, "wb") as fh:
        fh.write(b"\xff\xd8truncated")
    stray = [f for f in os.listdir(orph) if f.startswith("TDG_") and f not in known]
    check(len(stray) >= 1, f"{len(stray)} unaccounted file(s) present before resume")
    build(orph, size="120MB")
    check(not os.path.exists(planted), "resume sweeps away files no checkpoint knows")
    final = {f for f in os.listdir(orph) if f.startswith("TDG_")}
    check(final == {i["name"] for i in ref_doc["items"]},
          "and the finished directory holds exactly the manifest's files")

    # ---- wrong settings ---------------------------------------------------
    print("checkpoint from different settings")
    mism = os.path.join(tmp, "mism")
    kill_mid_build(mism, "120MB", "rjob", "5")
    try:
        build(mism, size="200MB")            # different target
        check(False, "refuses to resume into a build with different settings")
    except SystemExit as exc:
        check("different settings" in str(exc),
              "refuses to resume into a build with different settings")
    check(checkpoint.Checkpoint(mism).exists(),
          "and leaves the partial build alone rather than destroying it")

    print("--restart")
    restarted = build(mism, size="200MB", extra=["--restart"])
    check(restarted["total_bytes"] == restarted["target_bytes"],
          "--restart discards the mismatched checkpoint and builds cleanly")

    # ---- missing files ----------------------------------------------------
    print("checkpoint whose files have gone")
    gone = os.path.join(tmp, "gone")
    kill_mid_build(gone, "120MB", "rjob", "5")
    head, done = checkpoint.Checkpoint(gone).read()
    os.remove(os.path.join(gone, done[0]["name"]))
    try:
        build(gone, size="120MB")
        check(False, "refuses to resume when a recorded file has been deleted")
    except SystemExit as exc:
        check("missing or truncated" in str(exc),
              "refuses to resume when a recorded file has been deleted")

    # A truncated file is the likelier real failure: the disk filled up.
    head, done = checkpoint.Checkpoint(gone).read()
    victim = os.path.join(gone, done[1]["name"])
    with open(victim, "r+b") as fh:
        fh.truncate(done[1]["bytes"] // 2)
    try:
        build(gone, size="120MB")
        check(False, "refuses to resume when a recorded file is truncated")
    except SystemExit as exc:
        check("missing or truncated" in str(exc),
              "refuses to resume when a recorded file is truncated")

    print("torn checkpoint log")
    # A kill between appending items and rewriting the header leaves extra
    # lines. They must be ignored, not half-parsed.
    torn = os.path.join(tmp, "torn")
    kill_mid_build(torn, "120MB", "rjob", "5")
    ck = checkpoint.Checkpoint(torn)
    head, done = ck.read()
    with open(ck.log_path, "a") as fh:
        fh.write(json.dumps({"name": "TDG_rjob_99999.jpg", "bytes": 1}) + "\n")
        fh.write('{"name": "TDG_rjob_99999.jp')       # torn final line
    head2, done2 = ck.read()
    check(len(done2) == len(done),
          "lines written after the last header are ignored, torn line included")
    resumed_torn = build(torn, size="120MB")
    check(key(resumed_torn) == key(ref_doc),
          "and the pack still comes out identical")

    # ---- the video phase --------------------------------------------------
    # The hardest state to restore. The planner *learns* the encoder's real
    # bytes-per-second from the first clip and prices everything after on it,
    # so a resume that forgot it would re-derive a different rate and land a
    # different pack — while still hitting the target, which is what makes it
    # worth testing rather than assuming.
    print("resume across the video phase")
    if shutil.which("ffmpeg") is None:
        print("  skip  (no ffmpeg on this machine)")
    else:
        vid_common = ["--since", "2023-01-01", "--until", "2024-01-01",
                      "--min-clip", "4", "--max-clip", "6"]

        def vbuild(out, extra=()):
            cli(["build", "--size", "260MB", "--out", out, "--job", "vjob",
                 "--seed", "11", *vid_common, *extra, "--quiet"])
            with open(os.path.join(out, "manifest.json")) as fh:
                return json.load(fh)

        vref = os.path.join(tmp, "vref")
        vcut = os.path.join(tmp, "vcut")
        vref_doc = vbuild(vref)
        check(vref_doc["video_count"] >= 1,
              f"reference has video to interrupt ({vref_doc['video_count']} clips)")

        # Kill once at least one clip has been checkpointed, so the resume
        # genuinely has to restore a learned encoder rate.
        kill_mid_build(vcut, "260MB", "vjob", "11", args=vid_common,
                       ready=lambda head, items:
                           any(i["kind"] == "video" for i in items))

        head, done = checkpoint.Checkpoint(vcut).read()
        if head is None:
            print("  skip  (kill landed before the first checkpoint)")
        else:
            check(head["bytes_per_sec"] > 0,
                  f"the learned encoder rate is checkpointed "
                  f"({head['bytes_per_sec']:,.0f} B/s)")
            vres = vbuild(vcut)
            check(key(vres) == key(vref_doc),
                  "a pack interrupted during video resumes byte-for-byte identical")
            check(vres["video_count"] == vref_doc["video_count"],
                  "with the same number of clips")

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
