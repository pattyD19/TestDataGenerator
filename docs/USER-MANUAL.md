# TDG — User Manual

TDG fills a phone's camera roll with an exact number of gigabytes of realistic
photos and video, and takes every byte back on demand. This manual takes you
from a bare machine to a filled handset.

If you only have five minutes, [§4](#4-building-your-first-pack) is the part
that matters — everything before it is setup you do once.

**Contents**

1. [Getting the project](#1-getting-the-project)
2. [What you need](#2-what-you-need)
3. [First-time setup](#3-first-time-setup)
4. [Building your first pack](#4-building-your-first-pack)
5. [Getting a pack onto a device](#5-getting-a-pack-onto-a-device)
6. [FAQ](#6-faq)

---

## 1. Getting the project

```bash
git clone https://github.com/pattyD19/TestDataGenerator.git
cd TestDataGenerator
```

There is nothing to install after cloning — no `pip install`, no `npm install`,
no build step. Every Python entry point is run with `python3 -m`, and the web
control plane is stdlib Python serving hand-written JavaScript.

What you get:

| | |
|---|---|
| `packages/generator` | the `tdg` CLI — harvest, build, load, verify, wipe |
| `packages/web` | the browser control plane (`serve.py`) |
| `clients/android` | the Kotlin loader app |
| `clients/ios` | the SwiftUI loader app |
| `docs/conformance` | what `tdg verify` found on each real device |

**Two things are deliberately *not* in the clone**, because both are large and
neither belongs in version control:

- **`seed-pool/`** — the source photographs every pack is built from. You
  populate it once, in [§3](#3-first-time-setup). A fresh clone cannot build a
  pack until you do.
- **`packages/web/packs/`** — built packs. These are the gigabytes; they are
  produced on demand and reclaimed with `prune`.

---

## 2. What you need

### To build and serve packs

| | Minimum | Notes |
|---|---|---|
| **OS** | macOS, Linux, or WSL | Developed on macOS; the generator and server are portable |
| **Python** | **3.9+** | For `zoneinfo`. Currently developed against 3.14 |
| **Pillow** | any recent version | The only hard third-party dependency |
| **numpy** | any recent version | Only needed for `bootstrap-seeds` (the offline seed fallback) |
| **ffmpeg** | any recent build | Must be on `PATH`, with `libx264` — and `libx265` if you want HEVC |
| **Disk** | pack size + ~1 GB | A 25 GB pack occupies 25 GB. The seed pool adds a few hundred MB |
| **CPU** | anything; more cores is faster | Build time is CPU-bound and scales nearly linearly |

Optional:

- **`pillow-heif`** — only if you want `--photo-format heic` on Linux. On macOS
  the generator uses the built-in `sips` and needs no install at all.

Check what you have:

```bash
python3 -V && python3 -c "import PIL, numpy; print('Pillow', PIL.__version__, 'numpy', numpy.__version__)" && ffmpeg -version | head -1
```

If Pillow or numpy are missing, install them however your platform prefers —
`pip3 install --user Pillow numpy`, `brew install pillow`, `apt install
python3-pil python3-numpy`. On a distribution that marks its Python as
externally managed (PEP 668), either use the system packages or a virtualenv;
`pip3 install` into the system Python will be refused.

### Build time and throughput

Worth knowing before you ask for 64 GB. Measured on a 10-core Apple Silicon
laptop at the default 70/30 photo/video mix:

| | |
|---|---|
| photos, all cores | ~33 MB/s |
| video, 4K, default `--video-jobs 4` | ~4.6 MB/s |
| a 64 GB pack, mixed | ~1.5 h on a laptop, ~2.5 h on a 4-core VM |

Video is the expensive half, so `--photo-fraction` governs build time far more
than the codec does. A photo-only pack runs roughly twice as fast.

### Extra requirements, only if you want them

**To load onto an iOS Simulator or Android emulator from the CLI** — Xcode
command-line tools (`xcrun simctl`) and/or the Android SDK platform-tools
(`adb`) on `PATH`. Neither is needed to build a pack.

**To build the Android app** — JDK 17, Android SDK with API 37, and Gradle 9.
Android Studio supplies all three; the app itself targets Android 11 (API 30)
and up.

**To build the iOS app** — macOS with a full Xcode (not just the Command Line
Tools), and an Apple developer team if you want it on a physical iPhone. The
app targets iOS 17 and up.

---

## 3. First-time setup

### 3.1 Populate the seed pool

Every still in a pack is a crop of a seed image, re-encoded with its own EXIF.
Stock APIs prohibit bulk downloading, which is the whole point of the design:
**one polite harvest supports unlimited packs, at any size, forever, offline.**

```bash
cd packages/generator
python3 -m tdg.cli harvest --images 200
```

That takes a few minutes and writes into the repo's `seed-pool/`, along with a
`seeds.json` recording the licence, author and origin of every file. It fetches
CC0 and public-domain media only; attribution-required licences need an
explicit `--any-license`.

**With no network**, or to get moving immediately:

```bash
python3 -m tdg.cli bootstrap-seeds --count 48
```

This synthesises textured stills locally. It is fine for proving the plumbing
and wrong for anything that looks at the results — see
[the FAQ](#how-many-seeds-do-i-actually-need) for why pool size matters more
than it sounds like it should.

### 3.2 Confirm it works

Five test suites, no third-party runner, 190 checks between them:

```bash
python3 packages/generator/tests/test_exif.py      # capture times and EXIF
python3 packages/generator/tests/test_loader.py    # device loaders, via fakes
python3 packages/generator/tests/test_resume.py    # interrupted builds
python3 packages/generator/tests/test_formats.py   # HEIC, HEVC, edge cases
python3 packages/web/tests/test_server.py          # the control plane
```

Each prints `all checks passed` or exits non-zero. The loader suite uses fake
`adb` and `xcrun` fixtures, so it passes with no device attached.

### 3.3 Start the control plane

```bash
python3 packages/web/serve.py
```

Then open **http://localhost:8722**.

It binds `0.0.0.0` on port 8722 so phones on the same network can pull packs
directly — the reason the generator is meant to run on your LAN rather than in
the cloud. Useful flags:

| | |
|---|---|
| `--port 8722` | change the port |
| `--packs ./packs` | where built packs are written |
| `--seeds <dir>` | a seed pool other than the repo's |
| `--verbose` | log every request |

> **The pairing code is a speed bump, not authentication, and there is no TLS.**
> This is a lab tool for a trusted network. Do not expose it to the internet.

You do not need the server to use the CLI, and you do not need the CLI to use
the server. They are two front doors onto the same generator.

---

## 4. Building your first pack

Start small. A 500 MB pack proves the whole path in a couple of minutes; a
64 GB one proves the same thing in ninety.

### 4.1 From the browser

1. Open **http://localhost:8722**.
2. Click a preset — **Smoke test** (500 MB) is the one to start with. It fills
   the whole form.
3. Press **Build pack**. Progress and the build log stream live.
4. When it finishes, the job card shows a **six-digit pairing code**. That code
   is the entire handle a phone needs.

The form's fields, and what each actually changes:

| Field | What it does |
|---|---|
| **Size** | Exact, not approximate. "25GB" lands on 25 GB, delta zero |
| **Device profile** | The camera make, model, resolutions and video mode written into the EXIF |
| **Photo share of bytes** | Decides file *count*. 26,000 small files stress a client very differently from 200 large ones |
| **Photo format** / **Video codec** | JPEG, HEIC or mixed; H.264 or HEVC |
| **Earliest** / **Latest capture** | Optional. The timeline the gallery will group on |
| **Edge cases** | A checkbox. Adds the awkward files — see [§4.3](#43-the-edge-cases-flag) |
| **Label** | Optional, and worth filling in — it is how you find the job later |

### 4.2 From the command line

The same generator, scriptable:

```bash
cd packages/generator
python3 -m tdg.cli build --size 500MB --out ./pack --profile iphone-15-pro
```

Real output from a 40 MB run:

```
Building 40.0 MB pack [iphone-15-pro] -> /tmp/tdg-manual-pack
  edge cases 10  total 3.8 MB
  photos 5  total 11.8 MB / 40.0 MB
  photos 11  total 25.6 MB / 40.0 MB
  pad photo 8.0 MB  deficit 4.2 MB
  pad photo 4.2 MB  deficit 0 B

  job          manual1
  files        24  (24 photos, 0 videos)
  target       40.0 MB
  actual       40.0 MB  (delta +0 bytes, +0.0000%)
  elapsed      2.8s  (14.5 MB/s)
  manifest     /tmp/tdg-manual-pack/manifest.json
```

The `pad` and `trim` lines are the size planner closing the gap — video
duration first, then a JPEG comment segment. **`delta +0 bytes` is the point of
the whole tool.**

Flags worth knowing on the first day:

| Flag | |
|---|---|
| `--size` | `25GB`, `512MB`, or raw bytes |
| `--profile` | `iphone-15-pro`, `pixel-8`, `galaxy-s24`, `galaxy-a54` |
| `--photo-fraction` | share of *bytes* that are stills (default 0.70) |
| `--since` / `--until` | capture-date range |
| `--job` + `--seed` | same values rebuild the same pack, byte for byte |
| `--edge-cases` | add the awkward files |
| `--photo-format` | `jpeg`, `heic`, `mixed` |
| `--video-codec` | `h264`, `hevc` |
| `--jobs` | parallel photo encoders (default: CPU count) |

Check what you got:

```bash
python3 -m tdg.cli inspect ./pack
```

```
job manual1  persona iphone-15-pro  schema v2
24 files, 40.0 MB (target 40.0 MB, delta +0 B)
capture range 2022-08-15T15:46:55-06:00 .. 2025-12-19T12:37:56-05:00
busiest months: 2025-12 (13), 2022-08 (10), 2025-11 (1)
prefix TDG_manual1_   album 'TDG manual1'
unique checksums: 23 / 24
```

### 4.3 The `--edge-cases` flag

Adds six assets that break naive gallery code, each of which has already found
a real bug: a **zero-byte file** (iOS refuses it, Android indexes it happily), a
**screenshot with no EXIF** (no capture date, so Samsung files it under
"Today"), an **exact duplicate**, a **burst of five** 380 ms apart, a
**truncated JPEG**, and a **non-ASCII filename**.

That duplicate is why `inspect` above reports 23 unique checksums out of 24. It
is the only repeat in the pack, and it is there on purpose.

### 4.4 What a pack contains

```
pack/
  TDG_<job>_00000.jpg     every file carries the job id, so wipe is exact
  TDG_<job>_00001.mp4
  ...
  manifest.json           the contract every loader reads
  LICENSES.csv            provenance of every seed the pack was built from
```

`manifest.json` carries per-item bytes, sha256, capture time and GPS, plus the
totals a loader checks against free space *before* writing anything.

### 4.5 If a build is interrupted

Re-run the same command. A partial build in `--out` is resumed from its
checkpoint rather than restarted, and lands the same exact-size pack. Use
`--restart` to discard it and begin again. From the browser, a cancelled job
offers **resume**.

---

## 5. Getting a pack onto a device

There are two routes, and which one you need depends on whether the target is
real hardware.

| Target | Route |
|---|---|
| iOS Simulator, Android emulator, a folder | **CLI** — `tdg load` |
| A physical iPhone or Android handset | **The loader app** + the web control plane |

The reason for the split is the single fact the whole architecture is built
around: **a browser cannot write to the camera roll.** Downloads land in a
sandbox. Only a native app can insert into MediaStore or Photos.

### 5.1 Simulators and emulators, from the CLI

```bash
python3 -m tdg.cli devices
```

```
booted simulators:
  3766D8F3-9DAB-41EA-A904-C40F29B30178  iPhone 17  (iOS-26-5)
adb devices:
  none
```

Simulators and adb devices are probed separately, so a missing toolchain on one
side cannot hide the other platform.

```bash
python3 -m tdg.cli load --pack ./pack --target simulator --verify
python3 -m tdg.cli load --pack ./pack --target emulator  --verify
python3 -m tdg.cli load --pack ./pack --target folder --dest /tmp/out
```

Free space is checked before a byte is written. `--verify` re-hashes what
landed. `--dry-run` resolves the device and checks space without writing
anything.

Each load writes a **receipt** to `~/.tdg/receipts/`, outside the pack. That
receipt is what makes a fill resumable and a wipe exact — and it is why a device
stays cleanable long after the pack it came from has been deleted.

### 5.2 Physical phones, through the loader app

**Step 1 — put the app on the phone.**

Android:

```bash
cd clients/android
export JAVA_HOME="$HOME/Applications/Android Studio.app/Contents/jbr/Contents/Home"
export ANDROID_HOME="$HOME/Library/Android/sdk"
echo "sdk.dir=$ANDROID_HOME" > local.properties
gradle assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

iOS (macOS + Xcode + a developer team):

```bash
cd clients/ios
cp Signing.xcconfig.example Signing.xcconfig     # then put your team ID in it
xcodebuild -project TdgLoader.xcodeproj -scheme TdgLoader \
           -sdk iphoneos -configuration Debug -derivedDataPath build-device \
           -allowProvisioningUpdates build
xcrun devicectl device install app --device <device-udid> \
      build-device/Build/Products/Debug-iphoneos/TdgLoader.app
```

`-allowProvisioningUpdates` registers an App ID on your developer account, so
run it deliberately. Installing works with the phone locked; *launching* does
not — unlock it first.

**Step 2 — find your machine's LAN address.**

The phone cannot reach `localhost`. On macOS:

```bash
ipconfig getifaddr en0
```

**Step 3 — pair and fill.**

1. Build a pack in the browser and note its six-digit code.
2. Open **TDG Loader** on the phone.
3. Type the control plane's address — `http://192.168.1.10:8722`, with your own
   address — and the six digits.
4. Tap **Find pack**. Before anything is written, the app shows the label, the
   file count, the total size, and the free space on *this* device. If the pack
   will not fit, the fill button stays disabled and says why.
5. Tap **Fill the gallery** (Android) or **Fill the library** (iOS).

The address is remembered between runs; only the code changes.

**Permissions.** Android asks only for notification permission, so the fill can
show progress — no storage permission is requested and none is needed. iOS asks
for local network access, then **add-only** photo access, which is all a fill
needs. Full access is requested separately, and only when you wipe.

**The fill survives the screen going off.** Android runs it in a foreground
service — verified for 15 minutes unplugged with the screen off on a Galaxy S24,
for a 10 GB, 3,466-file fill. Progress is per file, and **Stop** leaves what
already landed in place. Re-pairing the same pack resumes: files already in the
receipt are skipped.

### 5.3 Checking what the gallery actually did

```bash
python3 -m tdg.cli verify --pack ./pack --target emulator
```

This queries the *gallery database*, not the filesystem — it answers whether
the assets were indexed, not merely copied. It checks the count, capture times
against the manifest to sub-second tolerance, and album grouping, and exits
non-zero on failure so it can gate CI.

`--out report.md` writes a conformance report; see `docs/conformance/` for the
ones committed against real hardware.

### 5.4 Taking it back off

From the CLI:

```bash
python3 -m tdg.cli receipts                    # what has been loaded where
python3 -m tdg.cli wipe --job <id>
python3 -m tdg.cli wipe --job <id> --dry-run   # see what would go first
```

From the app: **Wipe everything this app wrote** (Android) or **Remove
everything this app added** (iOS).

Wipe reads the receipt and deletes only what it records. Nothing else in the
gallery is touched, and on Android the album folder it empties goes too. iOS
will ask you to confirm the exact asset count, and will need full photo access
to do it — add-only cannot delete.

### 5.5 Getting the disk back on the server

Packs are the only thing here that grows without bound. Pruning deletes a
pack's media and keeps its job row, so the job stays in the list and can be
rebuilt byte for byte from its id and seed.

In the browser, each job carries a **prune** link, and the Jobs panel offers
**Reclaim *N*** when there is anything to get back. Without a browser:

```bash
cd packages/web
python3 -m tdgweb.prune --packs ./packs --dry-run   # what would go
python3 -m tdgweb.prune --packs ./packs             # prune every eligible job
python3 -m tdgweb.prune --packs ./packs --job <id>  # just this one
```

A partial build is never pruned by accident — discarding a resume takes
`--force`. **Receipts are never touched**, so a device you have already filled
stays fully wipeable after its pack is gone.

---

## 6. FAQ

### The build says "no seed pool". What now?

Run the harvest. A fresh clone has no `seed-pool/` — it is gitignored, because
it is hundreds of megabytes of other people's media.

```bash
cd packages/generator
python3 -m tdg.cli harvest --images 200      # real media, needs a network
python3 -m tdg.cli bootstrap-seeds           # synthetic, needs nothing
```

The build exits non-zero and writes nothing if the pool is missing, so a
scripted run fails cleanly rather than producing a broken pack.

### How many seeds do I actually need?

More than feels necessary. Pool size decides whether two stills in the same
pack are *the same picture*.

With twelve synthetic seeds, a 2,227-photo pack contained 268 pairs that were
perceptually identical and 7,091 a dedupe engine would flag — at 186 crops per
seed, overlapping windows are arithmetic rather than bad luck. With 212
harvested seeds, plus per-file variation and a perceptual-hash gate, the same
measurement finds none.

**200 or so is a good default.** If the pool is too small to satisfy the
uniqueness gate, the build says so and stands down rather than spinning.

### Are the generated photos and videos actually unique?

Yes, in both senses that matter, and they are different senses.

*Byte* uniqueness was never in question — different EXIF alone guarantees
distinct checksums. *Perceptual* uniqueness is the harder promise, and it is
kept by three things that compound: the size of the seed pool, per-file
variation (a random crop window and scale, a horizontal flip, a slight
rotation, and jitter on colour, brightness and contrast), and a gate that
perceptually hashes each still as it is rendered and redraws it if it lands too
close to one already in the pack.

The exception is deliberate: `--edge-cases` plants one byte-identical duplicate
and a burst of five near-identical frames, precisely so a dedupe engine has
something to find. Those never enter the gate.

### Will "25GB" really give me 25 GB?

Yes — delta zero, not an estimate. JPEG size is content-dependent, so nothing
is estimated twice: the planner prices each file at its measured bytes, and
closes the last of the gap with video duration and then a JPEG comment segment.
Every build prints the delta; it is `+0 bytes`.

This is the reason the tool exists. Storage and quota bugs live at exact
boundaries, and "about 25 GB" does not put a device at one.

### Can I rebuild the exact same pack later?

Yes. Same `--job` and `--seed` reproduce the pack byte for byte, video
included. A failing test stays re-runnable, and a pruned pack can be brought
back from nothing but its job row.

One exception: video seeds. `harvest --video-seeds` is **off by default**
because cutting a clip out of real footage means seeking into it, and that seek
is not frame-reproducible. Synthetic video always is.

### My phone can't reach the control plane.

Almost always one of three things:

1. **You gave it `localhost`.** The phone needs your machine's LAN address —
   `ipconfig getifaddr en0` on macOS.
2. **Firewall.** The server binds `0.0.0.0:8722`; something local may be
   blocking inbound connections on that port.
3. **Different networks.** A phone on cellular, or on a guest VLAN, is not on
   your LAN. Corporate Wi-Fi with client isolation will also do this.

For an Android *emulator* rather than a handset, `adb reverse tcp:8722
tcp:8722` makes the host's port reachable at `http://localhost:8722` inside the
emulator.

### The app says the pack was pruned.

It was — its media has been reclaimed to free disk. The job row survives, so
press **resume** on that job in the browser (or `POST /api/jobs/<id>/resume`)
and it rebuilds byte for byte, then pair again with the same code.

Every layer knows the difference: the server answers `410 Gone`, and both
loaders report a pruned pack rather than a transfer failure, including mid-fill.

### Can I run two builds at once?

You can, but nothing stops them competing for the same cores, and there is no
queue. For one operator this is fine. Two large builds at once will each take
roughly twice as long.

### Do I need the mobile apps to use this?

No. `tdg load --target simulator|emulator|folder` covers CI and desktop testing
entirely, and needs no app on anything. The apps exist for **physical
handsets**, which is the one thing no simulator and no browser can do.

### Why is my video build so slow?

Because a 4K clip spends almost all its time in the single-threaded frame
source, not the encoder. Lower `--photo-fraction`'s video share, or accept it:
video runs at ~4.6 MB/s against ~33 MB/s for photos. `--video-jobs` defaults
to 4; above that it got *slower*, not faster.

If you just want bulk quickly, `--photo-fraction 1.0` roughly doubles
throughput.

### HEIC fails with an install message.

HEIC is HEVC inside a HEIF container, and the generator does not carry an
encoder for it. On macOS it uses the built-in `sips` automatically. Elsewhere:
`pip install pillow-heif`. Everything else in the generator stays
dependency-free.

### Is this safe to put on a shared network?

No. The pairing code stops a browser stumbling into someone else's 64 GB pack;
it is not authentication. There is no TLS, and anyone who can reach the port can
create a job. Run it on a trusted lab LAN and nowhere else.

### Where do I look when something is wrong?

| | |
|---|---|
| A build's own log | streams in the browser; printed by the CLI |
| What landed on a device | `tdg verify --pack ./pack --target <t>` |
| What has been loaded where | `tdg receipts` |
| What a pack contains | `tdg inspect ./pack` |
| Server request log | `serve.py --verbose` |
| Known behaviour on real devices | `docs/conformance/` |

Each package also has its own README with the design reasoning and a **Known
limits** section that is kept honest: `packages/generator`, `packages/web`,
`clients/android`, `clients/ios`.
