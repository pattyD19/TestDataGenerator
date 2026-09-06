# Conformance — iPhone 15 Pro (physical), receipt custody after deletion

The iOS half of the question the [Galaxy S24
run](galaxy-s24-receipt-custody.md) asked: does a device stay cleanable when
the app that filled it is deleted?

- job `0bfad7` · 169 files (169 photos) · 300.0 MB
- device `iPhone16,1`, iOS 26.6.1
- control plane at `192.168.86.55:8722`
- verified 2026-09-06

| Step | Result | |
|---|---|---|
| fill | 169 / 169 into Photos, unattended from launch arguments | yes |
| deposit | 169 entries reached the control plane in under 20 s | yes |
| device id | keychain UUID `B3B016D6-…`, platform `ios` | — |
| **app deleted** | container gone, receipt destroyed with it | the hole |
| reinstall | fresh app, zero receipt files | yes |
| **keychain survived** | the app asked for the *same* device id and matched | yes |
| **recovery** | 169 entries restored, **identical to the server key for key** | yes |
| wipe | full-access prompt, then a system confirmation naming the count | yes |
| receipt on device | cleared | yes |
| server copy | withdrawn, `receipt_devices` back to 0 | yes |

**PASSED, first attempt.** No code changes were needed — which is the
interesting part, because the Android run needed three.

## Why iOS was the easier half

Android's failure was that **uninstalling an app clears MediaStore's record of
what it owned**, so a reinstalled app could name its assets and still not be
allowed to delete them. Photos has no equivalent: there is no per-app ownership
of a `PHAsset`, and wipe already escalates to full library access, which permits
deleting any asset by `localIdentifier`. So the identity that recovery restores
is sufficient on its own here. That had been the reasoning; this run is the
measurement.

## The proof that it was recovery and not a refill

A receipt file appeared within six seconds of launch, which is already too fast
for 300 MB. The decisive check is the content: the receipt on the phone matched
the server's copy **key for key, including every `PHAsset` localIdentifier**. A
refill creates new assets with new identifiers, so identical identifiers can
only mean the original assets and the recovered record of them.

## `identifierForVendor` would have failed here

The device id is a UUID kept in the **keychain**, and this run is why. iOS
resets `identifierForVendor` when the last app from a vendor is deleted — which
is precisely the event recovery has to survive, so the obvious identifier is the
one that cannot work. Keychain items outlive an app deletion, and the recovery
above is the evidence: the reinstalled app asked the control plane for the same
`B3B016D6-…` it had deposited under, and matched.

## What this run does not tell you

- **Deleting the app resets developer trust.** The reinstalled build would not
  launch until *Settings → General → VPN & Device Management → Trust* was tapped
  again. That is a development-signing artifact rather than a product defect,
  but it lands directly on the recovery path, since recovery begins with a
  reinstall. TestFlight or enterprise distribution would not have it.
- **The wipe needs two taps** — full photo access, then the system's own
  confirmation of the count. Neither can be scripted, so unattended fills are
  possible on iOS and unattended wipes are not.
- **One device, one pack, stills only.** Recovery across two devices sharing a
  pack, and recovery of a pack containing video, are covered by tests and have
  not been run on hardware.
