# TDG iOS loader

SwiftUI, iOS 17 floor. Reads a manifest, streams the bytes, and adds them to the
Photos library through `PHAssetCreationRequest`.

Phase 5 of [the plan](../../PLAN.md).

## Build and run

```bash
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
xcodebuild -project TdgLoader.xcodeproj -scheme TdgLoader \
           -sdk iphonesimulator -configuration Debug -derivedDataPath build build
xcrun simctl install booted build/Build/Products/Debug-iphonesimulator/TdgLoader.app
```

`DEVELOPER_DIR` is used rather than `xcode-select -s` because the latter needs an
admin password. The project is hand-written and uses a file-system-synchronized
group, so new Swift files in `TdgLoader/` are picked up with no project edit.

## Using it

Type the control plane's address and the six-digit code it showed, tap **Find
pack**, then **Fill the library**. The app reports the file count, the size and
the free space, and refuses to start if the pack will not fit.

For CI, or any machine where the simulator cannot be tapped, launch arguments
land in `UserDefaults`' argument domain for free:

```bash
xcrun simctl launch booted com.tdg.loader \
    -host http://127.0.0.1:8722 -code 123456 -autostart 1
xcrun simctl launch booted com.tdg.loader -autowipe 1
```

## Permission is asked for in two stages

This is the design decision that matters most here.

Filling the library needs only **add-only** access, so that is all the app asks
for up front — a tester who only ever loads packs never hands this tool their
whole photo library. Verified on iOS 26.5: `.addOnly` is enough for every
`PHAssetCreationRequest` the app makes, video included.

Add-only cannot fetch or delete, though, so **wipe escalates to full access** at
the moment it is needed, and the usage string says why. iOS then shows two
prompts in sequence: one to grant full access, and one system confirmation
naming the exact number of assets to be deleted.

A consequence worth knowing: under add-only the app cannot create the
`TDG <job>` album, because collections need full access. The album is a
convenience for finding assets by hand — the receipt, not the album, is what
makes wipe exact.

## The parts that matter

**Batched imports.** Assets are created 100 per `performChanges` transaction.
One transaction for 26,000 assets stalls the library; one per asset spends all
its time in round trips.

**`creationDate` from the manifest**, taken from `taken_at_utc`. This is what
Photos groups a timeline by. Measured delta against the manifest: **0.000 s**.

**Downloads land in a temp file**, never in memory, and are handed to Photos
with `shouldMoveFile` — a 4 GB clip must not become 4 GB of resident memory.

**A receipt per job**, written atomically, keyed on the `PHAsset`
**localIdentifier**. Photos renames every imported asset to `IMG_NNNN`, so a
filename-keyed wipe would find nothing; the identifier is also exactly what
`deleteAssets` needs. It makes a fill resumable and wipe exact.

## Verified

iPhone 17 Pro simulator, **iOS 26.5**, 2026-08-29, against a 24-file pack
(20 photos, 4 videos):

| | |
|---|---|
| pairing | six digits resolved the pack; label, count, size and free space shown |
| import | 24 of 24, with add-only permission alone |
| kinds | 20 photos and 4 videos, each in the right collection |
| `creationDate` | matched the manifest's UTC instants exactly — 0.000 s delta |
| album | created and populated when full access was granted; correctly skipped under add-only |
| wipe | "Removed 24 assets"; 0 live imported assets left, the 6 stock photos untouched |

## Known limits

- **Simulator only.** There are no signing identities on this machine, so the
  app has never run on a physical iPhone and has not been near TestFlight.
  A real device adds provisioning, a real Photos library, and iCloud Photos
  syncing behaviour that a simulator does not model.
- **iOS 17 floor is declared, not verified.** `IPHONEOS_DEPLOYMENT_TARGET` is
  17.0 but the only runs are on 26.5.
- **Deleted assets go to Recently Deleted for 30 days.** Space is not reclaimed
  immediately, which matters when the point of the exercise was filling a device.
- **Resume is per-file, not per-byte.** A killed transfer re-fetches the file it
  was on.
- **`simctl privacy grant photos` does not pre-authorize full access** on
  iOS 26.5 — the prompt appears regardless, so an unattended wipe cannot be
  fully scripted. Add-only *can* be pre-granted, so unattended **fills** work.
- **Cleartext HTTP** is allowed by default for simulator builds reaching a
  plain-HTTP control plane; a device build pointed at a real host would need an
  ATS exception. Internal tool only.
