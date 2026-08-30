# Conformance — Galaxy S24 (physical) — Android 16, 10 GB on battery, screen off

- job `209dc1` · 3466 files (3445 photos, 21 videos) · 10.0 GB
- verified 2026-08-30T19:33:46+00:00
- assets matched by name

| Check | Result | |
|---|---|---|
| assets indexed | 3466 / 3466 | yes |
| capture time preserved | 3464 / 3464 dated, worst delta 0.999s | yes |
| assets with no capture time | 2 — no EXIF date to carry | — |
| album grouping | DCIM/TDG 209dc1 | yes |
| fill duration | **14.9 min**, 11.5 MB/s over Wi-Fi | — |

**PASSED**

## The point of this run

The 500 MB run finished in 42 seconds, which said nothing about the risk §4
actually cares about: **Android's own limits and Samsung's battery manager
suspending a long fill**. This one ran for 15 minutes, on battery, with the
screen off.

| condition | |
|---|---|
| power | **unplugged**, discharging (USB removed; adb over Wi-Fi) |
| screen | **off** for all but the first 20 seconds (`mWakefulness=Dozing`) |
| duration | 14.9 minutes |
| files | 3,466 — so 3,466 separate MediaStore inserts, not one big copy |
| battery cost | 53% → 50%, about **3% for a 10 GB fill** |

**The foreground service was never suspended.** `isForeground=true` held at every
45-second sample across the whole run, transfer rate stayed flat at ~11.5 MB/s,
and the service stopped only when it finished. No kill, no ANR, nothing in
logcat. The ongoing notification carried `ONGOING_EVENT|NO_CLEAR|FOREGROUND_SERVICE`
throughout.

That is the plan's Phase 4 design — foreground service plus an ongoing
notification — doing exactly the job it was chosen for, on the OEM most likely
to interfere.

## Caveats worth keeping

- **One run, one device, ~15 minutes.** A 64 GB fill is roughly 90 minutes at
  this rate, four times longer, and Samsung's adaptive battery learns app
  behaviour over days. A single clean run is evidence, not proof.
- **The app had been used in the foreground minutes earlier.** Samsung's
  "sleeping apps" behaviour is harshest on apps the user never opens; an app
  that has just been interacted with is treated more kindly. A fill started and
  then left overnight is the harder case and is still untested.
- **`tdg verify` reports the duration as unknown** because the *app* performed
  the load, so there is no CLI receipt. The 14.9 minutes came from the control
  plane's request log.

## Correction: what actually made the first build slow

The first attempt at this pack was abandoned part-way and rebuilt, and the
reason given at the time — "HEVC is punishingly slow" — was **wrong**. Two
variables changed between the two builds, and the codec got the credit that
belonged to the other one.

A controlled measurement, identical settings with only the codec varying
(300 MB, 5 clips of 10 s at 4K):

| codec | video encode rate |
|---|---|
| H.264 | 1.90 MB/s |
| HEVC | 1.66 MB/s |

HEVC is about **14% slower**, not six times. What actually halved the build was
raising `--photo-fraction` from 0.6 to 0.8, which cut the video budget from 4 GB
to 2 GB. Video encodes at roughly **1.9 MB/s** and photos at roughly
**33 MB/s** — a seventeen-fold difference — so build time is dominated almost
entirely by how many bytes of video a pack contains, and barely at all by which
codec encodes them.
