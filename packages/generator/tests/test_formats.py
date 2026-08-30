#!/usr/bin/env python3
"""Formats and edge cases — Phase 7.

HEIC and HEVC close the v1 format gap, and the edge-case pack is the set of
files that break naive gallery code. Both are worth pinning down: the formats
because the metadata has to survive a container change, and the edge cases
because they are deliberately hostile and it would be easy to generate
something that is merely odd rather than actually pathological.

Run it:

    python3 packages/generator/tests/test_formats.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GENERATOR = os.path.dirname(HERE)
sys.path.insert(0, GENERATOR)

from PIL import Image                      # noqa: E402
from tdg import amplify                    # noqa: E402
from tdg.cli import main as cli            # noqa: E402

FAILURES = []


def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILURES.append(label)


def build(out, extra=(), size="24MB", job="fmtjob"):
    cli(["build", "--size", size, "--out", out, "--job", job, "--seed", "3",
         "--since", "2023-01-01", "--until", "2024-01-01", *extra, "--quiet"])
    with open(os.path.join(out, "manifest.json")) as fh:
        return json.load(fh)


def main():
    tmp = tempfile.mkdtemp(prefix="tdg-fmt-")

    # ---- HEIC -------------------------------------------------------------
    print("HEIC")
    backend = amplify.heic_backend()
    if backend == "none":
        print("  skip  (no HEIC encoder: install pillow-heif, or run on macOS)")
    else:
        print(f"  using {backend}")
        pack = os.path.join(tmp, "heic")
        doc = build(pack, ["--photo-format", "heic", "--photo-fraction", "1.0"])
        check(doc["total_bytes"] == doc["target_bytes"],
              "a HEIC pack still lands on its exact target")

        heics = [i for i in doc["items"] if i["name"].endswith(".heic")]
        check(len(heics) >= 1, f"the pack contains HEIC ({len(heics)} of "
                               f"{doc['file_count']})")
        check(all(i["format"] == "heic" for i in heics),
              "and the manifest records the format")

        blob = open(os.path.join(pack, heics[0]["name"]), "rb").read()
        check(b"ftyp" in blob[:32] and (b"heic" in blob[:32] or b"mif1" in blob[:32]),
              "the bytes really are an ISO/HEIF container")

        # The whole reason HEIC goes out through a JPEG: one EXIF writer.
        want = heics[0]["taken_at"][:19].replace("-", ":").replace("T", " ")
        m = re.search(rb"\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}", blob)
        check(m is not None and m.group().decode() == want,
              "DateTimeOriginal survives the conversion to HEIC")
        off = heics[0]["taken_at"][19:]
        check(off.encode() in blob,
              f"and so does the timezone offset ({off}) — without it the whole "
              "timezone chain would stop at the container change")
        check(b"iPhone 15 Pro" in blob, "Make/Model survive too")

        # The pad file closes the last few bytes with JPEG COM segments, which
        # is a JPEG-only trick — it must stay JPEG even in a HEIC pack.
        pads = [i for i in doc["items"] if i["name"].endswith(".jpg")]
        check(len(pads) >= 1, "the size-padding file stays JPEG in a HEIC pack")

        print("mixed")
        pack = os.path.join(tmp, "mixed")
        doc = build(pack, ["--photo-format", "mixed", "--photo-fraction", "1.0"],
                    size="40MB", job="mixjob")
        kinds = {i["format"] for i in doc["items"]}
        check("heic" in kinds and "jpeg" in kinds,
              f"a mixed pack contains both formats ({sorted(kinds)})")

    # ---- HEVC -------------------------------------------------------------
    print("HEVC")
    if shutil.which("ffmpeg") is None:
        print("  skip  (no ffmpeg)")
    else:
        pack = os.path.join(tmp, "hevc")
        doc = build(pack, ["--video-codec", "hevc", "--photo-fraction", "0.35",
                           "--min-clip", "4", "--max-clip", "5"],
                    size="60MB", job="hevcjob")
        vids = [i for i in doc["items"] if i["kind"] == "video"]
        check(len(vids) >= 1, f"the pack contains video ({len(vids)} clips)")
        check(all(i["format"] == "mp4/hevc" for i in vids),
              "and the manifest records the codec")
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_name,codec_tag_string", "-of", "csv=p=0",
             os.path.join(pack, vids[0]["name"])],
            capture_output=True, text=True).stdout
        check("hevc" in out, "ffprobe agrees the codec is HEVC")
        check("hvc1" in out,
              "tagged hvc1, not hev1 — QuickTime and iOS Photos will not play hev1")

    # ---- edge cases -------------------------------------------------------
    print("edge cases")
    pack = os.path.join(tmp, "edge")
    doc = build(pack, ["--edge-cases", "--photo-fraction", "1.0"],
                size="40MB", job="edgejob")

    # The bug this caught: the batch estimator prices the next batch off the
    # largest photo seen, and the deliberately tiny edge files poisoned it —
    # a 40 MB pack came out at 124 MB.
    check(doc["total_bytes"] == doc["target_bytes"],
          "a pack with edge cases still lands on its exact target")

    odd = [i for i in doc["items"] if i.get("note")]
    check(len(odd) == 10, f"ten edge-case files are added (got {len(odd)})")

    notes = " ".join(i["note"] for i in odd)
    for phrase, label in [
        ("burst frame", "a burst run"),
        ("exact duplicate", "an exact duplicate"),
        ("screenshot", "a screenshot"),
        ("non-ASCII", "a non-ASCII filename"),
        ("truncated", "a truncated file"),
        ("zero bytes", "a zero-byte file"),
    ]:
        check(phrase in notes, f"the pack includes {label}")

    by_name = {i["name"]: i for i in doc["items"]}
    for i in odd:
        real = os.path.getsize(os.path.join(pack, i["name"]))
        if real != i["bytes"]:
            FAILURES.append(f"{i['name']} size disagrees with the manifest")
    check(not [f for f in FAILURES if "disagrees" in f],
          "every edge-case file is the size the manifest claims")

    # The duplicate has to actually collide, or it tests nothing.
    sums = {}
    for i in doc["items"]:
        sums.setdefault(i["sha256"], []).append(i["name"])
    collisions = [v for v in sums.values() if len(v) > 1]
    check(len(collisions) == 1 and len(collisions[0]) == 2,
          "exactly one checksum collision — the duplicate, and nothing else")

    zero = [i for i in odd if "zero bytes" in i["note"]][0]
    check(zero["bytes"] == 0, "the zero-byte file is genuinely empty")

    trunc = [i for i in odd if "truncated" in i["note"]][0]
    try:
        im = Image.open(os.path.join(pack, trunc["name"]))
        im.load()
        check(False, "the truncated file fails to decode")
    except Exception:
        check(True, "the truncated file fails to decode")

    shot = [i for i in odd if "screenshot" in i["note"]][0]
    im = Image.open(os.path.join(pack, shot["name"]))
    check(im.format == "PNG", "the screenshot is a PNG")
    check(not im.getexif().get(0x010F),
          "and carries no camera Make — an app assuming EXIF on every asset "
          "meets this first")

    uni = [i for i in odd if "non-ASCII" in i["note"]][0]
    check(any(ord(c) > 127 for c in uni["name"]),
          f"the non-ASCII name really is non-ASCII ({uni['name']})")
    check(os.path.exists(os.path.join(pack, uni["name"])),
          "and the file exists on disk under that name")

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
