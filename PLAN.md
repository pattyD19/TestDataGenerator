# Mobile Test Data Generator (TDG) — Build Plan

**Goal:** fill an arbitrary iOS or Android device (or emulator) with N gigabytes of
open-license photos and videos that land in the **native camera roll**, carry
realistic EXIF and spread-out capture dates, and can be wiped again on demand.

**Primary use case:** exercising apps that scan the gallery — backup/sync, dedupe,
timeline/album grouping, migration.

---

## 0. Decisions (29 Aug 2026)

| Question | Decision | Consequence |
|---|---|---|
| Device matrix | **Pixel, Samsung and iOS only** for the first versions, each with one older-OS floor device (Android 11 / iOS 17). Xiaomi, Oppo and Motorola deferred. | Removes the worst of the OEM risk. Pixel is the AOSP reference, Samsung is the install base that matters, iOS is iOS. Keeps a trimmed conformance harness — see §4. |
| Formats in v1 | **JPEG + MP4 only.** HEIC/HEVC deferred to the realism phase. | Cuts ~4 days from Phase 1. Creates a known coverage gap: real iPhones shoot HEIC, so v1 does not exercise that path. Tracked as a risk. |
| CI automation | **Manual triggering.** Emulator scripting comes free with Phase 2; no device farm. | No headless pairing, no token rotation, no farm plumbing. QR pairing is sufficient. |
| Seed pool | **Public open-license sources.** No internal corpus. | Seed harvester is a real Phase 1 deliverable. Prefer CC0/public-domain assets so packs can circulate freely; see §2. |

---

## 1. The one constraint that shapes everything

A pure web app **cannot** write into the camera roll.

| Target | Can a browser put files in the gallery? | What actually works |
|---|---|---|
| Android | No. Downloads land in `/Download`; MediaStore indexing of that folder is OEM-dependent. | A tiny native app calling `MediaStore` `ContentResolver.insert()` — **no storage permission needed on Android 10+** for files the app owns. Or `adb push` + media scan. |
| iOS | No. Safari saves to Files; "Save to Photos" is one file at a time via the share sheet. | A tiny native app calling `PHAssetCreationRequest` with **Add Photos Only** permission. |
| iOS (AFC / `ifuse` → DCIM) | — | **Don't build on this.** Files copied into DCIM over AFC are not indexed by the Photos database. Known to fail. |
| iOS Simulator | — | `xcrun simctl addmedia <udid> <files...>` — fully scriptable. |
| Android emulator | — | `adb push` + `content call --uri content://media/external/file --method scan_file`. |

**Conclusion:** web-based control plane + thin native loaders. The web app does 95% of
the work (choosing, generating, serving, tracking); the loader is a ~300-line app per
platform whose only job is "read manifest → stream bytes → insert into gallery."

---

## 2. Architecture

```
┌───────────────────────────────────────────────┐
│  WEB CONTROL PLANE  (browser)                 │
│  size in GB · photo/video mix · formats ·     │
│  date range · device persona · albums         │
│  → creates a Job, shows a QR / 6-digit code   │
└───────────────────┬───────────────────────────┘
                    │
┌───────────────────▼───────────────────────────┐
│  GENERATOR SERVICE  (Node or Python, LAN or   │
│  cloud)                                       │
│   • seed pool  (real CC0 media, fetched once) │
│   • amplifier  (ffmpeg / sharp / exiftool)    │
│   • manifest   (JSON: url, bytes, sha256,     │
│                 exif, filename, album)        │
│   • byte-accurate size planner                │
└───────────────────┬───────────────────────────┘
                    │  HTTP over LAN (fast) or internet
     ┌──────────────┼──────────────┬─────────────────┐
     ▼              ▼              ▼                 ▼
 Android app    iOS app        CLI loader      CI loader
 MediaStore   PHAssetCreation  → folder     simctl / adb
```

### Why a **seed pool + local amplifier**

Stock-photo APIs are explicitly **not** for bulk harvesting:

- **Pixabay** — 100 req / 60 s; results must be cached 24 h; *"systematic mass downloads"* prohibited; download to your own server rather than hotlink.
- **Pexels** — 200 req/hour, 20,000/month; attribution required; circumventing limits terminates access.
- **Unsplash** — stricter still (download-tracking endpoint, no replicating the core experience). Verify their API terms before including.
- **Wikimedia Commons / Openverse** — generous but expect a descriptive User-Agent and polite pacing.
- **Blender open movies** (Big Buck Bunny, Sintel, Tears of Steel) — CC-BY, full-resolution masters, ideal video seed.

So: fetch a **seed pool once** (~500–2,000 assets, a few GB, respecting every limit),
store it with a license manifest, then **multiply it locally** to any GB target. One
polite harvest supports unlimited 128 GB test runs, forever, offline.

**Bias the harvest toward CC0 / public domain.** Openverse can filter to CC0; Wikimedia
has a large PD pool. Attribution-required sources (Pexels, Blender) are fine but every
pack built from them must ship `LICENSES.csv`, and packs should not be redistributed
outside the company. A mostly-CC0 seed pool means test packs can move freely between
teams and vendors without a licensing conversation each time.

### The amplifier

Each output = seed asset + deterministic transform, seeded by `(jobId, index)` so runs
are reproducible:

- random crop / rescale / rotate / mild colour jitter / re-encode at varied quality
- fresh EXIF: `Make`/`Model`/`LensModel` from a device persona, `DateTimeOriginal` drawn
  from your date distribution, GPS jittered around a set of cities
- videos: re-encode a random segment at a target bitrate, duration and codec

Re-encoding means **no two files are byte-identical** — important when the app under
test does content hashing or dedupe. When you *want* dedupe exercised, the edge-case
pack emits exact byte copies on purpose.

**v1 encode targets:** JPEG (quality 70–92, 8–48 MP) and H.264/AAC in MP4 (1080p and
4K, 20–60 Mbps). Nothing else until Phase 6.

### Hitting the GB target exactly

JPEG size is content-dependent, so don't estimate — **measure**:

1. Plan a manifest from running averages per (format, resolution) bucket.
2. Encode, measure real bytes, subtract from remaining budget, re-plan.
3. Video is the precise knob: `bytes ≈ (video_bitrate + audio_bitrate) × duration / 8`.
   Use one final video clip with a computed duration to land within ±0.5%.
4. Loader checks device free space against manifest total **before** writing a byte.

Reference planning numbers (70% photos by size, 3.5 MB photos, 45 s 4K/45 Mbps clips):

| Target | Photos | Video clips (~253 MB each) |
|---|---|---|
| 5 GB | ~1,000 | 6 |
| 25 GB | ~5,100 | 32 |
| 64 GB | ~13,100 | 81 |
| 128 GB | ~26,200 | 163 |

Expose **both** GB and file-count as knobs — 26,000 small files stresses a backup client
very differently from 200 large ones.

### Transfer time for 64 GB

| Path | Time |
|---|---|
| Wi-Fi 6, generator on LAN (~50 MB/s) | ~22 min |
| `adb push` over USB 3 (~35 MB/s) | ~31 min |
| Wi-Fi 5 (~20 MB/s) | ~55 min |
| Internet, 100 Mbit (~12 MB/s) | ~91 min |

**Run the generator on the LAN.** Pulling 64 GB from the cloud for every test run is
slow and expensive; the same box can serve every device in the lab.

---

## 3. Cleanup — design it in from day one

Filling a device is easy; *un*-filling it reliably is what makes the tool usable daily.

- Every file named `TDG_<jobId>_<seq>.<ext>`, and every asset placed in a dedicated
  album (`TDG <jobId>`) — but **neither is the deletion mechanism.** Verified
  2026-08-29: iOS renames every imported asset to `IMG_NNNN`, so the filename does
  not survive an import and nothing may key off it.
- **The receipt is the deletion mechanism.** The loader records a handle for every
  asset it wrote and never touches anything else. The handle is per-target: an
  absolute path for `folder`, a device path for `emulator`, and the `PHAsset`
  `localIdentifier` for the iOS app. That is what makes the filename question moot.
- `tdg wipe --job <id>` on each loader. Because the loader **owns** every file it wrote,
  Android can delete them with no storage permission at all — `MediaStore.createDeleteRequest`
  is only needed for files the app doesn't own. iOS uses `PHAssetChangeRequest.deleteAssets`
  on the recorded identifiers (one system confirmation per batch).
- The receipt lives outside the pack, so wipe works even if the generator and the
  pack are both gone.

**Deletion status per target, as actually tested:**

| Target | Delete individual assets? | How |
|---|---|---|
| `folder` | yes | unlink the recorded paths — verified |
| `emulator` | yes | `adb shell rm` + re-scan so MediaStore drops the rows — verified on Android 17: files, directory and rows all gone |
| `simulator` | **no** | simctl has no delete-media verb (confirmed against Xcode 26.6). `simctl erase` is the only route and resets the whole device — verified working, device reusable after. Acceptable because a simulator is disposable. |
| Android device (Phase 4) | yes | `ContentResolver.delete` on the content URIs in the app's receipt — no storage permission and no system prompt, because the app owns them. Verified on an Android 17 emulator: 18 of 18 removed, MediaStore rows to zero. The empty `DCIM/TDG <job>/` directory is left behind. |
| iOS device (Phase 5) | yes | `PHAssetChangeRequest.deleteAssets` on recorded `localIdentifier`s. Verified on an iOS 26.5 simulator: 24 of 24 removed, stock photos untouched. Needs **full** library access, which iOS will not pre-authorize — the user grants it once, then confirms a system alert naming the exact count. Deleted assets sit in Recently Deleted for 30 days, so space is not reclaimed at once. **Still unproven on a physical iPhone.** |

---

## 4. The device matrix

**v1 covers Pixel, Samsung and iPhone.** Xiaomi, Oppo and Motorola are deferred, which
removes most of the schedule risk that a wide OEM matrix carried. The shopping list:

| Device | Role |
|---|---|
| Current Pixel | The AOSP reference. If it doesn't work here, it's our bug, not the OEM's. |
| Current Galaxy S | Samsung One UI — the biggest real install base, its own Gallery app. |
| Galaxy A-series | Samsung's budget tier: slower storage, less RAM, tighter battery management. Where a 64 GB fill actually strains. |
| Pixel or Galaxy on Android 11 | The OS floor. Catches permission and scoped-storage differences. |
| Current iPhone | Photos, HEIC-native (even though v1 writes JPEG). |
| iPhone on iOS 17 | The iOS floor. |

Three things still need care even on this narrower matrix:

1. **Background execution.** Android's own limits — not just OEM battery managers — will
   suspend a 20-minute network-and-IO job. The Android loader runs as a **foreground
   service** with an ongoing notification, and resumes from its receipt after a kill.
   Samsung's "sleeping apps" and adaptive battery make this non-optional; Pixel is
   better behaved but not exempt.
2. **Samsung Gallery vs. MediaStore.** `MediaStore.insert()` is standard and Samsung
   honours it, but Samsung Gallery does its own grouping and album handling. Worth
   verifying rather than assuming.
3. **Permission drift across the floor.** Android 11 → 14 changed media permissions
   repeatedly (granular `READ_MEDIA_IMAGES`/`READ_MEDIA_VIDEO` in 13, partial photo
   access in 14). Writing is unaffected; reading back for verification and wipe is not.

### Test-device hygiene — read this before the first fill

Every device in this matrix ships with a photo backup service that is **on by default**:
Google Photos on Pixel, Samsung Cloud and often OneDrive on Galaxy, iCloud Photos on
iPhone. Push 64 GB into the camera roll and they will begin uploading it.

That is worse than an annoyance when the thing under test *is* a backup client: it
pollutes the network measurements, competes for bandwidth, and can quietly consume paid
cloud storage. **Disable every stock sync service on every test device, and make that a
checklist item the loader verifies where it can.** Dedicated test accounts, never
personal ones.

**Deliverable:** a trimmed **conformance harness** (`tdg verify`) that runs after a fill
and reports, per device: how many assets the gallery actually indexed, whether
`DATE_TAKEN` survived, whether album grouping held, and how long the fill took. Six
devices rather than a dozen, so this is days rather than a week. Results live in
`docs/conformance/`.

---

## 5. Phased build

| Phase | Deliverable | Rough effort |
|---|---|---|
| **0. Spike** | Prove the two risky bits: 500 assets into iOS Photos via `PHAssetCreationRequest` with correct `creationDate`, and 500 into Android MediaStore with correct `DATE_TAKEN`. Nothing else matters if these are slow or lossy. | 2–3 days |
| **1. CLI generator** ✅ | `tdg build --size 25GB --profile iphone-15-pro --out ./pack` → folder + `manifest.json` + `LICENSES.csv`. Amplifier, exact-size planner, hand-rolled EXIF writer, synthetic bootstrap seed pool, harvester written. **JPEG + MP4 only.** Built and verified 2026-08-29 — see `packages/generator/`. | done |
| **2. Desktop & CI loaders** ✅ | `tdg load --target simulator\|emulator\|folder`. Wraps `simctl addmedia` and `adb push` + media scan. Free-space preflight, resumable receipts stored outside the pack, `tdg wipe`, `tdg devices`, `tdg receipts`. Fake `adb`/`xcrun` fixtures so both device paths are testable with no device attached. Built 2026-08-29 and verified for real on an iPhone 17 Pro simulator (iOS 26.5) and a Pixel 9 emulator (Android 17): every asset indexed, capture times exact, wipe clean on both — see `packages/generator/tdg/loader.py`. | done |
| **3. Web control plane** ✅ | Browser front end + generator API. Presets, job creation, live progress over SSE, cancel/resume, LAN mode, per-job pairing code, and a pack API with `Range` support that loaders consume directly. **Built as stdlib Python + hand-written JS, not Next.js** — the generator is Python, so the server imports it and reads progress from the build's own checkpoint; no second runtime, no build step. QR pairing deferred until Phase 4/5 give it something to scan. Built 2026-08-29 — see `packages/web/`. | done |
| **4. Android loader** ✅ | Kotlin, minSdk 30, single screen: six-digit code → stream manifest → `MediaStore.insert()` with `IS_PENDING` → `DATE_TAKEN`/`RELATIVE_PATH`. Foreground service + per-job receipts. Plus wipe, which needs no storage permission because the app owns every file it wrote. Verified on a Pixel 9 emulator (Android 17): 24 assets indexed, photos and video, `DATE_TAKEN` exact, wipe clean. **Not yet run on a physical Samsung or on the Android 11 floor.** Built 2026-08-29 — see `clients/android/`. | done (emulator) |
| **5. iOS loader** ✅ | SwiftUI, iOS 17 floor, same flow → batched `PHAssetCreationRequest` (100 per `performChanges`). Plus wipe. **Permission is staged: add-only to fill, escalating to full access only for wipe** — verified that `.addOnly` suffices for every creation including video. Verified on an iPhone 17 Pro simulator (iOS 26.5): 24 assets imported, `creationDate` exact to 0.000 s, wipe removed all 24 leaving the stock photos untouched. **No signing identity on this machine, so never run on a physical iPhone and not near TestFlight.** Built 2026-08-29 — see `clients/ios/`. | done (simulator) |
| **6. Conformance harness** ◐ | `tdg verify` asks the *device* what its gallery indexed rather than trusting the loader — assets indexed, capture time preserved, album grouping, fill duration — and exits non-zero on failure so it can gate CI. Results in `docs/conformance/`. **Two of six devices covered, both emulated**; the four physical handsets and both OS floors are unrun for want of hardware, and a physical iPhone cannot be verified this way at all. Built 2026-08-29 — see `packages/generator/tdg/verify.py`. | harness done, matrix partial |
| **7. Realism & formats** | **HEIC/HEVC** (closes the v1 gap), device personas, burst sequences, screenshots, portrait/Live-Photo pairs, duplicates, zero-byte and corrupt files, unicode filenames, 4 GB single file. | 1–1.5 weeks |

Phases 1–2 alone give a working tool for simulators and CI in about two weeks. Phases
4–5 unlock real handsets. Narrowing to Pixel, Samsung and iOS takes roughly a week out
of phases 4 and 6 combined.

---

## 6. Tech choices

- **Generator:** Node 20 + `sharp` (JPEG) and `ffmpeg` (all video). `exiftool` for
  metadata the libraries won't write cleanly. `libheif` deferred to Phase 7.
- **Web:** Next.js + Tailwind. Job state in SQLite/Postgres. Server-sent events for progress.
- **Manifest:** versioned JSON schema — the contract between server and every loader.
  Keep it clean enough that a device farm *could* consume it later, but build no farm plumbing now.
- **Android:** Kotlin, **minSdk 30** (Android 11 floor), OkHttp streaming, foreground
  service. No storage permission needed for write or for wiping own files.
- **iOS:** SwiftUI, **iOS 17 floor**, `NSPhotoLibraryAddUsageDescription` (add-only).
  Request full access only if you need to create albums.
- **Packaging:** the generator ships as a Docker image so any lab machine becomes a source.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| iOS Photos import is slow at scale (10k+ assets) | Measure in Phase 0. Batch 100–200 per transaction; show honest ETA; consider overnight runs for 128 GB. |
| **Stock cloud backup uploads the test corpus** | Google Photos, Samsung Cloud/OneDrive and iCloud Photos are on by default. Disable all of them on every test device, use dedicated accounts, and make it a pre-flight checklist item. |
| Android suspends the loader mid-fill | Foreground service + ongoing notification; resumable receipts. Verify on Samsung's aggressive battery settings, not just Pixel. |
| Deferred OEMs surface late | Xiaomi/Oppo behaviour is unknown until someone tests it. Keep the conformance harness generic so adding a device is a run, not a rewrite. |
| **v1 ships no HEIC/HEVC** | Accepted trade. Real iPhones shoot HEIC, so v1 under-tests the format path — Phase 7 closes it. Don't sign off backup coverage on v1 alone. |
| Device runs out of space mid-run | Pre-flight free-space check; resumable manifest with per-file receipts. |
| API terms / rate limits | Seed pool fetched once, cached, attributed, CC0-biased. Never harvest per test run. `LICENSES.csv` with every pack. |
| Amplified media looks obviously synthetic | Seed pool breadth matters more than count — spread across subjects, lighting, orientations. |
| Distributing an internal iOS app | TestFlight internal testing; budget for provisioning-profile churn. |
| Test data leaks into a real backup account | Dedicated test accounts; make `wipe` a first-class, well-tested command. |

---

## 8. Still to settle

1. Whether attribution-required media (Pexels, Blender) is acceptable inside shared test
   packs, or whether the seed pool should be **CC0-only** to keep packs unencumbered.
2. Whether the Galaxy A-series budget device is in scope for v1 or joins with the
   deferred OEMs — it is the cheapest way to find out how the loader behaves on slow
   storage.
