# Conformance — iPhone 17 simulator · iOS 27.0

- job `b2f8fe` · 60 files (60 photos, 1 videos) · 150.0 MB
- verified 2026-09-15T22:29:52+00:00
- assets matched by instant

| Check | Result | |
|---|---|---|
| assets indexed | 60 / 60 | yes |
| capture time preserved | 60 / 60 dated, worst delta 0.0s | yes |
| refused, as expected | 1 zero-byte asset(s) — iOS has no resource to validate | expected |
| album grouping | TDG b2f8fe | yes |
| fill duration | 0.0s (—) | — |

**PASSED**

Run through the **loader app**, not `simctl addmedia` — the app was installed on
the simulator and driven by launch arguments, so this exercises
`PHAssetCreationRequest`, the receipt, and receipt custody on iOS 27 rather than
just the pack. Pack: JPEG + HEIC stills, one HEVC clip, and the full edge-case
set. Accepted by kind: 25 HEIC, 33 JPEG, 1 HEVC, 1 PNG.

## What the upgrade changed

Nothing in the app. It compiled against the iOS 27 SDK with **zero errors and
zero warnings** — no deprecation, nothing removed from under it — and the fill,
the album, the capture times and the wipe all behaved as they did on 26.

One toolchain difference is worth recording: on iOS 26,
`simctl privacy grant photos` did **not** pre-authorise full library access and
the prompt appeared anyway. On iOS 27 it does. The delete confirmation naming
the asset count still appears and still cannot be scripted, so an unattended
*wipe* remains impossible — but one of the two taps is gone.

## Two bugs this run found, neither of them iOS 27's

**`tdg verify` mispaired capture times.** It compares two sorted lists of
instants, and it paired them positionally. One legitimately absent asset — the
zero-byte file iOS is right to refuse — shifted every instant after it against
its neighbour and reported "capture time preserved 30/60, worst delta 2.1
years" on a fill where nothing was wrong. Matching each found instant to the
nearest expected one not already taken gives 60/60 at 0.0 s. The bug needed
three things at once to show up — an iOS target, an edge-case pack, and a
refusal — which is why it survived this long.

**The iOS device id was not stable.** `Custody.deviceId()` stores a UUID in the
keychain and ignored whether the write succeeded. An unsigned simulator build
has no keychain-access-group entitlement, so the write failed and every call
returned a fresh UUID: in one run the deposit, the recovery and the withdrawal
each went out under a different device. Nothing errored — the server simply had
no row matching the id being asked about, so the receipt was never withdrawn and
the next pairing "recovered" a stale copy. The write is now checked and read
back, with a UserDefaults fallback that at least keeps one id per install.

That fallback does not survive app deletion, so **receipt recovery after
deleting the app cannot be tested on a simulator** — it needs the signed device
build, where the keychain works. That path is covered by
[the iPhone 15 Pro record](iphone-15-pro-receipt-custody.md).

## What this run does not tell you

- **It is a simulator.** The physical iPhone 15 Pro has not been re-run on
  iOS 27; everything here is the simulator's Photos implementation.
- **150 MB, not a device fill.** Volume on iOS was measured separately at 900
  assets, on iOS 26.6.1.
- **The zero-byte refusal is now subtracted from the expected count** on iOS
  targets rather than counted as a loss. Without that, any pack built with
  `--edge-cases` fails on iOS permanently, which is a gate that cannot pass and
  therefore says nothing. Android still expects the file, because MediaStore
  accepts it.
