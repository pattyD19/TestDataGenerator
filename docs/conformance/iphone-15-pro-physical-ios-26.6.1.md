# Conformance — iPhone 15 Pro (physical), iOS 26.6.1

The first run on real hardware. Everything before this was an emulator or a
simulator.

- job `1e4283` · 72 files · 300.0 MB
- build `23G83`, device `iPhone16,1`
- pack: HEIC + JPEG stills, HEVC video, and the full edge-case set
- transfer over Wi-Fi from the control plane at `192.168.86.55:8722`

| Check | Result | |
|---|---|---|
| pairing | six digits resolved the pack over the LAN | yes |
| transfer | 72/72 files fetched, every response 200 | yes |
| accepted by Photos | 71 / 72 | yes |
| refused | 1 — zero bytes | expected |
| formats accepted | {'mp4/hevc': 4, 'jpeg': 43, 'png': 1, 'heic': 23} | yes |
| edge cases accepted | 9 / 10 | yes |
| resume | second run fetched only the 14 outstanding files, not all 72 | yes |

**PASSED**, with one correct refusal: a zero-byte file has no resource to
validate, so `PHAssetCreationRequest` cannot accept it. That is the platform
behaving properly, not a defect — and it is exactly what the edge-case pack
exists to surface.

## What this run found that no simulator did

Two genuine bugs in the iOS loader, both invisible until real hardware met a
real Photos database.

**One invalid asset failed the entire import.** All 72 assets went into a single
`performChanges` transaction, so the zero-byte file took the other 71 with it:
`PHPhotosErrorInvalidResource` (3302), 300 MB of completed transfer discarded,
nothing recorded. The loader now retries a failed batch one asset at a time, so
a refusal costs one asset instead of the run.

**`shouldMoveFile` made the retry lie.** With the per-asset retry in place the
result was 58 accepted and 14 refused — and the 14 were a *contiguous* run,
indices 00000–00013, which is the tell. Photos had already **moved** each staged
file as it processed it, so when the transaction aborted at index 13 the retry
found files 0–12 gone and reported perfectly good assets as invalid. Copying
instead of moving costs one pass over the bytes and makes the retry honest:
71/72 on the next run.

## Verifying a physical iPhone

`tdg verify` cannot reach a physical iPhone — the Photos database is not
readable from outside the app. The receipt is, though:

```bash
xcrun devicectl device copy from --device <name>   --domain-type appDataContainer --domain-identifier com.tdg.loader --user mobile   --source "Library/Application Support/receipt-<job>.json" --destination ./receipt.json
```

That gives the app's own record of every `PHAsset` localIdentifier it created,
which is what the numbers above were computed from. It is not the same as asking
Photos directly — it is the loader's account of itself — but it is a real
measurement from a real device, and it is how this report was produced.
