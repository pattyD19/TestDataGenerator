# Conformance — iPhone 15 Pro (physical), iOS 27.0, receipt custody

The delete-and-recover cycle, repeated after the OS upgrade. The
[first custody run](iphone-15-pro-receipt-custody.md) was on iOS 26.6.1.

- job `b2f8fe` · 61 files (60 photos, 1 video) · 150.0 MB
- device `iPhone16,1`, iOS 27.0, build 24A437
- control plane at `192.168.86.55:8722`
- verified 2026-09-15

| Step | Result | |
|---|---|---|
| fill | 60 / 60 accepted (the zero-byte file refused, as it must be) | yes |
| deposit | 60 entries, under device `B3B016D6-…` | yes |
| **app deleted** | container gone, receipt destroyed; **60 assets left in Photos** | the hole |
| reinstall | fresh app, zero receipt files | yes |
| **keychain id** | reinstalled app asked for **the same `B3B016D6-…`** | yes |
| **recovery** | 60 entries restored, **identical to the server key for key** | yes |
| no refill | exactly one file fetched — the zero-byte edge case, correctly retried | yes |
| wipe | all 60 removed **using recovered identifiers** | yes |
| receipt on device | cleared | yes |
| server copy | withdrawn, `receipt_devices` back to 0 | yes |

**PASSED**, first attempt, with no code changes for iOS 27.

## The identifier is the whole thing

`B3B016D6-F6CF-44DE-AA80-5789085A3792` was first recorded on 2026-09-06. It has
now survived, in order: an app deletion, a reinstall, a second app deletion, the
iOS 26 → 27 major upgrade, a provisioning profile Xcode reissued from scratch,
and a third deletion here.

`identifierForVendor` resets when the last app from a vendor is deleted — the
precise event recovery exists to survive — so the obvious identifier is the one
that cannot work. That was reasoning when the keychain was chosen. It is now
measured against every event that plausibly threatens it.

## Recovery, not a refill

Distinguishing the two matters, because a refill would also end with 60 assets
and a full receipt. Three things separate them here:

- the restored receipt was **identical to the control plane's copy key for
  key**, including every `PHAsset` localIdentifier. A refill creates new assets
  with new identifiers.
- the control plane served **one file** after the reinstall, not sixty.
- that one file was `TDG_b2f8fe_00010.jpg`, the **zero-byte** edge case. It is
  absent from the receipt because iOS refuses it, so it correctly showed as
  outstanding and was re-attempted — and refused again. The loader did not
  quietly skip the one asset it had no record of.

The wipe then deleted all 60 using identifiers the app had never itself
recorded.

## What this run does not tell you

- **150 MB, 60 assets.** Recovery at volume — 900 assets — has not been run.
- **One device, one pack.** Two handsets sharing a pack, and recovery of a pack
  whose job row was deleted rather than pruned, remain covered by tests only.
- **Trust reset again, and it is inherent here.** Deleting the app drops the
  trusted-developer entry, so the reinstall would not launch until *Settings →
  General → VPN & Device Management → Trust* was tapped. Recovery *begins* with
  a reinstall, so on a development build this cost is unavoidable — the fourth
  trust reset in this project and the only one intrinsic to the feature being
  tested. TestFlight or enterprise distribution removes it.
