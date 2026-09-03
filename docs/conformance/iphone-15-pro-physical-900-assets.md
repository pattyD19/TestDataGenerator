# Conformance — iPhone 15 Pro (physical), iOS 26.6.1, 900 assets

The volume run. Every earlier iOS fill — simulator and hardware alike — was
under 100 files, so the one thing §0 of the plan actually worried about, Photos
under load, had never been measured on iOS.

- job `9e7f2e` · 900 files (900 photos, 0 videos) · 2.00 GiB
- device `iPhone16,1`, 128 GB, iOS 26.6.1
- pack: JPEG stills only, `iphone-15-pro` profile, no edge cases
- transfer over Wi-Fi from the control plane at `192.168.86.55:8722`
- verified 2026-09-03

| Check | Result | |
|---|---|---|
| pairing | six digits resolved the pack over the LAN | yes |
| accepted by Photos | 900 / 900 | yes |
| missing from the receipt | 0 | yes |
| unexpected extras | 0 | yes |
| duplicate asset identifiers | 0 | yes |
| elapsed | ~110 s from launch to the last asset recorded | — |
| throughput | ~19 MB/s over Wi-Fi | — |
| capture range | 2022-01-03 .. 2026-08-24, preserved from the manifest | yes |
| wipe | all 900 removed, receipt cleared | yes |

**PASSED.** No refusals — this pack carries no edge cases, so there was nothing
Photos should have rejected.

**And it came back out.** Deletion on a physical iPhone was the least-proven
path in the whole system, and until this run it had only been done at 72
assets. `deleteAssets` took all 900 in one confirmation, and the app then
cleared `receipt-9e7f2e.json` — the ordering that matters, since a receipt
outliving the assets it names would make the next wipe lie. Verified from the
Mac: the app's Application Support directory is empty.

## What the run was for

The plan's Phase 0 spike named two risks and asked for **500 assets** against
each. The Android half was retired long ago and then some: 3,466 assets on a
physical Galaxy S24. The iOS half was not. `PHAssetCreationRequest` and
`creationDate` were proven correct on hardware in the
[first device run](iphone-15-pro-physical-ios-26.6.1.md), but that pack was 72
files, and the simulator runs were 20 and 23. Nothing had ever asked Photos to
take a real library in one sitting.

900 is 1.8× what the spike asked for, and it went in clean.

**Batching held.** Assets are created 100 per `performChanges` transaction, a
design decision made on the reasoning that one transaction for 26,000 assets
stalls the library while one per asset spends its life in round trips. Nine
transactions, no stall, no partial batch, no retry path taken.

**Throughput went up, not down.** ~19 MB/s against the 11.9 MB/s the Galaxy S24
managed over the same Wi-Fi, and against ~4.2 MB/s for `adb push` to an
emulator. Photos did not become the bottleneck at this size; the network still
is.

**Nothing needed a tap.** Add-only photo permission was already granted from the
2026-08-30 run and persisted, so the whole fill ran unattended from a single
`devicectl` launch:

```bash
xcrun devicectl device process launch --device <udid> --terminate-existing \
      com.tdg.loader -- -host http://<lan-ip>:8722 -code <six digits> -autostart 1
```

The `--` matters: without it `devicectl` reads `-host` as its own option.

## What this run does not tell you

- **The wipe needed hands.** Removal escalates to full photo access, and iOS
  then shows a system confirmation naming the asset count. Neither prompt can be
  scripted, so unattended *fills* are possible and unattended *wipes* are not.
- **It is stills only.** 900 JPEGs is not 900 mixed assets; video goes through
  the same `PHAssetCreationRequest` path but is not exercised here at volume.
- **It is 2 GB, not 64 GB.** A full-device fill is 30× this and would run for
  the better part of an hour, which is the multi-hour battery question the
  Galaxy S24 answered for Android and nothing has answered for iOS.
- **It is the loader's account of itself.** As with the first device run,
  `tdg verify` cannot reach a physical iPhone, so the numbers come from the
  receipt the app wrote — every `PHAsset` localIdentifier it created — pulled
  off the device with `devicectl`. That is a real measurement from real
  hardware, but it is not the same as asking Photos directly.
