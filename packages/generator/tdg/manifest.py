"""The manifest is the contract between the generator and every loader.

Loaders never guess: they read this file, check free space against
total_bytes, write each item, and keep it as the receipt that makes `wipe`
possible after the generator is long gone.
"""
import csv
import hashlib
import json
import os
from datetime import datetime, timezone

SCHEMA_VERSION = 1


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                return h.hexdigest()
            h.update(b)


def write_manifest(pack_dir, job_id, persona_key, target_bytes, items, notes=None):
    total = sum(i["bytes"] for i in items)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "persona": persona_key,
        "target_bytes": target_bytes,
        "total_bytes": total,
        "delta_bytes": total - target_bytes,
        "file_count": len(items),
        "photo_count": sum(1 for i in items if i["kind"] == "image"),
        "video_count": sum(1 for i in items if i["kind"] == "video"),
        "filename_prefix": f"TDG_{job_id}_",
        "album": f"TDG {job_id}",
        "notes": notes or {},
        "items": items,
    }
    path = os.path.join(pack_dir, "manifest.json")
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=2)
    return doc


def write_licenses(pack_dir, seed_entries):
    """Every pack carries the provenance of what it was built from."""
    path = os.path.join(pack_dir, "LICENSES.csv")
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["seed_file", "source", "author", "license", "license_url", "origin_url"])
        for e in seed_entries:
            w.writerow([e.get("file"), e.get("source"), e.get("author"),
                        e.get("license"), e.get("license_url"), e.get("origin_url") or ""])
    return path
