#!/usr/bin/env python3
"""End-to-end tests for `tdg load` / `tdg wipe`.

No third-party test runner, matching the rest of the package. Run it:

    python3 packages/generator/tests/test_loader.py

Builds one small real pack, then loads it to a folder and to a fake adb device
(see fakeadb.py), checking the things that actually break: resume after an
interruption, free-space refusal, checksum verification, and whether wipe
leaves anything behind.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GENERATOR = os.path.dirname(HERE)
sys.path.insert(0, GENERATOR)

from tdg import loader          # noqa: E402
from tdg.cli import main as cli  # noqa: E402

FAILURES = []


def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILURES.append(label)


def make_fake_adb(bin_dir, root, log=None, no_multi_push=False):
    """A directory containing an `adb` that is really fakeadb.py."""
    os.makedirs(bin_dir, exist_ok=True)
    os.makedirs(root, exist_ok=True)
    path = os.path.join(bin_dir, "adb")
    env = [f'export FAKEADB_ROOT="{root}"']
    if log:
        env.append(f'export FAKEADB_LOG="{log}"')
    if no_multi_push:
        env.append('export FAKEADB_NO_MULTI_PUSH=1')
    with open(path, "w") as fh:
        fh.write("#!/bin/sh\n" + "\n".join(env)
                 + f'\nexec "{sys.executable}" "{HERE}/fakeadb.py" "$@"\n')
    os.chmod(path, 0o755)
    return bin_dir


def make_fake_xcrun(bin_dir, root, log=None, mode="booted"):
    """A directory containing an `xcrun` that is really fakexcrun.py."""
    os.makedirs(bin_dir, exist_ok=True)
    os.makedirs(root, exist_ok=True)
    path = os.path.join(bin_dir, "xcrun")
    env = [f'export FAKEXCRUN_ROOT="{root}"', f'export FAKEXCRUN_MODE="{mode}"']
    if log:
        env.append(f'export FAKEXCRUN_LOG="{log}"')
    with open(path, "w") as fh:
        fh.write("#!/bin/sh\n" + "\n".join(env)
                 + f'\nexec "{sys.executable}" "{HERE}/fakexcrun.py" "$@"\n')
    os.chmod(path, 0o755)
    return bin_dir


def build_pack(out):
    cli(["build", "--size", "6MB", "--photo-fraction", "1.0",
         "--out", out, "--job", "testjob", "--quiet"])
    with open(os.path.join(out, "manifest.json")) as fh:
        return json.load(fh)


def quiet(_msg):
    pass


def main():
    tmp = tempfile.mkdtemp(prefix="tdg-test-")
    os.environ["TDG_RECEIPTS"] = os.path.join(tmp, "receipts")
    pack = os.path.join(tmp, "pack")

    print("build a pack")
    doc = build_pack(pack)
    check(doc["total_bytes"] == doc["target_bytes"], "pack lands on its exact target")
    check(doc["file_count"] > 1, f"pack has {doc['file_count']} files")

    # ---- folder target ----------------------------------------------------
    print("folder target")
    dest = os.path.join(tmp, "dest")
    loader.load(pack, "folder", dest=dest, dry_run=True, progress=quiet)
    check(not os.path.exists(dest), "dry run writes nothing")

    r = loader.load(pack, "folder", dest=dest, limit=1, progress=quiet)
    check(len(os.listdir(dest)) == 3, "partial load wrote 1 file plus manifest+licenses")

    r = loader.load(pack, "folder", dest=dest, progress=quiet)
    check(sum(1 for e in r["entries"] if e["ok"]) == doc["file_count"],
          "resume completes the remaining files")
    on_disk = {f for f in os.listdir(dest) if f.startswith("TDG_")}
    check(on_disk == {i["name"] for i in doc["items"]}, "every pack file is on disk")

    mtimes_match = all(
        int(os.path.getmtime(os.path.join(dest, i["name"])))
        == int(os.path.getmtime(os.path.join(pack, i["name"])))
        for i in doc["items"])
    check(mtimes_match, "capture-date mtimes survive the copy")

    check(loader.verify_load(pack, r, progress=quiet) == [], "checksums verify")

    r2 = loader.load(pack, "folder", dest=dest, progress=quiet)
    check(len(r2["entries"]) == doc["file_count"], "second load is a no-op, not a duplicate")

    # A receipt must outlive the pack: that is the whole reason it is not
    # stored inside the pack directory.
    print("wipe from receipt alone")
    shutil.rmtree(pack)
    loader.wipe(job_id="testjob", target="folder", progress=quiet)
    check(not os.path.exists(dest), "wipe removed the files and the empty directory")
    check(loader.find_receipts("testjob") == [], "wipe cleared the receipt")

    doc = build_pack(pack)          # rebuild for the remaining tests

    # ---- free space refusal ----------------------------------------------
    print("preflight")
    real_free = loader.free_bytes
    loader.free_bytes = lambda *a, **k: 1024
    try:
        loader.load(pack, "folder", dest=os.path.join(tmp, "nope"), progress=quiet)
        check(False, "refuses to write when the target is too small")
    except SystemExit as exc:
        check("not enough space" in str(exc),
              "refuses to write when the target is too small")
    finally:
        loader.free_bytes = real_free
    check(not os.path.exists(os.path.join(tmp, "nope")),
          "nothing was written before the space check")

    # ---- emulator target, via fake adb ------------------------------------
    print("emulator target (fake adb)")
    bin_dir = make_fake_adb(os.path.join(tmp, "bin"), os.path.join(tmp, "device"),
                            log=os.path.join(tmp, "adb.log"))
    os.environ["PATH"] = bin_dir + os.pathsep + os.environ["PATH"]

    r = loader.load(pack, "emulator", progress=quiet)
    remote = f"/sdcard/DCIM/{doc['album']}"   # the album the manifest declares
    landed = os.listdir(os.path.join(tmp, "device", remote.lstrip("/")))
    check(set(landed) == {i["name"] for i in doc["items"]}, "every file reached the device")
    check(r["dest"] == remote,
          f"the CLI files a pack under the manifest's album ({remote}), "
          "so it agrees with the Android app")

    log = open(os.path.join(tmp, "adb.log")).read()
    scanned = sum(1 for i in doc["items"] if f"--arg '{remote}/{i['name']}'" in log)
    check(scanned == doc["file_count"],
          "every pushed file was handed to MediaStore (an unscanned file is invisible)")

    check(loader.verify_load(pack, r, progress=quiet) == [],
          "checksums verify over adb sha256sum")

    print("emulator target with old platform-tools (no multi-file push)")
    make_fake_adb(bin_dir, os.path.join(tmp, "device2"),
                  log=os.path.join(tmp, "adb2.log"), no_multi_push=True)
    r = loader.load(pack, "emulator", force=True, progress=quiet)
    landed = os.listdir(os.path.join(tmp, "device2", remote.lstrip("/")))
    check(set(landed) == {i["name"] for i in doc["items"]},
          "per-file fallback pushes everything when batch push is rejected")

    print("wipe the device")
    loader.wipe(job_id="testjob", target="emulator", progress=quiet)
    check(not os.path.exists(os.path.join(tmp, "device2", remote.lstrip("/"))),
          "wipe removed the remote directory")
    log2 = open(os.path.join(tmp, "adb2.log")).read()
    check(log2.count("scan_file") >= doc["file_count"] * 2,
          "deleted paths were re-scanned so MediaStore drops the rows")

    # ---- tdg verify -------------------------------------------------------
    # The harness asks the *device* what it indexed rather than trusting the
    # loader's own success report, so it has to be exercised against something
    # that can answer.
    print("tdg verify")
    from tdg import verify as vmod

    dest2 = os.path.join(tmp, "vdest")
    rf = loader.load(pack, "folder", dest=dest2, progress=quiet)
    rep = vmod.verify(pack, "folder")
    check(rep["passed"], "folder verify passes after a good load")
    check(rep["indexed"] == doc["file_count"],
          f"folder verify counts every asset ({rep['indexed']}/{doc['file_count']})")
    check(rep["capture_time_preserved"] == doc["file_count"],
          "and finds every capture time intact")

    victim = os.path.join(dest2, doc["items"][0]["name"])
    os.remove(victim)
    rep = vmod.verify(pack, "folder")
    check(not rep["passed"], "verify fails when an asset is missing from the device")
    check(doc["items"][0]["name"] in rep["missing"],
          "and names the asset that went astray")
    loader.wipe(job_id="testjob", target="folder", progress=quiet)

    # The device was wiped a few steps ago, so put the pack back first.
    loader.load(pack, "emulator", force=True, progress=quiet)
    rep = vmod.verify(pack, "emulator")
    check(rep["indexed"] == doc["file_count"],
          f"emulator verify reads MediaStore ({rep['indexed']}/{doc['file_count']})")
    check(rep["capture_time_preserved"] == doc["file_count"],
          "and DATE_TAKEN matches the manifest")
    check(rep["album"] and doc["album"] in rep["album"],
          f"and the album is the manifest's ({rep['album']})")
    check(vmod.markdown(rep).startswith("# Conformance"),
          "a markdown report is produced for docs/conformance/")

    # ---- shell command length ---------------------------------------------
    # adb hands the whole string to the device's shell, whose length limit is
    # neither generous nor consistent. A batch that overflows it would be
    # truncated, silently skipping files — so batches are capped by length too.
    print("shell command length")
    long_paths = [f"/sdcard/DCIM/TDG_x/{'n' * 200}_{i}.jpg" for i in range(40)]
    lens = [len("; ".join(f"{loader.SCAN_CMD} '{p}'" for p in b))
            for b in loader._batches(long_paths, loader.SCAN_BATCH,
                                     lambda p: len(loader.SCAN_CMD) + len(p) + 5)]
    check(max(lens) <= loader.MAX_SHELL_CHARS,
          f"long filenames shrink the scan batch (longest command {max(lens)} chars)")
    check(sum(len(b) for b in loader._batches(long_paths, loader.SCAN_BATCH,
                                              lambda p: len(p) + 70)) == len(long_paths),
          "no path is dropped when batches are split by length")
    check(len(list(loader._batches(range(100), 10))) == 10,
          "count cap still applies when no length function is given")

    # ---- simulator target, via fake xcrun ---------------------------------
    print("simulator target (fake xcrun)")
    sim_root = os.path.join(tmp, "sim")
    make_fake_xcrun(bin_dir, sim_root, log=os.path.join(tmp, "xcrun.log"))
    udid = "11111111-1111-1111-1111-111111111111"

    check(loader.resolve_device("simulator", None) == udid,
          "a single booted simulator is picked automatically")

    r = loader.load(pack, "simulator", progress=quiet)
    imported = os.listdir(os.path.join(sim_root, udid, "Photos"))
    check(set(imported) == {i["name"] for i in doc["items"]},
          "every file was imported through addmedia")
    check(all(e["ok"] for e in r["entries"]), "receipt records the import")

    print("simulator device resolution")
    make_fake_xcrun(bin_dir, sim_root, mode="none")
    try:
        loader.resolve_device("simulator", None)
        check(False, "no booted simulator is an actionable error")
    except SystemExit as exc:
        check("no booted simulator" in str(exc),
              "no booted simulator is an actionable error")

    make_fake_xcrun(bin_dir, sim_root, mode="two")
    try:
        loader.resolve_device("simulator", None)
        check(False, "two booted simulators demands an explicit --device")
    except SystemExit as exc:
        check("pass --device" in str(exc),
              "two booted simulators demands an explicit --device")

    print("simulator wipe")
    make_fake_xcrun(bin_dir, sim_root, mode="booted")
    loader.wipe(job_id="testjob", target="simulator", progress=quiet)
    check(os.path.isdir(os.path.join(sim_root, udid, "Photos")),
          "wipe without --erase-device does not touch the simulator")
    check(len(loader.find_receipts("testjob", "simulator")) == 1,
          "the receipt survives, because nothing was actually removed")

    loader.wipe(job_id="testjob", target="simulator", erase_device=True, progress=quiet)
    check(not os.path.exists(os.path.join(sim_root, udid)),
          "--erase-device erases the simulator")
    check(loader.find_receipts("testjob", "simulator") == [],
          "and clears the receipt")

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
