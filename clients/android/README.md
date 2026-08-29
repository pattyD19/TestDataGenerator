# TDG Android loader

The ~600 lines that a browser cannot replace: reads a manifest, streams the
bytes, and inserts them into the camera roll through `MediaStore`.

Phase 4 of [the plan](../../PLAN.md).

## Build and install

```bash
export JAVA_HOME="$HOME/Applications/Android Studio.app/Contents/jbr/Contents/Home"
export ANDROID_HOME="$HOME/Library/Android/sdk"
gradle assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

`local.properties` is machine-specific and gitignored; recreate it with
`echo "sdk.dir=$ANDROID_HOME" > local.properties`.

## Using it

1. Build a pack in the [control plane](../../packages/web) and note the
   six-digit code it shows.
2. Open the app, type the control plane's address and that code, tap
   **Find pack**.
3. It reports the file count, the size and the free space on the device, and
   only enables **Fill the gallery** if the pack will fit.

The pairing code alone is enough: a phone has a keypad and no way to know a
twelve-hex-digit job id, so `GET /api/pair/<code>` resolves it to a manifest URL.

## The parts that matter

**`MediaStore.insert`, not a file copy.** Files merely written into DCIM are
indexed at the OEM's discretion. Inserting a row is the supported path and the
whole reason this app exists.

**`IS_PENDING` while the bytes are in flight.** The row is hidden from every
gallery until the transfer completes, so an interrupted fill never surfaces a
half-written photo. Publishing is a separate step; a failed transfer discards
the row instead of leaving a stub that would block a retry.

**`DATE_TAKEN` from the manifest**, in epoch milliseconds, taken from
`taken_at_utc`. This is what galleries group by, and getting it right is the
point of the whole timezone chain in the generator.

**A foreground service.** Android's own background limits — never mind
Samsung's sleeping-apps and adaptive battery — will suspend a twenty-minute
network-and-IO job run any other way.

**A receipt per job**, written atomically to app storage, mapping each file to
the content URI it became. It makes a fill resumable after a kill, and it makes
wipe exact: the app deletes the URIs it recorded and nothing else. It is keyed
on the URI rather than the filename deliberately — the filename is not a
reliable handle for an imported asset, and the URI is what `delete` needs.

**No storage permission at all.** Since API 29 an app needs none to write, read
back or delete media it owns. Wipe is a plain `delete` with no system prompt,
which is what makes it usable daily.

## Dependencies

`core-ktx`, `appcompat`, and coroutines. JSON comes from the platform's
`org.json` and HTTP from `HttpURLConnection` — no OkHttp, no Moshi. A loader
with almost no dependencies is one that still builds in two years, which is the
same argument the rest of this repo makes for hand-rolling.

## Verified

Run against a Pixel 9 emulator, **Android 17 (API 37, arm64)**, 2026-08-29:

| | |
|---|---|
| pairing | six digits resolved to the pack; label, count, size and free space shown |
| fill | 18 photos, then a second pack of 18 photos + 6 videos |
| indexing | every file in MediaStore — images in `Images.Media`, videos in `Video.Media` with correct durations |
| `DATE_TAKEN` | matched the manifest's UTC instants to under a second |
| album | `RELATIVE_PATH` = `DCIM/TDG <job>`, `IS_PENDING` cleared |
| wipe | "Removed 18 of 18"; MediaStore rows back to zero, receipt cleared |

## Known limits

- **Not tested on a physical handset.** An emulator does not exercise Samsung's
  battery manager, One UI's Gallery grouping, or slow eMMC storage — the three
  things §4 of the plan says to check. Emulator success is necessary, not
  sufficient.
- **Android 11 floor is declared, not verified.** `minSdk` is 30 but the only
  run so far is API 37.
- **Resume is per-file, not per-byte.** A killed transfer re-fetches the file it
  was on. The `Range` support in `Downloader` is there for it, but the service
  currently restarts a partial file rather than continuing it.
- **Wipe leaves the empty album directory.** The rows and bytes go, but
  `DCIM/TDG <job>/` remains; scoped storage gives the app no way to remove a
  directory it does not own media in. Some galleries may show an empty album
  until the OS tidies it.
- **One job at a time.** Loading a second pack while one is running is ignored
  rather than queued.
- **Cleartext HTTP is permitted** so the app can reach a plain-HTTP control
  plane on a lab LAN. Internal tool only.
