# Conformance — Galaxy S24 (physical), receipt custody after uninstall

Whether a device stays cleanable when the app that filled it is deleted. This
is the promise the whole design rests on — *a test you cannot undo is a test you
run once* — and until this run nothing had ever tested it, because it cannot be
tested without a real device and a real uninstall.

- job `0bfad7` · 169 files (169 photos) · 300.0 MB
- device `SM-S921U1`, Android 16, One UI
- control plane at `192.168.86.55:8722`
- verified 2026-09-06

| Step | Result | |
|---|---|---|
| fill | 169 / 169 into the gallery | yes |
| deposit | 169 entries reached the control plane, keyed on `ANDROID_ID` | yes |
| job list | reported `on 1 device` | yes |
| **uninstall** | app and its receipt destroyed; **169 assets remained** | the hole |
| ownership after uninstall | `owner_package_name` = **NULL** on all 169 rows | — |
| reinstall | fresh app, no local receipt ("Nothing yet.") | yes |
| **recovery** | pairing restored all 169 entries from the control plane | yes |
| deletion | system consent dialog, "Allow TDG Loader to delete 169 photos?" | yes |
| assets after wipe | 0 in MediaStore | yes |
| album folder | removed | yes |
| local receipt | cleared | yes |
| server copy | withdrawn, `receipt_devices` back to 0 | yes |

**PASSED**, on the third attempt. The first two failed, and what they failed at
is the useful part of this record.

## What the first attempt found

Recovery worked immediately: a freshly installed app with no receipt of its own
pulled all 169 entries back and offered to remove them. Then the wipe reported
**"Removed 0 of 169"** and every asset was still there.

`owner_package_name` was NULL on all 169 rows. **Uninstalling an app clears
MediaStore's record of what it owned**, so the reinstalled app — the exact case
recovery exists to serve — is no longer permitted to delete what it wrote. Worse,
MediaStore does not say so: `ContentResolver.delete` returns 0 rather than
raising, so a wipe that removed nothing looked like a wipe that had nothing to
remove.

Recovering a receipt restores the *identity* of the assets. It does not restore
the *authority* to delete them. Those are separate problems and only the first
had been solved.

## What the second attempt found

The fix was `MediaStore.createDeleteRequest`, which shows one system dialog and
deletes regardless of owner. It did not fire, and the wipe cleared the receipt
while leaving the assets — strictly worse than before.

The detection was wrong. It looked for surviving rows with a `query`, and **an
app that may not delete unowned media may not read it either**: the query came
back empty, the wipe concluded everything was gone, and it discarded the only
record of what was still there. The delete's own return value is the only honest
signal, so the code now collects what `delete` refused rather than asking
MediaStore what survived.

## What the third attempt found

The consent dialog appeared and removed all 169. But the server-side copy was
still there afterwards, still claiming the device held 169 assets — which caused
the *next* pairing to "recover" a receipt for assets that no longer existed and
report "Already loaded" against an empty gallery.

The withdrawal runs from an activity-result callback, on the main thread, where
`HttpURLConnection` throws `NetworkOnMainThreadException` — and `Custody`
catches everything by design, so it failed in complete silence. Moved to
`Dispatchers.IO`.

## What this run does not tell you

- **The recovered wipe is not scriptable.** The system delete dialog cannot be
  automated, so a device that has lost its app needs one human tap to be
  cleaned. The everyday wipe, where the app still owns its media, stays
  promptless.
- **iOS does not share this failure**, and that is now measured rather than
  assumed: the same scenario on an iPhone 15 Pro passed first time, because
  Photos has no per-app asset ownership and wipe already holds full library
  access. See [the iOS record](iphone-15-pro-receipt-custody.md).
- **One device, one pack.** Recovery across two handsets sharing a pack, and
  recovery of a pack whose job row was deleted rather than pruned, are both
  covered by tests and neither has been run on hardware.
