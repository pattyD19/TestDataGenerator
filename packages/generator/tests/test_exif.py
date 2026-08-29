#!/usr/bin/env python3
"""Tests for capture-time correctness.

These exist because of a real bug. Capture times used to be naive datetimes,
and four consumers each guessed a different zone for them: mtime read them as
build-host local, ffmpeg labelled them UTC, the GPS tags got wall-clock (GPS
time is defined as UTC), and iOS Photos applied the importing device's zone.
A pack therefore landed at a different absolute instant on every machine, and
photos and videos inside one pack disagreed by the host's offset.

Measured on a real iPhone 17 Pro simulator before the fix: photos imported
4 hours late, exactly the host's UTC offset. Videos did not move.

Run it:

    python3 packages/generator/tests/test_exif.py
"""
import datetime
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GENERATOR = os.path.dirname(HERE)
sys.path.insert(0, GENERATOR)

from PIL import Image                                    # noqa: E402
from tdg import personas                                 # noqa: E402
from tdg.cli import main as cli                          # noqa: E402
from tdg.exifwrite import ffmpeg_time, offset_string     # noqa: E402

FAILURES = []

DATETIME_ORIGINAL, OFFSET_TIME_ORIGINAL = 0x9003, 0x9011
OFFSET_TIME, OFFSET_TIME_DIGITIZED = 0x9010, 0x9012
GPS_TIMESTAMP, GPS_DATESTAMP = 7, 29


def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILURES.append(label)


def have_tzdata():
    """The IANA database is absent from some slim images; DST tests need it."""
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo("Asia/Seoul")
        return True
    except Exception:
        return False


def main():
    print("offset formatting")
    tz = datetime.timezone
    td = datetime.timedelta
    at = lambda off: datetime.datetime(2024, 6, 1, 12, tzinfo=tz(td(hours=off)))
    check(offset_string(at(9)) == "+09:00", "positive whole-hour offset")
    check(offset_string(at(-4)) == "-04:00", "negative offset")
    check(offset_string(datetime.datetime(2024, 6, 1, 12,
                                          tzinfo=tz(td(hours=5, minutes=30)))) == "+05:30",
          "half-hour offset (Kolkata is +05:30, not +05:00)")
    check(offset_string(datetime.datetime(2024, 6, 1, 12)) == "",
          "a naive datetime yields no offset rather than a wrong one")

    print("city timezones")
    check(all(len(c) == 5 for c in personas.CITIES),
          "every city carries a zone and a fallback offset")
    if have_tzdata():
        dublin = next(c for c in personas.CITIES if c[0].startswith("Dublin"))
        z = personas.city_tz(dublin[3], dublin[4])
        summer = datetime.datetime(2024, 8, 15, 12, tzinfo=z)
        winter = datetime.datetime(2024, 1, 15, 12, tzinfo=z)
        check(offset_string(summer) == "+01:00" and offset_string(winter) == "+00:00",
              "DST is modelled: Dublin is +01:00 in August, +00:00 in January")
    else:
        print("  skip  DST check (no IANA tz database on this machine)")
    fallback = personas.city_tz("Not/AZone", 9 * 3600)
    check(offset_string(datetime.datetime(2024, 6, 1, tzinfo=fallback)) == "+09:00",
          "an unknown zone falls back to a fixed offset instead of failing the build")

    print("video timestamps")
    aware = datetime.datetime(2024, 6, 1, 12, 0, 0, tzinfo=tz(td(hours=9)))
    check(ffmpeg_time(aware).startswith("2024-06-01T03:00:00"),
          "ffmpeg creation_time is the UTC instant, not the wall clock")

    # ---- a real pack ------------------------------------------------------
    print("a generated pack")
    tmp = tempfile.mkdtemp(prefix="tdg-exif-")
    pack = os.path.join(tmp, "pack")
    # Photo-only so the test needs no ffmpeg.
    cli(["build", "--size", "9MB", "--photo-fraction", "1.0", "--out", pack,
         "--job", "tzjob", "--quiet"])
    doc = json.load(open(os.path.join(pack, "manifest.json")))

    check(doc["schema_version"] >= 2, "manifest schema is v2 or later")
    check(all("+" in i["taken_at"] or "-" in i["taken_at"][10:] for i in doc["items"]),
          "every manifest taken_at carries a UTC offset")
    check(all("taken_at_utc" in i for i in doc["items"]),
          "every item states its absolute instant too")

    agree = all(
        datetime.datetime.fromisoformat(i["taken_at"])
        == datetime.datetime.fromisoformat(i["taken_at_utc"])
        for i in doc["items"])
    check(agree, "taken_at and taken_at_utc describe the same instant")

    photos = [i for i in doc["items"] if i["kind"] == "image"]
    offsets, gps_utc_ok, mtime_ok = set(), 0, 0
    gps_count = 0
    for it in photos:
        path = os.path.join(pack, it["name"])
        exif = Image.open(path).getexif()
        sub = exif.get_ifd(0x8769)
        gps = exif.get_ifd(0x8825)
        want = datetime.datetime.fromisoformat(it["taken_at"])

        offsets.add(sub.get(OFFSET_TIME_ORIGINAL))
        if sub.get(OFFSET_TIME_ORIGINAL) != offset_string(want):
            FAILURES.append(f"{it['name']} offset tag disagrees with the manifest")

        # DateTimeOriginal stays wall-clock; the offset tag is what anchors it.
        if sub.get(DATETIME_ORIGINAL) != want.strftime("%Y:%m:%d %H:%M:%S"):
            FAILURES.append(f"{it['name']} DateTimeOriginal is not the local wall clock")

        if gps and gps.get(GPS_TIMESTAMP):
            gps_count += 1
            utc = want.astimezone(datetime.timezone.utc)
            h, m, s = (int(x) for x in gps[GPS_TIMESTAMP])
            if (h, m, s) == (utc.hour, utc.minute, utc.second) and \
                    gps[GPS_DATESTAMP] == utc.strftime("%Y:%m:%d"):
                gps_utc_ok += 1

        disk = datetime.datetime.fromtimestamp(os.path.getmtime(path),
                                               datetime.timezone.utc)
        if abs((disk - want).total_seconds()) < 1.5:
            mtime_ok += 1

    check(not [f for f in FAILURES if "offset tag disagrees" in f],
          "every photo's OffsetTimeOriginal matches its manifest entry")
    check(not [f for f in FAILURES if "DateTimeOriginal is not" in f],
          "DateTimeOriginal stays local wall-clock")
    check(len(offsets) >= 1 and None not in offsets,
          f"all photos carry an offset tag (saw {sorted(o for o in offsets if o)})")
    check(gps_count and gps_utc_ok == gps_count,
          f"GPS timestamps are UTC, not wall clock ({gps_utc_ok}/{gps_count})")
    check(mtime_ok == len(photos),
          f"file mtimes equal the absolute capture instant ({mtime_ok}/{len(photos)})")

    # The bug in one assertion: the same job built on two machines in different
    # timezones must describe the same absolute instants. This is what actually
    # regressed, so it is built twice for real rather than re-read.
    print("host timezone independence")
    import time as _time

    def build_under_tz(tz_name, out):
        saved = os.environ.get("TZ")
        os.environ["TZ"] = tz_name
        _time.tzset()
        try:
            # --since/--until must be explicit: the default `until` is
            # datetime.now(), which would itself shift with the host zone and
            # confound the comparison.
            cli(["build", "--size", "9MB", "--photo-fraction", "1.0",
                 "--out", out, "--job", "tzjob", "--seed", "7",
                 "--since", "2023-01-01", "--until", "2024-01-01", "--quiet"])
            man = json.load(open(os.path.join(out, "manifest.json")))
        finally:
            if saved is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = saved
            _time.tzset()
        return man

    a = build_under_tz("Pacific/Kiritimati", os.path.join(tmp, "a"))   # UTC+14
    b = build_under_tz("Pacific/Niue", os.path.join(tmp, "b"))         # UTC-11

    check([i["taken_at_utc"] for i in a["items"]]
          == [i["taken_at_utc"] for i in b["items"]],
          "same job built 25 hours of timezone apart yields identical instants")
    check([i["sha256"] for i in a["items"]] == [i["sha256"] for i in b["items"]],
          "and byte-identical files, so reproducibility survives the fix")

    mtimes_a = [os.path.getmtime(os.path.join(tmp, "a", i["name"])) for i in a["items"]]
    mtimes_b = [os.path.getmtime(os.path.join(tmp, "b", i["name"])) for i in b["items"]]
    check(mtimes_a == mtimes_b, "and identical mtimes")

    import shutil
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
