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
| `--job` + `--seed` | Same values produce the same pack, byte for byte. A failing test stays re-runnable. |
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

## Known limits

- **JPEG and MP4/H.264 only.** HEIC and HEVC are Phase 7. Real iPhones shoot
  HEIC, so a pack from this generator does not exercise that path.
- **Synthetic bootstrap seeds are obviously synthetic.** They are textured
  enough to compress like photographs (a 12 MP still lands at ~3.4 MB at q88,
  which is the point), but they are not photographs. Run `harvest` for real
  media before any test that involves looking at the results.
- `harvest` needs network access to the media hosts. It is deliberately paced
  and cached: stock APIs prohibit bulk downloading, which is the entire reason
  for the seed-pool-plus-amplifier design.
- Throughput on a 4-core VM: ~19 MB/s photo-heavy, ~8 MB/s with 4K video in
  the mix (x264 dominates). A 64 GB pack is therefore roughly 1-2.5 hours
  there. It is CPU-bound on JPEG encoding and x264, and scales nearly linearly
  with cores — `--jobs` and a faster `--preset` are the levers.
- Long builds must survive being interrupted. They currently do not resume;
  `--job` + `--seed` reproduce a pack from scratch, which is not the same
  thing. Resumability is the next thing to add.
