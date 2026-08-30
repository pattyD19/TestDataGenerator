# Conformance results

What `tdg verify` found when it asked each device what its gallery actually
did with a fill. Four questions, from §4 of [the plan](../../PLAN.md): how many
assets were indexed, whether the capture time survived, whether album grouping
held, and how long the fill took.

```bash
tdg verify --pack ./pack --target emulator \
           --label "Pixel 9 emulator" \
           --out docs/conformance/pixel-9.md
```

It exits non-zero when a check fails, so a conformance run can gate CI.

## The matrix

The plan calls for six devices. Four are covered — two emulated, plus a **physical iPhone and a physical Galaxy S24**:

| Device | Role | Status |
|---|---|---|
| [Pixel 9 emulator, Android 17](pixel-9-emulator-android-17.md) | AOSP reference | **passed** |
| [iPhone 17 Pro simulator, iOS 26.5](iphone-17-pro-simulator-ios-26.5.md) | iOS reference | **passed** |
| [Pixel 9, edge-case pack](pixel-9-emulator-edge-cases.md) | duplicates, zero-byte, truncated, non-ASCII names, a screenshot | **passed** |
| [iPhone 17 Pro, HEIC + HEVC pack](iphone-17-pro-simulator-heic-hevc.md) | the formats a real iPhone actually produces | **passed** |
| [**iPhone 15 Pro (physical), iOS 26.6.1**](iphone-15-pro-physical-ios-26.6.1.md) | **real hardware** — 71/72 accepted, one correct refusal | **passed** |
| [**Galaxy S24 (physical), Android 16**](galaxy-s24-physical-android-16.md) | **real hardware** — One UI, Samsung Gallery, 144/144 indexed | **passed** |
| Current Pixel (physical) | AOSP reference on real hardware | not run — no device |
| ~~Current Galaxy S~~ | One UI, its own Gallery app | **done** — see above |
| Galaxy A-series | budget tier: slow storage, tight battery management | not run — no device |
| Pixel or Galaxy on Android 11 | the OS floor | not run — no device |
| ~~Current iPhone (physical)~~ | Photos, HEIC-native | **done** — see above |
| iPhone on iOS 17 | the iOS floor | not run — no device |

**An emulator pass is necessary, not sufficient**, and the two physical runs
proved it: the iPhone found two loader bugs no simulator could, and the S24
showed that One UI files undated assets under "Today" and hides the album behind
"View all". What is still untested is **Samsung's battery manager suspending a
long fill** — both physical runs were under a minute — and the OS floors, since
`minSdk 30` and `IPHONEOS_DEPLOYMENT_TARGET 17.0` are declared rather than
exercised.

## What the two passes tell us

Both platforms agree on the number that matters most. Capture times survived
the import to within a second of the manifest — on Android through
`MediaStore.DATE_TAKEN` in epoch milliseconds, on iOS through
`PHAsset.creationDate` — which is two entirely separate metadata paths arriving
at the same instant. That is the payoff from anchoring capture times to a
timezone in the generator.

The interesting difference is throughput, and the physical devices settle it:

| path | rate | 64 GB would take |
|---|---|---|
| simulator (`addmedia`, host disk) | 63.8 MB/s | — |
| **Galaxy S24 over Wi-Fi** | **11.9 MB/s** | ~1.5 h |
| Pixel emulator over adb | 4.5 MB/s | ~4 h |
| **iPhone 15 Pro over Wi-Fi** | **~2.2 MB/s** | ~8 h |

On real hardware the transfer, not the encode, is the bound — which is the
argument for running the generator on the LAN. The five-fold gap between the two
handsets on the same network is worth a second look before anyone plans a 64 GB
iPhone fill.

## What the format and edge-case runs found

The HEIC/HEVC pack indexed cleanly on both platforms, and Android assigned
`image/heic` to every HEIC rather than falling back to a generic type — so the
v1 format gap is closed with evidence, not assertion.

The edge-case pack turned up real behaviour worth knowing:

- **A zero-byte file and a screenshot are both indexed**, but with a **null
  `DATE_TAKEN`** — Android populates that column from EXIF, and neither has
  any. An app that assumes every library asset has a capture time will find
  these first.
- **Non-ASCII filenames survive** `adb push`, the MediaStore scan and the
  checksum read-back. They did *not* survive the control plane's file route
  until this run, which is what prompted allowlisting names from the manifest
  instead of matching an ASCII-only pattern.
- **The truncated JPEG and the exact duplicate both index normally.** Nothing
  upstream rejects them, which is the point — the app under test is what has to
  cope.

## Known gaps in the harness itself

- **A physical iPhone cannot be verified by `tdg verify`.** The Photos database
  is not reachable from outside the app. The workaround used for the real-device
  run is to pull the loader's own receipt off the phone with
  `devicectl device copy from` — the app's account of every localIdentifier it
  created. Real measurement, but the loader's word rather than Photos'. Folding
  that into `tdg verify` as a `device-ios` target is the obvious next step.
- **`--target emulator` works on a physical Android handset** over USB
  debugging — the same MediaStore query. That path is untested only because
  there is no handset here.
- **iOS assets are matched by instant, not name**, because Photos renames every
  import to `IMG_NNNN`. The comparison is windowed to the pack's own date range
  so the runtime's stock photos are not counted; a pack whose dates overlapped
  those would need a tighter window.
- **Album grouping is reported, not enforced.** `simctl addmedia` cannot create
  albums at all, so that check reports "n/a" for the simulator rather than
  failing something the CLI route was never able to do.
