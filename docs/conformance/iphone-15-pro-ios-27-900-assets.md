# Conformance — iPhone 15 Pro (physical), iOS 27.0, 900 assets

Volume on iOS, repeated after the OS upgrade. The
[first 900-asset run](iphone-15-pro-physical-900-assets.md) was on iOS 26.6.1;
this is the same pack on the same phone on 27.

- job `9e7f2e` · 900 files (900 photos) · 2.00 GiB
- device `iPhone16,1`, iOS 27.0, build 24A437
- transfer over Wi-Fi from the control plane at `192.168.86.55:8722`
- verified 2026-09-15

| Check | Result | |
|---|---|---|
| accepted by Photos | **900 / 900** | yes |
| missing from the receipt | 0 | yes |
| unexpected extras | 0 | yes |
| duplicate asset identifiers | 0 | yes |
| receipt vs the server's copy | **identical, key for key** | yes |
| transfer | **83 s for 2.00 GiB — ~25.9 MB/s** | — |
| wipe | all 900 removed, receipt cleared, server copy withdrawn | yes |

**PASSED.**

## Same pack, not an equivalent one

The pack was **rebuilt from the pruned job** rather than generated fresh: its
media had been reclaimed weeks earlier, and `resume` reproduced it byte for byte
from the job id and seed still on the row. That is the reproducibility promise
being *used* rather than tested, and it is what makes the comparison below worth
anything — same 900 files, same device, same network, different OS and SDK.

| | iOS 26.6.1 | iOS 27.0 |
|---|---|---|
| accepted | 900 / 900 | 900 / 900 |
| transfer | ~110 s | **83 s** |
| throughput | ~19 MB/s | **~25.9 MB/s** |

About 36% faster. Timings come from the control plane's own request log — first
byte requested at 18:41:38, last file at 18:43:01 — rather than from the app's
account of itself.

**Do not over-read it.** One run on each side, and Wi-Fi conditions were not
controlled. What it does support is the weaker and more useful claim: iOS 27 did
not make a volume fill slower, and Photos was not the bottleneck at this size on
either version.

## What this run does not tell you

- **Stills only.** 900 JPEGs is not 900 mixed assets; video goes through the
  same `PHAssetCreationRequest` path and is not exercised here at volume.
- **2 GB, not 64 GB.** A full-device fill is 30× this and would run for the
  better part of an hour — the multi-hour question the Galaxy S24 answered for
  Android and nothing has answered for iOS.
- **Batching was not observed, only its outcome.** Assets are created 100 per
  `performChanges` by design, so nine transactions; whether any batch fell back
  to the per-asset retry was not instrumented. What is measured is that all 900
  arrived, once each, with no duplicate identifiers.
- **The loader's account of itself, cross-checked.** `tdg verify` cannot reach a
  physical iPhone, so the numbers come from the receipt the app wrote — but that
  receipt was compared against both the manifest and the control plane's
  independent copy, and all three agree.
