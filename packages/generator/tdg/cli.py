"""tdg — mobile test data generator."""
import argparse
import json
import os
import sys
import time
from datetime import datetime

from . import loader, planner, sizing, synth, verify as verify_mod
from .personas import PERSONAS

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DEFAULT_SEEDS = os.path.join(REPO, "seed-pool")


def cmd_bootstrap(args):
    n = args.count
    print(f"Synthesising {n} bootstrap seed stills into {args.out}")
    entries = synth.build_bootstrap_pool(args.out, count=n, seed=args.seed)
    print(f"  {len(entries)} seeds written. These are synthetic placeholders —")
    print("  run `tdg harvest` on a machine with internet for real media.")


def cmd_harvest(args):
    from . import harvest
    print(f"Harvesting up to {args.images} open-license stills into {args.out}")
    entries = harvest.harvest(args.out, images=args.images, videos=not args.no_videos,
                              query=args.query, cc0_only=not args.any_license)
    print(f"  seed pool now holds {len(entries)} assets")


def cmd_build(args):
    target = sizing.parse_size(args.size)
    started = time.time()
    say = (lambda *a: None) if args.quiet else print
    say(f"Building {sizing.human(target)} pack "
        f"[{args.profile}] -> {args.out}")
    doc = planner.build_pack(
        args.out, args.seeds, target, persona_key=args.profile,
        photo_fraction=args.photo_fraction,
        since=datetime.fromisoformat(args.since) if args.since else None,
        until=datetime.fromisoformat(args.until) if args.until else None,
        job_id=args.job, seed=args.seed, preset=args.preset, jobs=args.jobs,
        clip_seconds=(args.min_clip, args.max_clip), restart=args.restart,
        video_jobs=args.video_jobs,
        photo_format=args.photo_format, video_codec=args.video_codec,
        edge_cases=args.edge_cases,
        progress=(lambda m: None) if args.quiet else print)
    dt = time.time() - started
    delta = doc["delta_bytes"]
    pct = 100 * delta / target if target else 0
    say()
    say(f"  job          {doc['job_id']}")
    say(f"  files        {doc['file_count']}  "
        f"({doc['photo_count']} photos, {doc['video_count']} videos)")
    say(f"  target       {sizing.human(target)}")
    say(f"  actual       {sizing.human(doc['total_bytes'])}  "
        f"(delta {delta:+,} bytes, {pct:+.4f}%)")
    say(f"  elapsed      {dt:,.1f}s  ({sizing.human(doc['total_bytes']/max(dt,.001))}/s)")
    say(f"  manifest     {os.path.join(args.out, 'manifest.json')}")


def cmd_inspect(args):
    with open(os.path.join(args.pack, "manifest.json")) as fh:
        doc = json.load(fh)
    print(f"job {doc['job_id']}  persona {doc['persona']}  schema v{doc['schema_version']}")
    print(f"{doc['file_count']} files, {sizing.human(doc['total_bytes'])} "
          f"(target {sizing.human(doc['target_bytes'])}, delta {doc['delta_bytes']:+,} B)")
    dates = sorted(i["taken_at"] for i in doc["items"])
    print(f"capture range {dates[0]} .. {dates[-1]}")
    by_month = {}
    for i in doc["items"]:
        by_month[i["taken_at"][:7]] = by_month.get(i["taken_at"][:7], 0) + 1
    top = sorted(by_month.items(), key=lambda kv: -kv[1])[:6]
    print("busiest months: " + ", ".join(f"{m} ({c})" for m, c in top))
    print(f"prefix {doc['filename_prefix']}   album {doc['album']!r}")
    hashes = {i["sha256"] for i in doc["items"]}
    print(f"unique checksums: {len(hashes)} / {len(doc['items'])}")


def cmd_load(args):
    loader.load(args.pack, args.target, device=args.device, dest=args.dest,
                force=args.force, dry_run=args.dry_run, limit=args.limit,
                verify=args.verify,
                progress=(lambda m: None) if args.quiet else print)


def cmd_wipe(args):
    loader.wipe(job_id=args.job, target=args.target, device=args.device,
                dry_run=args.dry_run, erase_device=args.erase_device)


def cmd_devices(args):
    def probe(fn):
        # One missing toolchain must not hide the other platform's devices.
        try:
            return fn(), None
        except (SystemExit, RuntimeError) as exc:
            return [], str(exc)

    sims, sim_err = probe(loader.booted_simulators)
    print("booted simulators:")
    for s in sims:
        print(f"  {s['udid']}  {s['name']}  ({s['runtime']})")
    if not sims:
        print("  " + "\n  ".join((sim_err or "none").splitlines()))

    devs, adb_err = probe(loader.adb_devices)
    print("adb devices:")
    for d in devs:
        print(f"  {d['serial']}  {d['desc']}")
    if not devs:
        print("  " + "\n  ".join((adb_err or "none").splitlines()))


def cmd_receipts(args):
    docs = loader.find_receipts(args.job, args.target, args.device)
    if not docs:
        print(f"no receipts in {loader.receipts_dir()}")
        return
    for d in docs:
        ok = sum(1 for e in d["entries"] if e.get("ok"))
        print(f"{d['job_id']}  {d['target']}"
              + (f" {d['device']}" if d.get("device") else "")
              + f"  {ok}/{len(d['entries'])} files"
              + (f"  {sizing.human(d['bytes_loaded'])}" if d.get("bytes_loaded") else "")
              + f"  {d.get('loaded_at') or 'incomplete'}")
        if d.get("dest"):
            print(f"    -> {d['dest']}")


def cmd_verify(args):
    report = verify_mod.verify(args.pack, args.target, device=args.device)
    print(verify_mod.summarise(report))
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"\n  json     {args.json}")
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w") as fh:
            fh.write(verify_mod.markdown(report, args.label))
        print(f"  report   {args.out}")
    # A conformance run that fails should fail the shell too, so CI notices.
    return 0 if report["passed"] else 1


def main(argv=None):
    p = argparse.ArgumentParser(prog="tdg", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bootstrap-seeds", help="synthesise a seed pool locally (no network)")
    b.add_argument("--out", default=DEFAULT_SEEDS)
    b.add_argument("--count", type=int, default=48)
    b.add_argument("--seed", type=int, default=1)
    b.set_defaults(fn=cmd_bootstrap)

    h = sub.add_parser("harvest", help="fetch real open-license seed media (needs internet)")
    h.add_argument("--out", default=DEFAULT_SEEDS)
    h.add_argument("--images", type=int, default=200)
    h.add_argument("--query", default="landscape")
    h.add_argument("--no-videos", action="store_true")
    h.add_argument("--any-license", action="store_true",
                   help="allow attribution-required licences, not just CC0/PDM")
    h.set_defaults(fn=cmd_harvest)

    g = sub.add_parser("build", help="build a pack of a given size")
    g.add_argument("--size", required=True, help="e.g. 25GB, 512MB")
    g.add_argument("--out", required=True)
    g.add_argument("--seeds", default=DEFAULT_SEEDS)
    g.add_argument("--profile", default="iphone-15-pro", choices=sorted(PERSONAS))
    g.add_argument("--photo-fraction", type=float, default=0.70,
                   help="share of BYTES that should be stills (default 0.70)")
    g.add_argument("--since", help="earliest capture date, ISO (default 2022-01-01)")
    g.add_argument("--until", help="latest capture date, ISO (default now)")
    g.add_argument("--job", help="job id; default random. Same job+seed = same pack")
    g.add_argument("--seed", type=int, default=1)
    g.add_argument("--preset", default="ultrafast",
                   help="x264 preset; ultrafast keeps large packs practical")
    g.add_argument("--min-clip", type=float, default=20)
    g.add_argument("--max-clip", type=float, default=60)
    g.add_argument("--jobs", type=int, default=None,
                   help="parallel photo encoders (default: CPU count)")
    g.add_argument("--photo-format", default="jpeg",
                   choices=("jpeg", "heic", "mixed"),
                   help="HEIC is what a real iPhone shoots; 'mixed' is the "
                        "realistic library, where shared images arrive as JPEG")
    g.add_argument("--video-codec", default="h264", choices=("h264", "hevc"),
                   help="HEVC is what a modern phone records")
    g.add_argument("--edge-cases", action="store_true",
                   help="add the awkward files: duplicates, zero-byte, "
                        "truncated, unicode names, a burst run, a screenshot")
    g.add_argument("--video-jobs", type=int, default=None,
                   help="clips to encode at once (default 4). The lavfi source "
                        "is single-threaded, so this is where video build time "
                        "comes from; above 4 it got slower, not faster")
    g.add_argument("--restart", action="store_true",
                   help="discard a partial build in --out and start over "
                        "(a matching partial build is otherwise resumed)")
    g.add_argument("--quiet", action="store_true")
    g.set_defaults(fn=cmd_build)

    i = sub.add_parser("inspect", help="summarise a built pack")
    i.add_argument("pack")
    i.set_defaults(fn=cmd_inspect)

    l = sub.add_parser("load", help="push a pack onto a folder, simulator or emulator")
    l.add_argument("--pack", required=True, help="a directory built by `tdg build`")
    l.add_argument("--target", required=True, choices=loader.TARGETS)
    l.add_argument("--device", help="simulator udid (or 'booted') / adb serial")
    l.add_argument("--dest", help="folder target: destination dir. "
                                  "emulator: remote dir, default /sdcard/DCIM/TDG_<job>")
    l.add_argument("--force", action="store_true",
                   help="re-send files the receipt says are already there")
    l.add_argument("--limit", type=int, help="stop after N files (smoke tests)")
    l.add_argument("--verify", action="store_true",
                   help="re-hash what landed (folder and emulator only)")
    l.add_argument("--dry-run", action="store_true",
                   help="preflight only: resolve device, check free space, write nothing")
    l.add_argument("--quiet", action="store_true")
    l.set_defaults(fn=cmd_load)

    w = sub.add_parser("wipe", help="remove a previously loaded pack, using its receipt")
    w.add_argument("--job", help="job id; default every recorded job")
    w.add_argument("--target", choices=loader.TARGETS)
    w.add_argument("--device")
    w.add_argument("--dry-run", action="store_true")
    w.add_argument("--erase-device", action="store_true",
                   help="simulator only: erase the WHOLE simulator, since simctl "
                        "cannot delete individual assets")
    w.set_defaults(fn=cmd_wipe)

    d = sub.add_parser("devices", help="what simulators and adb devices are reachable")
    d.set_defaults(fn=cmd_devices)

    v = sub.add_parser("verify", help="ask the device what the gallery actually indexed")
    v.add_argument("--pack", required=True)
    v.add_argument("--target", required=True, choices=loader.TARGETS)
    v.add_argument("--device")
    v.add_argument("--label", help="device name for the report heading")
    v.add_argument("--out", help="write a markdown report here, e.g. docs/conformance/pixel.md")
    v.add_argument("--json", help="write the raw numbers here")
    v.set_defaults(fn=cmd_verify)

    r = sub.add_parser("receipts", help="what has been loaded where")
    r.add_argument("--job")
    r.add_argument("--target", choices=loader.TARGETS)
    r.add_argument("--device")
    r.set_defaults(fn=cmd_receipts)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except RuntimeError as exc:
        # An external tool (adb, ffmpeg, simctl) refused. The message from the
        # tool is the useful part; a Python traceback is not.
        raise SystemExit(str(exc))
    except KeyboardInterrupt:
        raise SystemExit("\ninterrupted. Re-run the same command to resume.")


if __name__ == "__main__":
    sys.exit(main())
