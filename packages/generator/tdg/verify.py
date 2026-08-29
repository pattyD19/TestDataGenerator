"""`tdg verify` — did the fill actually land?

A load reporting success only means the bytes were handed over. What matters is
what the gallery did with them, and that is where platforms differ:

  * a pushed file that was never scanned is invisible on Android
  * an import can silently reinterpret the capture time, which is what the
    whole timezone chain in the generator exists to prevent
  * album grouping is the OEM's business, not the API's

So this asks the device, not the loader. Four questions, per the plan's §4:
how many assets the gallery indexed, whether the capture time survived, whether
album grouping held, and how long the fill took.

Verification is necessarily per-platform:

  * ``folder``    — the files, straight off disk
  * ``emulator``  — MediaStore, over adb. Works on a physical handset too
  * ``simulator`` — the Photos database on the host filesystem

A **physical iPhone has no equivalent**: the Photos database is not reachable
from outside the app, so verification there has to come from the loader app
itself. That gap is real and is reported rather than papered over.
"""
import datetime
import json
import os
import re
import shutil
import sqlite3
import tempfile

from . import loader, sizing

# Photos stores timestamps as seconds since 2001-01-01; MediaStore uses
# milliseconds since the Unix epoch.
APPLE_EPOCH = 978307200
TOLERANCE_SECONDS = 1.5      # sub-second truncation in the manifest, not drift


def _iso(dt):
    return dt.replace(microsecond=0).isoformat()


def _manifest_instants(doc):
    """{name: aware datetime} of what the pack says each asset was taken at."""
    out = {}
    for item in doc["items"]:
        raw = item.get("taken_at_utc") or item.get("taken_at")
        if not raw:
            continue
        try:
            d = datetime.datetime.fromisoformat(raw)
        except ValueError:
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=datetime.timezone.utc)
        out[item["name"]] = d.astimezone(datetime.timezone.utc)
    return out


# ------------------------------------------------------------------ probes --

def _probe_folder(doc, receipt, device):
    """The trivial case, and the control: the files as written."""
    found = {}
    for entry in receipt.get("entries", []):
        if not entry.get("ok"):
            continue
        path = entry["dest"]
        if os.path.exists(path):
            found[entry["name"]] = datetime.datetime.fromtimestamp(
                os.path.getmtime(path), datetime.timezone.utc)
    return {"indexed": found, "album": receipt.get("dest") or None,
            "album_supported": True, "album_note": None,
            "matched_by": "name"}


def _probe_emulator(doc, receipt, device):
    """Ask MediaStore what it indexed. Also valid against a real handset."""
    prefix = doc["filename_prefix"]
    found, albums = {}, set()
    for uri in ("content://media/external/images/media",
                "content://media/external/video/media"):
        q = (f"content query --uri {uri} "
             f"--projection _display_name:datetaken:relative_path "
             f"--where \"_display_name like '{prefix}%'\"")
        out = loader.adb(["shell", q], device=device, check=False).stdout
        for line in out.splitlines():
            m = re.search(r"_display_name=(\S+?), datetaken=(-?\d+)", line)
            if not m:
                continue
            name, taken = m.group(1), int(m.group(2))
            found[name] = datetime.datetime.fromtimestamp(
                taken / 1000, datetime.timezone.utc)
            a = re.search(r"relative_path=([^,]+)", line)
            if a:
                albums.add(a.group(1).strip().rstrip("/"))
    return {"indexed": found, "album": ", ".join(sorted(albums)) or None,
            "album_supported": True, "album_note": None,
            "matched_by": "name"}


def _sim_photos_db(udid):
    return os.path.expanduser(
        f"~/Library/Developer/CoreSimulator/Devices/{udid}"
        "/data/Media/PhotoData/Photos.sqlite")


def _probe_simulator(doc, receipt, device):
    """Read the simulator's Photos database.

    Matching is by **instant, not filename**: Photos renames every imported
    asset to IMG_NNNN, so the names in the manifest do not exist on the far
    side. The comparison is therefore between two multisets of capture times,
    windowed to the pack's own date range so the runtime's stock photos (2009
    to 2018) are not counted as ours.
    """
    db_path = _sim_photos_db(device)
    if not os.path.exists(db_path):
        raise SystemExit(f"no Photos database for simulator {device}")

    tmp = tempfile.mkdtemp(prefix="tdg-verify-")
    try:
        local = os.path.join(tmp, "Photos.sqlite")
        shutil.copy(db_path, local)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(db_path + suffix):
                shutil.copy(db_path + suffix, local + suffix)
        db = sqlite3.connect(local)

        want = _manifest_instants(doc)
        lo = min(want.values()) - datetime.timedelta(seconds=2)
        hi = max(want.values()) + datetime.timedelta(seconds=2)
        rows = db.execute(
            "SELECT ZDATECREATED, ZKIND FROM ZASSET "
            "WHERE ZTRASHEDSTATE = 0 AND ZDATECREATED BETWEEN ? AND ?",
            (lo.timestamp() - APPLE_EPOCH, hi.timestamp() - APPLE_EPOCH)
        ).fetchall()
        found = {}
        for i, (created, _kind) in enumerate(rows):
            found[f"asset-{i:05d}"] = datetime.datetime.fromtimestamp(
                created + APPLE_EPOCH, datetime.timezone.utc)

        album = None
        try:
            titles = [r[0] for r in db.execute(
                "SELECT ZTITLE FROM ZGENERICALBUM WHERE ZTITLE LIKE 'TDG %'"
            ).fetchall()]
            album = ", ".join(titles) or None
        except sqlite3.Error:
            pass
        # `simctl addmedia` imports into the library and offers no way to put
        # the assets in an album — that needs the Photos framework, which means
        # the loader app. Reporting this as a failed check would be wrong: the
        # pack and the fill are fine, the CLI route simply cannot group.
        return {"indexed": found, "album": album,
                "album_supported": album is not None,
                "album_note": None if album else
                              "simctl addmedia cannot create albums; "
                              "the iOS loader app does",
                "matched_by": "instant"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


PROBES = {"folder": _probe_folder, "emulator": _probe_emulator,
          "simulator": _probe_simulator}


# ------------------------------------------------------------------ verify --

def verify(pack_dir, target, device=None, receipt=None):
    """Compare what the manifest promised against what the device holds."""
    doc = loader.load_manifest(pack_dir)
    device = loader.resolve_device(target, device)
    if receipt is None:
        found = loader.find_receipts(doc["job_id"], target, device)
        receipt = found[0] if found else {}

    probe = PROBES[target](doc, receipt, device)
    want = _manifest_instants(doc)
    got = probe["indexed"]

    if probe["matched_by"] == "name":
        missing = sorted(set(want) - set(got))
        deltas = [abs((got[n] - want[n]).total_seconds())
                  for n in want if n in got]
    else:
        # Compare sorted instants pairwise; names do not survive the import.
        w = sorted(want.values())
        g = sorted(got.values())
        missing = [f"{len(w) - len(g)} asset(s) unaccounted for"] if len(g) < len(w) else []
        deltas = [abs((a - b).total_seconds()) for a, b in zip(g, w)]

    worst = max(deltas) if deltas else None
    preserved = sum(1 for d in deltas if d <= TOLERANCE_SECONDS)
    elapsed = receipt.get("elapsed_seconds") or 0.0

    return {
        "job_id": doc["job_id"],
        "target": target,
        "device": device,
        "verified_at": _iso(datetime.datetime.now(datetime.timezone.utc)),
        "expected_files": doc["file_count"],
        "expected_bytes": doc["total_bytes"],
        "photo_count": doc.get("photo_count"),
        "video_count": doc.get("video_count"),
        "indexed": len(got),
        "missing": missing[:10],
        "capture_time_preserved": preserved,
        "worst_delta_seconds": round(worst, 3) if worst is not None else None,
        "album": probe["album"],
        "album_supported": probe.get("album_supported", True),
        "album_note": probe.get("album_note"),
        "expected_album": doc.get("album"),
        "matched_by": probe["matched_by"],
        "elapsed_seconds": round(elapsed, 2),
        "throughput_bytes_per_second":
            round(doc["total_bytes"] / elapsed, 1) if elapsed else None,
        "passed": (len(got) == doc["file_count"]
                   and preserved == doc["file_count"]
                   and not missing),
    }


def summarise(r):
    """A human-readable block; the same numbers as the JSON."""
    tick = lambda ok: "PASS" if ok else "FAIL"
    indexed_ok = r["indexed"] == r["expected_files"]
    dates_ok = r["capture_time_preserved"] == r["expected_files"]
    lines = [
        f"job {r['job_id']}  ->  {r['target']} {r['device'] or ''}".rstrip(),
        f"  {r['expected_files']} files expected "
        f"({r['photo_count']} photos, {r['video_count']} videos), "
        f"{sizing.human(r['expected_bytes'])}",
        "",
        f"  assets indexed         {r['indexed']}/{r['expected_files']}"
        f"   [{tick(indexed_ok)}]",
        f"  capture time preserved {r['capture_time_preserved']}/{r['expected_files']}"
        + (f"   (worst delta {r['worst_delta_seconds']}s)" if r['worst_delta_seconds'] is not None else "")
        + f"   [{tick(dates_ok)}]",
        f"  album grouping         " + (
            f"{r['album']}   (expected {r['expected_album']})"
            if r["album_supported"] else f"n/a — {r['album_note']}"),
    ]
    if r["elapsed_seconds"]:
        rate = sizing.human(r["throughput_bytes_per_second"]) + "/s"
        lines.append(f"  fill took              {r['elapsed_seconds']:.1f}s  ({rate})")
    else:
        lines.append("  fill took              unknown (no receipt)")
    if r["missing"]:
        lines.append(f"  missing: {', '.join(str(m) for m in r['missing'])}")
    lines += ["", f"  {'PASSED' if r['passed'] else 'FAILED'}"]
    return "\n".join(lines)


def markdown(r, label=None):
    """A row-per-check report for docs/conformance/."""
    ok = lambda b: "yes" if b else "**no**"
    title = label or f"{r['target']} {r['device'] or ''}".strip()
    rate = (sizing.human(r["throughput_bytes_per_second"]) + "/s"
            if r["throughput_bytes_per_second"] else "—")
    return "\n".join([
        f"# Conformance — {title}",
        "",
        f"- job `{r['job_id']}` · {r['expected_files']} files "
        f"({r['photo_count']} photos, {r['video_count']} videos) · "
        f"{sizing.human(r['expected_bytes'])}",
        f"- verified {r['verified_at']}",
        f"- assets matched by {r['matched_by']}",
        "",
        "| Check | Result | |",
        "|---|---|---|",
        f"| assets indexed | {r['indexed']} / {r['expected_files']} | "
        f"{ok(r['indexed'] == r['expected_files'])} |",
        f"| capture time preserved | {r['capture_time_preserved']} / "
        f"{r['expected_files']}"
        + (f", worst delta {r['worst_delta_seconds']}s" if r['worst_delta_seconds'] is not None else "")
        + f" | {ok(r['capture_time_preserved'] == r['expected_files'])} |",
        (f"| album grouping | {r['album']} | "
         f"{ok(bool(r['album']))} |") if r["album_supported"] else
        (f"| album grouping | n/a — {r['album_note']} | — |"),
        f"| fill duration | {r['elapsed_seconds']:.1f}s ({rate}) | — |",
        "",
        f"**{'PASSED' if r['passed'] else 'FAILED'}**",
        "",
    ])
