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

The plan calls for six devices. Two are covered, both emulated:

| Device | Role | Status |
|---|---|---|
| [Pixel 9 emulator, Android 17](pixel-9-emulator-android-17.md) | AOSP reference | **passed** |
| [iPhone 17 Pro simulator, iOS 26.5](iphone-17-pro-simulator-ios-26.5.md) | iOS reference | **passed** |
| [Pixel 9, edge-case pack](pixel-9-emulator-edge-cases.md) | duplicates, zero-byte, truncated, non-ASCII names, a screenshot | **passed** |
| [iPhone 17 Pro, HEIC + HEVC pack](iphone-17-pro-simulator-heic-hevc.md) | the formats a real iPhone actually produces | **passed** |
| Current Pixel (physical) | AOSP reference on real hardware | not run — no device |
| Current Galaxy S | One UI, its own Gallery app | not run — no device |
| Galaxy A-series | budget tier: slow storage, tight battery management | not run — no device |
| Pixel or Galaxy on Android 11 | the OS floor | not run — no device |
| Current iPhone (physical) | Photos, HEIC-native | not run — no device |
| iPhone on iOS 17 | the iOS floor | not run — no device |

**An emulator pass is necessary, not sufficient.** It does not exercise the
three things §4 actually warns about: Samsung's battery manager suspending a
long fill, One UI's Gallery doing its own album grouping, or slow eMMC storage
on a budget handset. Nor does it touch the OS floors — `minSdk 30` and
`IPHONEOS_DEPLOYMENT_TARGET 17.0` are both declared rather than tested.

## What the two passes tell us

Both platforms agree on the number that matters most. Capture times survived
the import to within a second of the manifest — on Android through
`MediaStore.DATE_TAKEN` in epoch milliseconds, on iOS through
`PHAsset.creationDate` — which is two entirely separate metadata paths arriving
at the same instant. That is the payoff from anchoring capture times to a
timezone in the generator.

The interesting difference is throughput: **4.5 MB/s over adb against
63.8 MB/s into the simulator**. The simulator writes to the host's own disk;
adb is a real transfer. On a physical device the transfer, not the encode, is
the bound — which is the argument for running the generator on the LAN.

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

- **A physical iPhone cannot be verified this way.** The Photos database is not
  reachable from outside the app, so `--target simulator` reads a file that only
  exists on a simulator. Verifying a real iPhone needs the loader app to report
  back, which is not built.
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
