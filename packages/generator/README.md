# tdg — test data generator

Builds a pack of photos and video of an exact size, with realistic EXIF and
capture dates, ready for a loader to push into a device's camera roll.

Phase 1 of [the plan](../../PLAN.md). Python, because Pillow, numpy and ffmpeg
are already present everywhere we build; no third-party packages are required
(the EXIF writer is hand-rolled against the TIFF spec rather than pulling
piexif, and `harvest` is the only module that needs `requests`).

## Quick start

```bash
cd packages/generator

# 1. a seed pool. No network needed — synthesises textured stills locally.
python3 -m tdg.cli bootstrap-seeds --count 48

#    ...or, on a machine with internet, fetch real open-license media:
python3 -m tdg.cli harvest --images 200

# 2. build a pack
python3 -m tdg.cli build --size 25GB --profile iphone-15-pro --out ./pack

# 3. check it
python3 -m tdg.cli inspect ./pack

# 4. put it somewhere a gallery will index it
python3 -m tdg.cli devices                       # what's reachable
python3 -m tdg.cli load --pack ./pack --target simulator
python3 -m tdg.cli wipe --job <id>               # and take it back out
```

## What a pack contains

```
pack/
  TDG_<job>_00000.mp4     every file carries the job id, so wipe is exact
  TDG_<job>_00001.jpg
  ...
  manifest.json           the contract every loader reads
  LICENSES.csv            provenance of every seed the pack was built from
```

`manifest.json` carries per-item bytes, sha256, capture time and GPS, plus the
totals a loader checks against free space *before* writing anything.

## Options that matter

| Flag | Why |
|---|---|
| `--size` | `25GB`, `512MB`, raw bytes. The pack lands on this **exactly**. |
| `--profile` | `iphone-15-pro`, `pixel-8`, `galaxy-s24`, `galaxy-a54`. Sets camera make/model, resolutions, JPEG quality band and video mode. |
| `--photo-fraction` | Share of *bytes* that are stills (default 0.70). File **count** follows from this — 26,000 small files stress a backup client very differently from 200 large ones. |
| `--since` / `--until` | Capture-date range. Dates are clustered into bursts with a background sprinkle, because a uniform sprinkle makes every date-grouping feature look identical. |
| `--job` + `--seed` | Same values produce the same pack, byte for byte — video included. A failing test stays re-runnable. |
| `--jobs` | Parallel photo encoders; defaults to CPU count. |
| `--preset` | x264 preset. `ultrafast` keeps large packs practical; `medium` if you care about how the video actually looks. |

## How it hits an exact size

JPEG size is content-dependent, so nothing is estimated twice. Videos are
priced at their measured bytes-per-second (the nominal bitrate undershoots by
~5% once container overhead is counted, so the rate is re-learned from the
first clip). Photo batches are sized off the **largest** photo seen rather than
the mean, so a batch never overshoots its ceiling. Whatever is left is closed
by a trim clip and finally a JPEG padded with COM comment segments — valid
JPEG, ignored by decoders, exact to the byte.

Verified: a 120 MB pack lands at 120 MB, delta 0 bytes.

## Loading a pack (`tdg load`)

Three targets, none of which needs a mobile app:

| Target | Mechanism | Notes |
|---|---|---|
| `folder` | plain copy, mtimes preserved | CI, shared drives, desktop clients |
| `simulator` | `xcrun simctl addmedia` | Real Photos import, so `DateTimeOriginal` becomes the asset's creationDate. Needs full Xcode — the Command Line Tools do not ship `simctl`. |
| `emulator` | `adb push` + explicit MediaStore scan | A pushed file that is never scanned is invisible to the gallery. Also works on a plugged-in handset with USB debugging. |

Two things make it usable on real 64 GB runs rather than just demos:

- **Free space is checked before a single byte is written.** Filling a device
  until it wedges is a slow, unhelpful failure.
- **Every load writes a receipt** to `~/.tdg/receipts` — deliberately *outside*
  the pack. Re-running the same command resumes rather than restarting, and
  `tdg wipe --job <id>` still works after the pack has been deleted. Nothing
  outside the receipt is ever touched.

### Getting the files back off

Deletion is keyed on the receipt, never on filenames — iOS renames every
imported asset to `IMG_NNNN`, so a filename-keyed wipe would find nothing there.
Each receipt line stores the handle that deletes that asset: a host path for
`folder`, a device path for `emulator`, and (for the Phase 5 iOS app) the
`PHAsset` `localIdentifier`.

| Target | Delete individual assets? |
|---|---|
| `folder` | yes |
| `emulator` | yes — `rm` plus a re-scan, so MediaStore drops the rows too |
| `simulator` | **no.** Confirmed against Xcode 26.6: simctl can `addmedia` but has no delete-media verb. `tdg wipe --erase-device` resets the whole simulator, which is fine for a disposable device; otherwise delete by hand in Photos. |

`--erase-device` is verified: a 39-asset load was erased back to the runtime's
stock 6 photos and the device booted normally afterwards.

### Verified against real devices

Not just the fakes. Both device targets have been run end to end:

| | iPhone 17 Pro simulator (Xcode 26.6, iOS 26.5) | Pixel 9 emulator (Android 17, API 37, arm64) |
|---|---|---|
| import | 14 assets at 66 MB/s via `addmedia` | 39 assets at 4.2 MB/s via `adb push` |
| indexed | 14/14 in the Photos database | 39/39 in MediaStore (37 images, 2 videos) |
| capture time | exact instant, photos and video alike | `DATE_TAKEN` matched the manifest to under a second |
| checksums | n/a (Photos re-encodes the container) | 39/39 verified by `sha256sum` on device |
| wipe | erase only — no per-asset delete exists | files, directory and MediaStore rows all gone |

The Android numbers are the interesting ones: a pushed file that is never
scanned is invisible, and a deleted file that is never re-scanned leaves a dead
row behind. Both were checked by querying MediaStore directly rather than by
trusting the exit code.

## Interrupted builds resume

A 64 GB pack is one to two hours of CPU. Re-running the same `build` command
picks up where it left off; nothing extra to pass.

```bash
python3 -m tdg.cli build --size 64GB --out ./pack   # ^C, closed laptop, OOM
python3 -m tdg.cli build --size 64GB --out ./pack   # carries on
python3 -m tdg.cli build --size 64GB --out ./pack --restart   # or start over
```

A resumed pack is **byte-for-byte the pack an uninterrupted run would have
made**. That takes more than remembering which files exist: the planner learns
the encoder's real bytes-per-second as it goes and draws from a seeded RNG
whose position determines every later file, so the checkpoint carries the
learned state and the RNG position too.

Two files in the output directory hold it — `.tdg-build.jsonl` (one completed
item per line) and `.tdg-build.json` (the header). Items are flushed before the
header that counts them is rewritten, so a kill at any instant leaves a header
naming *fewer* items than the log holds, never more. Both are deleted when the
manifest is written, so a finished pack never looks partial.

Refusals are deliberate. A checkpoint whose settings do not match the current
command is not resumed — that would produce a pack matching neither run — and
neither is one whose recorded files have been deleted or truncated. Both say so
and suggest `--restart` rather than guessing.

## Capture times carry a timezone

EXIF `DateTimeOriginal` is a wall clock with no zone attached. An importer left
to guess uses its own, so the same pack lands at a different absolute instant on
every machine — and because MP4 stores an absolute instant rather than a local
one, the videos in a pack would not move with the photos.

So every capture time here is anchored to the timezone of the city its GPS
points at, and that anchor is written down in four places that must agree:

| | |
|---|---|
| `DateTimeOriginal` | local wall clock, as a camera writes it |
| `OffsetTimeOriginal` (+ `OffsetTime`, `OffsetTimeDigitized`) | the UTC offset, so nothing has to guess |
| `GPSTimeStamp` / `GPSDateStamp` | UTC, which is what the GPS spec requires |
| file mtime and MP4 `creation_time` | the absolute instant |

Real zones via `zoneinfo`, so DST is modelled — Dublin is `+01:00` in August and
`+00:00` in January. Cities keep a fixed fallback offset for images that ship
without the IANA database. Every item gets a zone even when it gets no GPS,
because a camera always knows the local time.

The manifest records both `taken_at` (local, with offset) and `taken_at_utc`.
Verified on an iPhone 17 Pro simulator: all 14 assets landed on the exact
instant the manifest specified, photos and video alike.

## Tests

```bash
python3 packages/generator/tests/test_loader.py
python3 packages/generator/tests/test_exif.py
python3 packages/generator/tests/test_resume.py
```

No third-party runner. The device targets are covered by fake `adb` and `xcrun`
binaries (`tests/fakeadb.py`, `tests/fakexcrun.py`) backed by a directory, so
push/scan/verify/wipe, device resolution, resume and the per-file push fallback
for older platform-tools all run in CI with no device and no Android SDK. The
fakes reject any verb the loader does not currently use, so a new call site
fails the tests rather than passing silently.

`test_exif.py` builds the same job under `Pacific/Kiritimati` (UTC+14) and
`Pacific/Niue` (UTC-11) and requires identical instants, checksums and mtimes —
25 hours apart is enough that the old naive-datetime handling could not have
passed it.

## Known limits

- **JPEG and MP4/H.264 only.** HEIC and HEVC are Phase 7. Real iPhones shoot
  HEIC, so a pack from this generator does not exercise that path.
- **Manifest schema v2 is not v1.** In v1 `taken_at` was a naive local time
  with no offset, so packs built before the timezone fix cannot be compared
  instant-for-instant against ones built after. Rebuild rather than mix. The
  same `--job`/`--seed` will not reproduce a pre-v2 pack byte for byte either,
  since the EXIF now carries offset tags.
- **Synthetic bootstrap seeds are obviously synthetic.** They are textured
  enough to compress like photographs (a 12 MP still lands at ~3.4 MB at q88,
  which is the point), but they are not photographs. Run `harvest` for real
  media before any test that involves looking at the results.
- `harvest` needs network access to the media hosts. It is deliberately paced
  and cached: stock APIs prohibit bulk downloading, which is the entire reason
  for the seed-pool-plus-amplifier design.
- Throughput is CPU-bound on JPEG encoding and x264, and scales nearly
  linearly with cores — `--jobs` and a faster `--preset` are the levers.
  Measured at the default 70/30 photo/video mix:

  | Machine | Rate | 64 GB pack |
  |---|---|---|
  | Apple Silicon laptop (ffmpeg 9, x264) | ~12 MB/s | ~1.5 h |
  | 4-core VM | ~8 MB/s | ~2.5 h |

  Photo-only packs (`--photo-fraction 1.0`) run roughly twice as fast; x264
  dominates whenever video is in the mix.
- Resume granularity is the batch, not the file. An interrupted run loses at
  most the photos that were in flight (`--jobs` × 4) or the single clip being
  encoded — seconds to a couple of minutes of work, not hours.
- Real-device throughput is bounded by the transfer, not the encode: `adb push`
  to an emulator ran at 4.2 MB/s, so a 64 GB fill over adb is hours. Wi-Fi to a
  handset on the LAN is the faster path, and the reason the generator is meant
  to run on the LAN rather than in the cloud.
- Verified for real on both device targets (see below). The remaining unproven
  path is deletion on a **physical iPhone**, which needs the Phase 5 loader app
  that does not exist yet.
