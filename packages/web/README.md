# tdg control plane

The browser front end for the generator: pick a size and a device profile, watch
the pack build, hand a phone the address it can pull from.

Phase 3 of [the plan](../../PLAN.md).

```bash
python3 packages/web/serve.py --port 8722
```

Then open <http://localhost:8722>. Binds `0.0.0.0` by default so devices on the
same network can reach it — the plan's whole argument for running the generator
on the LAN rather than pulling 64 GB from the cloud for every test run.

## Why Python and not Next.js

The plan named Next.js + Tailwind. This is stdlib Python and hand-written
JavaScript instead, decided 2026-08-29:

- The generator **is** Python, so the server imports it directly. Progress comes
  from the build's own checkpoint rather than a network hop or a parsed CLI.
- No `node_modules`, no build step, no second runtime in the Docker image. A lab
  machine runs one command.
- It matches the rest of the repo, which hand-rolls its EXIF writer rather than
  taking a dependency.

The cost is a hand-written UI. That is a fair trade for a form, a progress
stream, a job list and a pairing code; it would be a bad trade if this grew into
a many-screen product.

## How a build runs

Each build is a `tdg build` **subprocess**, not an in-process call:

- **Cancel is a real kill.** A CPU-bound encode loop cannot be interrupted
  cooperatively from another Python thread.
- **A crash stays contained.** An OOM-killed build does not take the server with it.
- **Resume comes free.** The generator already checkpoints, so cancel-then-resume
  is the same code path a `^C`-then-rerun takes on the CLI. Cancelling at 45%
  and resuming lands the same exact-size pack.

Progress is read from that checkpoint rather than scraped from stdout, so it is
byte-accurate and stays correct across a resume where a line count would not.

## The jobs list

The four most recent jobs are full cards — progress, log, pairing code, the
actions. Everything older folds into **one line each** behind a disclosure that
remembers whether it was open, because a job list is append-only and a month of
builds otherwise pushes the thing you are actually watching off the screen. A
folded row still carries its status, size and pairing code; opening the card is
one click away.

A job that changes status while the fold is closed still forces a repaint — the
list is polled, and a signature over the statuses is what decides whether the
cached survey of reclaimable space is still good.

## API

Two halves. The browser half:

| | |
|---|---|
| `GET /api/options` | presets, device profiles, the LAN URL |
| `GET /api/jobs` | every job, newest first |
| `POST /api/jobs` | create and start one |
| `GET /api/jobs/<id>` | one job |
| `GET /api/jobs/<id>/events` | SSE: progress, log lines, completion |
| `POST /api/jobs/<id>/cancel` | stop, keeping what was built |
| `POST /api/jobs/<id>/resume` | continue a cancelled or failed job — or rebuild a pruned one |
| `GET /api/prune` | what could be reclaimed and how much it would free |
| `POST /api/prune` | prune every eligible job (`{"drop_rows":…, "force":…}`) |
| `POST /api/jobs/<id>/prune` | delete this pack, keep the job (`?force=1` for a partial build) |
| `POST /api/receipts?token=` | a loader depositing what it wrote onto a device |
| `GET /api/receipts?token=` | the devices carrying this pack |
| `GET /api/receipts?token=&device=` | one device's full receipt, for recovery |
| `DELETE /api/receipts?token=&device=` | that device has wiped |
| `DELETE /api/jobs/<id>` | delete the pack *and* the job row |

And the pack half, which is what a loader consumes:

| | |
|---|---|
| `GET /api/jobs/<id>/manifest?token=…` | the manifest, with a fetchable `url` on every item |
| `GET /api/jobs/<id>/files/<name>?token=…` | the bytes, with `Range` support |

The manifest is rewritten on the way out so each item carries its own URL: a
loader needs the manifest and nothing else, and never has to know how this
server lays out paths.

## Reclaiming disk

Packs are the only thing here that grows without bound: a 64 GB job leaves
64 GB behind, and the row describing it is a few hundred bytes. So the two are
removed separately.

**Prune** deletes the media and keeps the row. The job stays in the list with
what was asked for and what it produced, but its status becomes `pruned` — it
stops pairing and its manifest answers `410 Gone`, rather than letting a phone
discover the absence halfway through a transfer. **Delete** removes both.

Neither touches receipts. The pack is the *source*; the receipt written at load
time lives on the device (or under `~/.tdg/receipts`), never inside the pack, so
**a device stays fully wipeable long after the pack it came from is gone.** That
is the property that makes reclaiming space safe at all.

A pruned job can be rebuilt: the job id and seed are on the row and the
generator is deterministic, so `resume` reproduces the same pack byte for byte.
Pruning costs build time, never information.

In the UI, each job carries a **prune** link and the Jobs panel offers
**Reclaim *N*** when there is anything to get back. The same thing without a
browser:

```bash
python3 -m tdgweb.prune --packs ./packs --dry-run   # what would go
python3 -m tdgweb.prune --packs ./packs             # prune every eligible job
python3 -m tdgweb.prune --packs ./packs --job <id> --drop-rows
```

Two guards, because this deletes recursively:

- a **partial build is never pruned by accident.** A pack still holding a
  checkpoint is skipped by the bulk prune and refused with `409` individually;
  discarding a resume takes `--force` / `?force=1`.
- a **`pack_dir` outside the packs root is refused** with `400`. That path comes
  out of the database, so it is checked against real paths — a row written when
  the server ran with a different `--packs`, or edited by hand, cannot make
  `rmtree` run somewhere else on the disk.

`Range` matters more than it looks. A phone pulling a 4 GB clip over flaky Wi-Fi
resumes rather than restarts, and the files are streamed in 64 KB chunks so a
4 GB asset never becomes 4 GB of resident memory.

## Receipt custody

The device holds the authoritative receipt — it is what a resumed fill reads and
what the wipe deletes from. But it lives in app storage, so **uninstalling the
loader destroys it while leaving every asset in the gallery.** The bytes stay;
the only record of *which* bytes they are goes with the app, and nothing can
then remove them precisely. "A test you cannot undo is a test you run once" only
holds if the receipt outlives the app that wrote it.

So a loader deposits a copy here when a fill finishes, and a freshly installed
app asks for it back the moment it pairs. Three properties make it work:

- **Recovery survives a prune.** The receipt routes resolve the pairing code
  with `by_token_any`, not `by_token`, so they answer for a job whose pack is
  long gone. Pairing still refuses a pruned pack — but that is precisely the
  state where a phone is full and has nothing to explain itself with, so it is
  the case recovery has to cover.
- **The device stays the authority.** `adopt` on both clients refuses to
  overwrite a receipt that already has entries. A copy can only ever be restored
  onto an app that has none, so recovery cannot lose an identifier.
- **Deleting a job is refused while a device carries it.** `DELETE
  /api/jobs/<id>` drops the stored receipts with the row, which is the one
  operation that can strand assets. It answers `409` naming the number of
  devices, and points at prune — which reclaims the same disk and keeps them.

A receipt is a few hundred kilobytes at 3,466 assets, so the deposit is a single
POST at the end of a fill rather than a write per file, and the list endpoint
omits the entries.

**Restoring the receipt is only half of it.** It gives a device back the
*identity* of what it holds, not the *authority* to remove it — Android clears
MediaStore ownership when an app is uninstalled, so the loader has to ask the
system's permission before it can delete media it recovered. That is a client
concern, not a server one, but it is the reason recovery is not simply a
download. See [the Android README](../../clients/android/README.md) and
[the conformance record](../../docs/conformance/galaxy-s24-receipt-custody.md).

Every client call is **best effort**: a control plane that has moved or gone
away must never fail a fill or block a wipe, because those depend on the
device's own receipt. This is a second chance, not a dependency.

## The pairing code

Every job gets a six-digit code. It gates the manifest and the pack bytes.

This is a **speed bump, not a security boundary** — enough that a browser on the
same network cannot stumble into someone else's 64 GB pack. The control plane is
meant for a trusted lab LAN. Do not put it on the public internet.

QR pairing from the plan is deliberately deferred: the apps that would scan a
code are Phases 4 and 5, and a QR encoder with nothing to scan it is speculative
work. The six-digit code is the half that is useful today.

## Tests

```bash
python3 packages/web/tests/test_server.py
```

A real server on a real socket running real builds — nothing about the generator
is mocked, because the interesting failures are at the seams. It covers the
manifest a loader actually fetches, `Range` semantics including suffix ranges and
416s, token refusal, path traversal, the eight ways a bad request should be
rejected *before* a two-hour build starts, and a genuine cancel-at-45%-then-resume.

The prune tests are the same shape: a pack is really deleted from a real disk,
and the checks are that the bytes went, the job survived, its manifest turned
into a `410`, its pairing code stopped resolving, a rebuild reproduced the
identical checksums, and neither guard could be walked past by accident.

## Known limits

- **One job at a time is not enforced.** Two large builds will happily compete
  for the same cores. Fine for one operator; a queue is wanted before this is
  shared.
- **No authentication beyond the per-job code.** Anyone on the LAN can create a
  job and see the list.
- **Reclaiming disk is manual.** Prune is a button and a command, not a policy:
  nothing expires a pack on age or a disk-space watermark, so packs still
  accumulate in `--packs` until someone asks for the space back.
- **Receipt custody is per control plane.** A device recovers from the server it
  was filled from. Point the app at a different one, or lose the jobs database,
  and the backup is gone even though the device's own receipt is unaffected.
- **`--packs` is trusted.** The server serves only files inside a job's own pack
  directory, but it does not sandbox what the generator writes there.
