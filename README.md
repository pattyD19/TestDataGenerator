# TestDataGenerator (TDG)

Fills mobile devices with N GB of open-license photos and videos, in the native
camera roll, with realistic EXIF — for testing gallery-scanning apps.

See [PLAN.md](./PLAN.md) for the full build plan.

## Quick start

```bash
# a seed pool (synthetic, no network needed)
python3 -m tdg.cli bootstrap-seeds            # from packages/generator

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
- `seed-pool` — cached CC0/CC-BY source media + LICENSES.csv (gitignored, rebuilt by harvester)
- `docs/conformance` — what `tdg verify` found on each device

## Status

| Phase | | |
|---|---|---|
| 1 | Exact-size generator | done |
| 2 | Desktop & CI loaders | done — verified on a real iOS simulator and Android emulator |
| 3 | Web control plane | done |
| 4 | Android loader app | done — verified on an Android 17 emulator, not yet a physical handset |
| 5 | iOS loader app | done — verified on an iOS 26.5 simulator; no signing identity here, so untested on a physical iPhone |
| 6 | Conformance harness | harness done; 2 of 6 devices verified, both emulated |
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
