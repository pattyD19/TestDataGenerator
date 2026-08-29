# TestDataGenerator (TDG)

Fills mobile devices with N GB of open-license photos and videos, in the native
camera roll, with realistic EXIF — for testing gallery-scanning apps.

See [PLAN.md](./PLAN.md) for the full build plan.

## Layout

- `packages/generator` — seed harvester, amplifier, size planner, manifest builder
- `packages/web` — browser control plane (Next.js)
- `packages/cli` — `tdg build` / `tdg load` / `tdg wipe`
- `clients/android` — Kotlin loader (MediaStore)
- `clients/ios` — SwiftUI loader (PHAssetCreationRequest)
- `seed-pool` — cached CC0/CC-BY source media + LICENSES.csv (gitignored, rebuilt by harvester)
- `docs` — manifest schema, device personas, licensing notes
