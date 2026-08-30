# Conformance — Galaxy S24 (SM-S921U1, physical) — Android 16, One UI

- job `053a7b` · 144 files (139 photos, 5 videos) · 500.0 MB
- verified 2026-08-30T18:14:43+00:00
- assets matched by name

| Check | Result | |
|---|---|---|
| assets indexed | 144 / 144 | yes |
| capture time preserved | 142 / 142 dated, worst delta 0.997s | yes |
| assets with no capture time | 2 — no EXIF date to carry | — |
| album grouping | DCIM/TDG 053a7b | yes |
| fill duration | 42s for 500 MB over Wi-Fi (**11.9 MB/s**) | — |

**PASSED**

## Why this device mattered

§4 of the plan names three things a Pixel emulator cannot tell you. All three
were exercised here.

**Samsung Gallery does honour `RELATIVE_PATH`.** The album `TDG 053a7b` appears
with all 144 assets — but under **View all**, not in the curated "Essential
albums" row, so a tester looking at the default Albums screen will not see it.
Worth knowing before someone reports the album "missing".

**Assets with no EXIF date land under "Today".** The screenshot and the
zero-byte file have no `DATE_TAKEN`, so One UI's timeline files them at the top
under today's date rather than at their intended 2023–2024 position. The
zero-byte file also becomes the **album cover**, as a broken-image placeholder,
because it sorts newest. Both are correct behaviour and both are exactly what an
app that assumes every asset has a capture time will trip over.

**Android accepts what iOS refuses.** The same pack on an iPhone 15 Pro had the
zero-byte file rejected by `PHAssetCreationRequest`; MediaStore indexes it
happily, size 0. Any app under test that scans both platforms sees a different
library from the same pack — which is the sort of divergence this tool exists to
make visible.

**Throughput.** 500 MB in 42 s over Wi-Fi, about **11.9 MB/s** — roughly five
times what the same pack managed to an iPhone on the same network, and enough
to put a 64 GB fill at about 1.5 hours. That is transfer-bound, not
encode-bound.

## Not covered

- The fill took 42 seconds, so this says **nothing about Samsung's battery
  manager suspending a long job**. That risk needs a multi-GB fill, ideally with
  the screen off, and is still untested.
- `tdg verify` reports the fill duration as unknown here because the *app*
  performed the load, so there is no CLI receipt to read the elapsed time from.
  The number above came from the control plane's own request log.
