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

## Formats

| Flag | Values | |
|---|---|---|
| `--photo-format` | `jpeg` (default), `heic`, `mixed` | HEIC is what a real iPhone shoots. `mixed` is the realistic library: HEIC from the camera, JPEG from everything shared into it. |
| `--video-codec` | `h264` (default), `hevc` | HEVC is what a modern phone records. Tagged `hvc1`, not `hev1` — QuickTime and iOS Photos will not play the latter. |

HEIC goes out through a JPEG, so the hand-rolled EXIF writer stays the single
source of metadata for both formats. Verified: `DateTimeOriginal`, all three
`OffsetTime` tags and `Make`/`Model` survive the conversion intact.

HEIC is the one thing the generator does not hand-roll — it is HEVC inside a
HEIF container, which is a different order of job from writing an EXIF block.
It uses whichever encoder is present rather than requiring one:

1. `pillow-heif`, if installed — works anywhere
2. `sips`, on macOS — no install at all
3. otherwise it refuses with the `pip install pillow-heif` line

So a Linux build box or the Docker image needs `pip install pillow-heif` for
HEIC. Everything else stays dependency-free.

### What a build actually costs

Measured on a 10-core Apple Silicon laptop, and the ratio is what matters:

| phase | rate |
|---|---|
| photos (parallel, all cores) | ~33 MB/s |
| video, 4K, serial | ~1.8 MB/s |
| video, 4K, `--video-jobs 4` (default) | ~4.6 MB/s |

Video is the expensive half, so build time is governed by `--photo-fraction`
far more than by `--video-codec` — HEVC costs about 14% over H.264, while
halving the video share nearly halves the build.

**Video encoding is not encoder-bound.** A 4K clip spends ~29 of its ~30
seconds inside the single-threaded lavfi source generating frames, and the
whole job uses about 1.4 of 10 cores. That is why clips are encoded several at
a time: on a 600 MB video-heavy pack, `--video-jobs 1` took 313 s and the
default 4 took 124 s, a **2.5x** speedup.

More is not better. Measured throughput for 4K clips:

| concurrent clips | throughput |
|---|---|
| 1 | 1.72 MB/s |
| **4** | **5.21 MB/s** |
| 8 | 1.44 MB/s — *slower than serial* |

Past the four performance cores the work lands on efficiency cores and 4K
frame buffers start to hurt, so `--video-jobs` is capped at 4.

Clips are planned as a batch and priced off one rate estimate rather than
re-learned per clip, so **`--video-jobs` changes which durations get chosen**.
It is part of the build fingerprint: the same `--job`/`--seed` reproduces a pack
only at the same `--video-jobs`, and a part-built pack will not resume with a
different value.

## Edge cases (`--edge-cases`)

The files that break naive gallery code. Ten of them, generated before the bulk
of the pack so their bytes sit inside the size budget:

| | Why |
|---|---|
| a 5-frame burst | same scene 380 ms apart — timeline and "best of" grouping |
| an exact duplicate | everything else is re-encoded so nothing collides; this one exists for a dedupe implementation to find |
| a screenshot | PNG, flat colour, and **no camera EXIF at all** |
| a non-ASCII filename | `café_日本語.jpg` — every layer that shells out or builds a URL gets a vote |
| a truncated JPEG | valid header, body cut short |
| a zero-byte file | what a failed copy leaves behind |

Two of these found real bugs on their first run. The non-ASCII name was
rejected by the control plane's ASCII-only file route, which now allowlists
against the manifest instead — stricter *and* more permissive. And the
screenshot and zero-byte file have no EXIF, so Android leaves `DATE_TAKEN`
null; `tdg verify` used to count them as missing rather than as undated.

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

## Verifying a fill (`tdg verify`)

A load reporting success only means the bytes were handed over. `tdg verify`
asks the **device** what its gallery did with them:

```bash
tdg verify --pack ./pack --target emulator --out docs/conformance/pixel.md
```

Four checks — assets indexed, capture time preserved, album grouping, fill
duration — printed, optionally written as markdown and JSON, and reflected in
the exit code so a conformance run can gate CI.

It is worth its keep: the first real run found that this CLI filed packs under
`DCIM/TDG_<job>` while the Android app used `DCIM/TDG <job>`, so the same pack
landed in two differently-named albums depending on which loader you used. The
CLI now follows the manifest's album and quotes its remote paths.

Verification is per-platform, and one gap is structural: a **physical iPhone**
has no route in, because the Photos database is unreachable from outside the
app. See [docs/conformance](../../docs/conformance/).

## Tests

```bash
python3 packages/generator/tests/test_loader.py
python3 packages/generator/tests/test_exif.py
python3 packages/generator/tests/test_resume.py
python3 packages/generator/tests/test_formats.py
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
