# TestDataGenerator (TDG)

Fills mobile devices with N GB of open-license photos and videos, in the native
camera roll, with realistic EXIF — for testing gallery-scanning apps.

See [PLAN.md](./PLAN.md) for the full build plan.

## Quick start

```bash
# a seed pool — every still is a crop of one of these, so run this first.
# Real media, needs a network; `bootstrap-seeds` is the offline fallback.
python3 -m tdg.cli harvest --images 200       # from packages/generator

# either drive it from the browser...
python3 packages/web/serve.py                 # then open http://localhost:8722

# ...or from the command line
python3 -m tdg.cli build --size 25GB --out ./pack
python3 -m tdg.cli load --pack ./pack --target simulator
python3 -m tdg.cli wipe --job <id>
```

## Layout

- `packages/generator` — seed harvester, amplifier, size planner, manifest builder,
  and the `tdg` CLI (`build` / `load` / `wipe` / `devices` / `receipts`)
- `packages/web` — browser control plane: presets, live progress, LAN pack serving
- `clients/android` — Kotlin loader (MediaStore), builds with Gradle
- `clients/ios` — SwiftUI loader (PHAssetCreationRequest), builds with xcodebuild
- `seed-pool` — cached CC0/CC-BY source media, with the licence and origin of
  each file in `seeds.json`. **Gitignored**, so a fresh clone has none: run
  `tdg harvest` (or `tdg bootstrap-seeds` offline) before building a pack.
  Pool size decides how visually distinct a pack's stills are — see
  [the generator README](./packages/generator/README.md#the-seed-pool)
- `docs/conformance` — what `tdg verify` found on each device

## Status

| Phase | | |
|---|---|---|
| 1 | Exact-size generator | done |
| 2 | Desktop & CI loaders | done — verified on a real iOS simulator and Android emulator |
| 3 | Web control plane | done |
| 4 | Android loader app | done — verified on a **physical Galaxy S24** (Android 16) and an emulator |
| 5 | iOS loader app | done — verified on a **physical iPhone 15 Pro** (iOS 26.6.1) and the simulator |
| 6 | Conformance harness | harness done; 4 of 6 devices verified, including a physical iPhone and Galaxy S24 |
| 7 | HEIC/HEVC and realism | formats done and verified; Live Photos and the 4 GB file outstanding |

## The mark

Two sheets of media, stacked, with a photo glyph on the front — the product
makes files, and specifically photos and video.

There is no SVG rasteriser on the build machines, so the geometry lives once in
[`assets/make_logo.py`](assets/make_logo.py) and is emitted twice: drawn with
Pillow for the PNGs the app stores need, written out as SVG for the web. The
favicon and the app icons therefore cannot drift apart.

```bash
python3 assets/make_logo.py     # regenerates every size, all committed
```

Its output lands in the iOS asset catalog, the Android mipmaps (adaptive icon
included) and the web's static directory. Nobody needs to run it to build.

## Tests

No third-party runner; each suite is a plain script.

```bash
python3 packages/generator/tests/test_exif.py      # capture times and EXIF
python3 packages/generator/tests/test_loader.py    # device loaders, via fakes
python3 packages/generator/tests/test_resume.py    # interrupted builds
python3 packages/generator/tests/test_formats.py   # HEIC, HEVC, edge cases
python3 packages/web/tests/test_server.py          # the control plane
```
