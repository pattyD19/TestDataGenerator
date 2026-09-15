# Conformance — iPhone 15 Pro (physical), iOS 27.0

Re-certification after the OS upgrade. macOS 27, Xcode 27, the iOS 27.0 SDK —
which is the only iOS SDK Xcode 27 ships, so there is no falling back to 26.

- job `b2f8fe` · 61 files (60 photos, 1 video) · 150.0 MB
- device `iPhone16,1`, iOS 27.0, build 24A437, 128 GB
- pack: JPEG + HEIC stills, one HEVC clip, the full edge-case set
- transfer over Wi-Fi from the control plane at `192.168.86.55:8722`
- verified 2026-09-15

| Check | Result | |
|---|---|---|
| build against the iOS 27 SDK | succeeded; 2 warnings, both pre-existing and benign | yes |
| accepted by Photos | **60 / 60** | yes |
| refused | 1 — the zero-byte file, which has no resource to validate | expected |
| formats accepted | 33 JPEG, 25 HEIC, 1 HEVC, 1 PNG | yes |
| deposit | 60 entries reached the control plane in under 8 s | yes |
| receipt integrity | phone's copy **identical to the server's, key for key** | yes |
| duplicate asset identifiers | 0 | yes |
| device identity | **the same keychain UUID as 2026-09-06** | yes |
| wipe | all 60 removed, receipt cleared | yes |
| server copy | withdrawn, `receipt_devices` back to 0 | yes |

**PASSED.** Nothing in the app needed changing for iOS 27.

## The result worth keeping

The device id came back as `B3B016D6-F6CF-44DE-AA80-5789085A3792` — **the same
value recorded on 2026-09-06**. Between those two runs that keychain item
survived the app being deleted, reinstalled, an iOS 26 → 27 major upgrade, and a
rebuild under a provisioning profile Xcode reissued from scratch.

Choosing the keychain over `identifierForVendor` was reasoning at the time:
`identifierForVendor` resets when the last app from a vendor is deleted, which
is the precise event receipt recovery has to survive. It is now measured against
the harder event as well. A device that was filled a month and an OS version ago
can still be identified, and therefore still cleaned.

## Two bugs found in the same certification, neither iOS 27's

Both surfaced on the [iOS 27 simulator run](iphone-17-simulator-ios-27.md) and
are described there in full:

- **`tdg verify` mispaired capture times**, reporting "30/60, worst delta 2.1
  years" on a fill where nothing was wrong. It paired two sorted lists of
  instants positionally, so one legitimately absent asset shifted everything
  after it. Read naively, that looked exactly like an iOS 27 regression.
- **`Custody.deviceId()` was not stable** where the keychain is unavailable. It
  never bit on hardware — this device is the proof the keychain path works — but
  an unsigned simulator build silently produced a new UUID per call.

## What this run does not tell you

- **It is 150 MB, not a device fill.** Volume on iOS was measured separately at
  900 assets, on iOS 26.6.1, and has not been repeated on 27.
- **Recovery after deleting the app was not re-run here.** It passed on
  2026-09-06 and the device id proves the mechanism it depends on still holds,
  but the full delete-and-recover cycle was not repeated on 27.
- **Trust reset again.** Xcode 27 issued a new provisioning profile, so the
  device refused to launch the build until *Settings → General → VPN & Device
  Management → Trust* was tapped. Third occurrence in this project, each from a
  different cause. It is a development-signing artifact, not a defect, and it is
  the friction that makes TestFlight or enterprise distribution the real
  precondition for anyone but the developer running this.
