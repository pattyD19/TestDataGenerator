"""tdg — mobile test data generator."""
import argparse
import json
import os
import sys
import time
from datetime import datetime

from . import planner, sizing, synth
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
    print(f"Building {sizing.human(target)} pack "
          f"[{args.profile}] -> {args.out}")
    doc = planner.build_pack(
        args.out, args.seeds, target, persona_key=args.profile,
        photo_fraction=args.photo_fraction,
        since=datetime.fromisoformat(args.since) if args.since else None,
        until=datetime.fromisoformat(args.until) if args.until else None,
        job_id=args.job, seed=args.seed, preset=args.preset, jobs=args.jobs,
        clip_seconds=(args.min_clip, args.max_clip),
        progress=(lambda m: None) if args.quiet else print)
    dt = time.time() - started
    delta = doc["delta_bytes"]
    pct = 100 * delta / target if target else 0
    print()
    print(f"  job          {doc['job_id']}")
    print(f"  files        {doc['file_count']}  "
          f"({doc['photo_count']} photos, {doc['video_count']} videos)")
    print(f"  target       {sizing.human(target)}")
    print(f"  actual       {sizing.human(doc['total_bytes'])}  "
          f"(delta {delta:+,} bytes, {pct:+.4f}%)")
    print(f"  elapsed      {dt:,.1f}s  ({sizing.human(doc['total_bytes']/max(dt,.001))}/s)")
    print(f"  manifest     {os.path.join(args.out, 'manifest.json')}")


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
    g.add_argument("--quiet", action="store_true")
    g.set_defaults(fn=cmd_build)

    i = sub.add_parser("inspect", help="summarise a built pack")
    i.add_argument("pack")
    i.set_defaults(fn=cmd_inspect)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
