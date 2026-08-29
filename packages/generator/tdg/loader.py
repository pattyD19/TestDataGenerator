"""Put a built pack somewhere a gallery will index it, and take it back out.

Three targets, none of which needs a mobile app:

  * ``folder``    — a plain directory. CI, shared drives, and the thing you
                    point a desktop client at.
  * ``simulator`` — ``xcrun simctl addmedia``, which goes through the real
                    Photos import path, so ``DateTimeOriginal`` becomes the
                    asset's creationDate.
  * ``emulator``  — ``adb push`` into DCIM followed by an explicit MediaStore
                    scan. Pushing alone leaves files invisible to the gallery.

Every load writes a **receipt** outside the pack (``~/.tdg/receipts``) listing
what was written and where. Wipe reads the receipt, so a device can be cleaned
long after the pack that filled it has been deleted, and an interrupted load
resumes instead of starting over.
"""
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone

from . import manifest as manifest_mod
from . import sizing

RECEIPT_SCHEMA = 1
TARGETS = ("folder", "simulator", "emulator")

# adb and simctl both take many paths per invocation, and the per-invocation
# cost is tens of milliseconds — enough to matter across 26,000 files. Batch
# big enough to amortise it, small enough that progress stays honest and an
# interrupted run loses little.
PUSH_BATCH = 25
ADDMEDIA_BATCH = 100
SCAN_BATCH = 50

# A batch is also capped by how long a command line the far end will accept.
# adb passes the whole thing to the device's shell, and the limit is neither
# generous nor consistent across versions, so batches are trimmed by length as
# well as by count. Long filenames therefore shrink the batch instead of
# producing a truncated command that silently skips files.
MAX_SHELL_CHARS = 3000


def _batches(items, count_cap, length_of=None, char_cap=MAX_SHELL_CHARS):
    """Split into batches bounded by both item count and total command length."""
    batch, chars = [], 0
    for item in items:
        n = length_of(item) if length_of else 0
        if batch and (len(batch) >= count_cap or chars + n > char_cap):
            yield batch
            batch, chars = [], 0
        batch.append(item)
        chars += n
    if batch:
        yield batch


# ---------------------------------------------------------------- receipts --

def receipts_dir():
    return os.environ.get("TDG_RECEIPTS") or os.path.expanduser("~/.tdg/receipts")


def receipt_path(job_id, target, device):
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in (device or "local"))
    return os.path.join(receipts_dir(), f"{job_id}__{target}__{slug}.json")


def read_receipt(path):
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def write_receipt(doc):
    os.makedirs(receipts_dir(), exist_ok=True)
    path = receipt_path(doc["job_id"], doc["target"], doc.get("device"))
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(doc, fh, indent=2)
    os.replace(tmp, path)          # a receipt is never allowed to be half-written
    return path


def find_receipts(job_id=None, target=None, device=None):
    d = receipts_dir()
    if not os.path.isdir(d):
        return []
    out = []
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json"):
            continue
        doc = read_receipt(os.path.join(d, name))
        if not doc:
            continue
        if job_id and doc.get("job_id") != job_id:
            continue
        if target and doc.get("target") != target:
            continue
        if device and doc.get("device") != device:
            continue
        out.append(doc)
    return out


def load_manifest(pack_dir):
    path = os.path.join(pack_dir, "manifest.json")
    if not os.path.exists(path):
        raise SystemExit(f"no manifest.json in {pack_dir} — is that a pack directory?")
    with open(path) as fh:
        return json.load(fh)


# ------------------------------------------------------------------ shells --

def _run(cmd, check=True, capture=True):
    p = subprocess.run(cmd, capture_output=capture, text=True)
    if check and p.returncode != 0:
        detail = (p.stderr or p.stdout or "").strip()[-800:]
        raise RuntimeError(f"{' '.join(cmd[:3])}... failed ({p.returncode}): {detail}")
    return p


def adb(args, device=None, **kw):
    cmd = ["adb"]
    if device:
        cmd += ["-s", device]
    return _run(cmd + list(args), **kw)


def simctl(args, **kw):
    return _run(["xcrun", "simctl", *args], **kw)


def require_tool(name, hint):
    if shutil.which(name) is None:
        raise SystemExit(f"{name} not found on PATH — {hint}")


def require_simctl():
    """xcrun ships with the Command Line Tools; simctl only with full Xcode."""
    require_tool("xcrun", "this target only works on macOS with Xcode installed")
    if _run(["xcrun", "--find", "simctl"], check=False).returncode != 0:
        active = _run(["xcode-select", "-p"], check=False).stdout.strip()
        raise SystemExit(
            "simctl is unavailable — it ships with Xcode, not the Command Line "
            f"Tools.\n  xcode-select currently points at: {active or 'nothing'}\n"
            "  Install Xcode, then point the toolchain at it:\n"
            "    sudo xcode-select -s /Applications/Xcode.app\n"
            "  On an account without admin rights, set DEVELOPER_DIR instead — "
            "xcrun\n  honours it and it needs no password:\n"
            "    export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer")


def booted_simulators():
    require_simctl()
    p = simctl(["list", "devices", "booted", "-j"])
    doc = json.loads(p.stdout)
    out = []
    for runtime, devices in doc.get("devices", {}).items():
        for d in devices:
            if d.get("state") == "Booted":
                out.append({"udid": d["udid"], "name": d.get("name", "?"),
                            "runtime": runtime.rsplit(".", 1)[-1]})
    return out


def adb_devices():
    require_tool("adb", "install Android platform-tools")
    p = adb(["devices", "-l"], check=False)
    out = []
    for line in p.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            out.append({"serial": parts[0], "desc": " ".join(parts[2:])})
    return out


def resolve_device(target, device):
    """Turn an optional --device into a concrete one, or explain what's missing."""
    if target == "simulator":
        booted = booted_simulators()
        if device and device != "booted":
            return device
        if not booted:
            raise SystemExit("no booted simulator. Start one, or pass --device <udid>.\n"
                             "  xcrun simctl boot 'iPhone 15 Pro'")
        if len(booted) > 1 and not device:
            names = ", ".join(f"{b['name']} ({b['udid'][:8]})" for b in booted)
            raise SystemExit(f"several simulators booted — pass --device <udid>: {names}")
        return booted[0]["udid"]
    if target == "emulator":
        devs = adb_devices()
        if device:
            if device not in [d["serial"] for d in devs]:
                raise SystemExit(f"adb does not see {device!r}. Connected: "
                                 + (", ".join(d['serial'] for d in devs) or "none"))
            return device
        if not devs:
            raise SystemExit("adb sees no device. Start an emulator or plug in a handset "
                             "with USB debugging on.")
        if len(devs) > 1:
            raise SystemExit("several devices connected — pass --device <serial>: "
                             + ", ".join(d["serial"] for d in devs))
        return devs[0]["serial"]
    return None


# ---------------------------------------------------------------- preflight --

def free_bytes(target, device, dest=None):
    """Space available where the pack is about to land, or None if unknown.

    The plan is explicit that this is checked before a single byte is written:
    filling a device until it wedges is a slow, unhelpful failure.
    """
    if target == "folder":
        probe = dest
        while probe and not os.path.isdir(probe):
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
        return shutil.disk_usage(probe or ".").free
    if target == "simulator":
        # The simulator's Photos library lives on the host disk.
        return shutil.disk_usage(os.path.expanduser("~")).free
    if target == "emulator":
        p = adb(["shell", "df", "/sdcard"], device=device, check=False)
        for line in p.stdout.splitlines()[1:]:
            cols = line.split()
            if len(cols) >= 4 and cols[3].isdigit():
                return int(cols[3]) * 1024          # df reports 1K blocks
    return None


# -------------------------------------------------------------------- load --

def _pending(items, receipt, force):
    done = set() if force else {e["name"] for e in receipt.get("entries", []) if e.get("ok")}
    return [i for i in items if i["name"] not in done]


def _new_receipt(doc, pack_dir, target, device, dest):
    return {
        "receipt_schema": RECEIPT_SCHEMA,
        "job_id": doc["job_id"],
        "target": target,
        "device": device,
        "dest": dest,
        "album": doc.get("album"),
        "filename_prefix": doc.get("filename_prefix"),
        "pack_dir": os.path.abspath(pack_dir),
        "loaded_at": None,
        "entries": [],
    }


def load(pack_dir, target, device=None, dest=None, force=False, dry_run=False,
         limit=None, verify=False, progress=print):
    """Copy a pack onto a target and leave a receipt. Resumable and idempotent."""
    if target not in TARGETS:
        raise SystemExit(f"unknown target {target!r}; one of {', '.join(TARGETS)}")
    doc = load_manifest(pack_dir)
    device = resolve_device(target, device)

    if target == "folder":
        if not dest:
            raise SystemExit("--target folder needs --dest <directory>")
        dest = os.path.abspath(os.path.expanduser(dest))
    elif target == "emulator":
        dest = dest or f"/sdcard/DCIM/TDG_{doc['job_id']}"

    rpath = receipt_path(doc["job_id"], target, device)
    receipt = read_receipt(rpath) or _new_receipt(doc, pack_dir, target, device, dest)
    receipt["dest"] = dest
    receipt["pack_dir"] = os.path.abspath(pack_dir)

    items = _pending(doc["items"], receipt, force)
    if limit:
        items = items[:limit]
    already = doc["file_count"] - len(items)
    need = sum(i["bytes"] for i in items)

    progress(f"job {doc['job_id']}  ->  {target}"
             + (f" {device}" if device else "")
             + (f"  {dest}" if dest else ""))
    if already and not force:
        progress(f"  resuming: {already} of {doc['file_count']} already loaded")
    if not items:
        progress("  nothing to do — the receipt says this pack is already loaded.")
        return receipt

    avail = free_bytes(target, device, dest)
    progress(f"  {len(items)} files, {sizing.human(need)}"
             + (f"; {sizing.human(avail)} free" if avail is not None else "; free space unknown"))
    if avail is not None and avail < need:
        raise SystemExit(f"not enough space: need {sizing.human(need)}, "
                         f"have {sizing.human(avail)}")
    if avail is not None and avail < need * 1.05:
        progress("  warning: this fills the target to within 5% of full.")

    if dry_run:
        progress("  dry run — nothing written.")
        return receipt

    started = time.time()
    fn = {"folder": _load_folder, "simulator": _load_simulator,
          "emulator": _load_emulator}[target]
    entries = fn(pack_dir, items, dest, device, progress)

    by_name = {e["name"]: e for e in receipt["entries"]}
    for e in entries:
        by_name[e["name"]] = e
    receipt["entries"] = sorted(by_name.values(), key=lambda e: e["name"])
    receipt["loaded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = sum(e["bytes"] for e in receipt["entries"] if e.get("ok"))
    receipt["bytes_loaded"] = written
    path = write_receipt(receipt)

    dt = time.time() - started
    ok = sum(1 for e in entries if e.get("ok"))
    progress(f"  {ok}/{len(entries)} files in {dt:,.1f}s "
             f"({sizing.human(need / max(dt, .001))}/s)")
    if verify:
        bad = verify_load(pack_dir, receipt, progress)
        if bad:
            progress(f"  VERIFY FAILED on {len(bad)} files")
    progress(f"  receipt {path}")
    return receipt


def _entry(item, dest_path, ok=True):
    """One receipt line: everything wipe needs to remove exactly this asset.

    ``dest`` is the handle by which the asset can later be deleted, and its
    meaning is per-target: an absolute host path for ``folder``, a device path
    for ``emulator``, and — for the Phase 5 iOS app — the ``PHAsset``
    ``localIdentifier`` returned when the asset was created. It is deliberately
    *not* the filename: iOS renames every imported asset to ``IMG_NNNN``, so a
    filename-keyed wipe would find nothing there.

    ``simulator`` is the one target with no per-asset handle, because simctl
    can add media but cannot delete it; it stores a placeholder and wipe falls
    back to erasing the device.
    """
    return {"name": item["name"], "bytes": item["bytes"], "sha256": item["sha256"],
            "dest": dest_path, "ok": ok}


def _load_folder(pack_dir, items, dest, device, progress):
    os.makedirs(dest, exist_ok=True)
    entries = []
    for n, item in enumerate(items, 1):
        src = os.path.join(pack_dir, item["name"])
        dst = os.path.join(dest, item["name"])
        shutil.copy2(src, dst)      # copy2 keeps the mtime, which is the capture date
        entries.append(_entry(item, dst))
        if n % 200 == 0 or n == len(items):
            progress(f"    copied {n}/{len(items)}")
    for extra in ("manifest.json", "LICENSES.csv"):
        p = os.path.join(pack_dir, extra)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(dest, extra))
    return entries


def _load_simulator(pack_dir, items, dest, device, progress):
    """simctl addmedia runs the real Photos import, so EXIF dates survive."""
    require_simctl()
    entries, done = [], 0
    for i in range(0, len(items), ADDMEDIA_BATCH):
        batch = items[i:i + ADDMEDIA_BATCH]
        paths = [os.path.join(pack_dir, it["name"]) for it in batch]
        try:
            simctl(["addmedia", device, *paths])
            ok = True
        except RuntimeError as exc:
            progress(f"    batch at {i} failed: {exc}")
            ok = False
        entries += [_entry(it, "Photos", ok) for it in batch]
        done += len(batch)
        progress(f"    imported {done}/{len(items)}")
    return entries


def _load_emulator(pack_dir, items, dest, device, progress):
    """Push, then scan. A pushed file that is never scanned is invisible.

    ``content call ... scan_file`` is the supported route on Android 10+; the
    old MEDIA_SCANNER_SCAN_FILE broadcast was removed in Android 10.
    """
    require_tool("adb", "install Android platform-tools")
    adb(["shell", "mkdir", "-p", dest], device=device)
    entries, done, pushed = [], 0, []

    for i in range(0, len(items), PUSH_BATCH):
        batch = items[i:i + PUSH_BATCH]
        paths = [os.path.join(pack_dir, it["name"]) for it in batch]
        try:
            adb(["push", *paths, dest + "/"], device=device)
            ok = [True] * len(batch)
        except RuntimeError:
            # Multi-argument push is not universal across platform-tools
            # versions; fall back so an old toolchain degrades to slow, not
            # broken.
            ok = []
            for it, p in zip(batch, paths):
                r = adb(["push", p, f"{dest}/{it['name']}"], device=device, check=False)
                ok.append(r.returncode == 0)
        for it, good in zip(batch, ok):
            remote = f"{dest}/{it['name']}"
            entries.append(_entry(it, remote, good))
            if good:
                pushed.append(remote)
        done += len(batch)
        progress(f"    pushed {done}/{len(items)}")

    scan_paths(pushed, device, progress)
    return entries


SCAN_CMD = "content call --uri content://media/external/file --method scan_file --arg"


def scan_paths(paths, device, progress=print):
    """Ask MediaStore to index each path, batched into few adb round trips."""
    done = 0
    for batch in _batches(paths, SCAN_BATCH, lambda p: len(SCAN_CMD) + len(p) + 5):
        script = "; ".join(f"{SCAN_CMD} '{p}'" for p in batch)
        adb(["shell", script], device=device, check=False)
        done += len(batch)
        progress(f"    scanned {done}/{len(paths)}")


def verify_load(pack_dir, receipt, progress=print):
    """Re-hash what landed. Returns the entries that do not match."""
    target, device = receipt["target"], receipt.get("device")
    entries = [e for e in receipt["entries"] if e.get("ok")]
    bad = []
    if target == "folder":
        for e in entries:
            if not os.path.exists(e["dest"]) or \
                    manifest_mod.sha256_file(e["dest"]) != e["sha256"]:
                bad.append(e)
    elif target == "emulator":
        for batch in _batches(entries, SCAN_BATCH, lambda e: len(e["dest"]) + 1):
            p = adb(["shell", "sha256sum", *[e["dest"] for e in batch]],
                    device=device, check=False)
            got = {}
            for line in p.stdout.splitlines():
                parts = line.split(None, 1)
                if len(parts) == 2:
                    got[parts[1].strip()] = parts[0].strip()
            for e in batch:
                if got.get(e["dest"]) != e["sha256"]:
                    bad.append(e)
    else:
        progress("  verify: simulator imports are copied into the Photos library, "
                 "so the written bytes cannot be re-read. Skipped.")
        return []
    progress(f"  verified {len(entries) - len(bad)}/{len(entries)} files")
    return bad


# -------------------------------------------------------------------- wipe --

def wipe(job_id=None, target=None, device=None, dry_run=False, erase_device=False,
         progress=print):
    """Undo a load using its receipt. Nothing outside the receipt is touched."""
    receipts = find_receipts(job_id, target, device)
    if not receipts:
        raise SystemExit("no matching receipt in " + receipts_dir()
                         + " — nothing known to have been loaded.")
    for receipt in receipts:
        _wipe_one(receipt, dry_run, erase_device, progress)


def _wipe_one(receipt, dry_run, erase_device, progress):
    t, device = receipt["target"], receipt.get("device")
    entries = [e for e in receipt["entries"] if e.get("ok")]
    progress(f"job {receipt['job_id']}  {t}"
             + (f" {device}" if device else "")
             + f"  {len(entries)} files")
    if dry_run:
        progress("  dry run — nothing deleted.")
        return

    if t == "folder":
        gone = 0
        for e in entries:
            try:
                os.remove(e["dest"])
                gone += 1
            except FileNotFoundError:
                pass
        d = receipt.get("dest")
        for extra in ("manifest.json", "LICENSES.csv"):
            try:
                os.remove(os.path.join(d, extra))
            except (FileNotFoundError, TypeError):
                pass
        if d and os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
        progress(f"  removed {gone} files")

    elif t == "emulator":
        paths = [e["dest"] for e in entries]
        gone = 0
        for batch in _batches(paths, SCAN_BATCH, lambda p: len(p) + 1):
            adb(["shell", "rm", "-f", *batch], device=device, check=False)
            gone += len(batch)
            progress(f"    deleted {gone}/{len(paths)}")
        # Re-scanning a path that no longer exists is how MediaStore is told to
        # drop the row; without it the gallery keeps showing dead thumbnails.
        scan_paths(paths, device, progress)
        if receipt.get("dest"):
            adb(["shell", "rmdir", receipt["dest"]], device=device, check=False)

    elif t == "simulator":
        if not erase_device:
            progress("  simctl has no way to delete individual assets.\n"
                     "  Either remove the TDG album by hand in Photos, or re-run with\n"
                     "  --erase-device to erase the whole simulator (destroys ALL its\n"
                     "  data, not just this pack).")
            return
        progress(f"  erasing simulator {device} — all of its data, not just this pack")
        simctl(["shutdown", device], check=False)
        simctl(["erase", device])
        progress("  erased")

    path = receipt_path(receipt["job_id"], t, device)
    if os.path.exists(path):
        os.remove(path)
        progress(f"  receipt cleared {path}")
