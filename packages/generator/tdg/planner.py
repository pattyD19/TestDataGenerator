"""Plan and generate a pack that lands on an exact byte target."""
import json
import os
import random
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone

from . import amplify, checkpoint, manifest, sizing
from .exifwrite import set_mtime
from .personas import CITIES, PERSONAS, city_tz


def capture_dates(count, since: datetime, until: datetime, rng):
    """Capture times clustered into bursts, the way a real camera roll looks.

    A uniform sprinkle would make every date-grouping feature look identical.
    Real libraries are lumpy: a holiday, a birthday, three months of nothing.
    """
    span = (until - since).total_seconds()
    n_clusters = max(3, count // 8)
    centres = sorted(rng.uniform(0, span) for _ in range(n_clusters))
    out = []
    for i in range(count):
        if rng.random() < 0.15:
            # A background sprinkle across the whole range. Without it every
            # photo lands in a handful of months and a timeline UI never gets
            # exercised on sparse periods.
            t = rng.uniform(0, span)
        else:
            c = centres[rng.randrange(n_clusters)]
            jitter = rng.gauss(0, 4 * 86400)
            t = min(max(c + jitter, 0), span)
        d = since + timedelta(seconds=t)
        # daylight hours, mostly
        d = d.replace(hour=int(rng.triangular(6, 22, 14)),
                      minute=rng.randrange(60), second=rng.randrange(60),
                      microsecond=rng.randrange(1000) * 1000)
        out.append(d)
    out.sort()
    return out


def _photo_task(args):
    """Top-level so it survives pickling into the worker pool."""
    (seed_path, out_path, persona, when, index, job_id, gps) = args
    size = amplify.render_photo(seed_path, out_path, persona, when, index, job_id, gps=gps)
    return out_path, size


def build_pack(out_dir, seed_dir, target_bytes, persona_key="iphone-15-pro",
               photo_fraction=0.70, since=None, until=None, job_id=None,
               seed=1, video_mode=None, clip_seconds=(20, 60), preset="ultrafast",
               jobs=None, progress=print, restart=False):
    persona = PERSONAS[persona_key]
    rng = random.Random(seed)
    job_id = job_id or f"{rng.randrange(16**6):06x}"
    since = since or datetime(2022, 1, 1)
    until_explicit = until is not None

    os.makedirs(out_dir, exist_ok=True)
    ckpt = checkpoint.Checkpoint(out_dir)
    if restart:
        ckpt.clear()
    head, done_items = ckpt.read()

    # `until` defaults to now(), which moves between runs. It is therefore
    # taken from the checkpoint when resuming, and only pinned into the
    # fingerprint when the caller asked for a specific value.
    fp = checkpoint.fingerprint(
        target=target_bytes, persona=persona_key, photo_fraction=photo_fraction,
        since=since.isoformat(), until=until.isoformat() if until_explicit else None,
        job=job_id, seed=seed, video_mode=video_mode,
        clips=list(clip_seconds), preset=preset,
        seeds=os.path.abspath(seed_dir))

    resuming = head is not None
    if resuming and head.get("fingerprint") != fp:
        raise SystemExit(
            f"{out_dir} holds a partial build with different settings.\n"
            "  Resuming it would produce a pack matching neither run. Either\n"
            "  build into a fresh --out, or pass --restart to discard it.")
    if resuming:
        until = datetime.fromisoformat(head["until"])
    else:
        until = until or datetime.now()
    seeds_json = os.path.join(seed_dir, "seeds.json")
    if not os.path.exists(seeds_json):
        # The likeliest misconfiguration on a fresh machine, and a traceback
        # about a missing file does not tell anyone what to do about it.
        raise SystemExit(
            f"no seed pool at {seed_dir}\n"
            "  Build one first:\n"
            "    python3 -m tdg.cli bootstrap-seeds        # synthetic, no network\n"
            "    python3 -m tdg.cli harvest --images 200   # real open-license media")
    with open(seeds_json) as fh:
        seed_doc = json.load(fh)
    stills = [s for s in seed_doc["seeds"] if s["kind"] == "image"]
    clips = [s for s in seed_doc["seeds"] if s["kind"] == "video"]
    if not stills:
        raise SystemExit("seed pool has no stills — run `bootstrap-seeds` or `harvest` first")

    vmode = video_mode or persona.video_modes[0]
    vw, vh, vbitrate = vmode
    abr = 128_000
    # Nominal rate from the bitrates, plus headroom for container overhead:
    # measured MP4s ran ~4-5% over (moov atom, AAC overshoot, CBR slack).
    # The rate is re-learned from the first real clip, so this is only a seed.
    nominal_bps = (vbitrate + abr) / 8
    bytes_per_sec = nominal_bps * 1.06

    photo_budget = int(target_bytes * photo_fraction)
    video_budget = target_bytes - photo_budget

    est_clip = bytes_per_sec * sum(clip_seconds) / 2
    n_videos = max(1, round(video_budget / est_clip)) if video_budget > 0 else 0
    est_photo = 3.2 * 1024 * 1024
    n_photos = max(1, int(photo_budget // est_photo))

    total_planned = n_videos + n_photos + 4
    dates = capture_dates(total_planned, since, until, rng)
    # A city carries a timezone as well as coordinates, and every item gets one
    # even when it gets no GPS: a camera always knows what time it is locally,
    # and an unanchored capture time lands at a different instant on every
    # machine that imports it.
    city_pool = [(name, lat + rng.gauss(0, 0.05), lon + rng.gauss(0, 0.05),
                  city_tz(tz, fallback))
                 for name, lat, lon, tz, fallback in CITIES]

    items, total, idx = [], 0, 0
    made = 0
    phase = "video"
    remaining_video, video_i = video_budget, 0

    if resuming:
        missing = ckpt.verify(out_dir, done_items)
        if missing:
            raise SystemExit(
                f"{len(missing)} file(s) recorded in the checkpoint are missing or "
                f"truncated, e.g. {missing[0]}.\n"
                "  The build cannot be resumed safely — pass --restart to rebuild.")
        # Setup above consumed the RNG deterministically; discard that position
        # and restore the one the interrupted run had reached, so the resumed
        # pack is identical to an uninterrupted build.
        rng.setstate(checkpoint.rng_state_from_json(head["rng"]))
        items = done_items
        total = sum(i["bytes"] for i in items)
        idx = head["idx"]
        made = head["made"]
        phase = head["phase"]
        bytes_per_sec = head["bytes_per_sec"]
        remaining_video, video_i = head["remaining_video"], head["video_i"]
        swept = checkpoint.sweep_orphans(out_dir, job_id, {i["name"] for i in items})
        progress(f"  resuming: {len(items)} files, {sizing.human(total)} already built"
                 + (f"; discarded {swept} partial file(s)" if swept else ""))
    ckpt.open_log(existing_count=len(items))

    def save(new_items):
        """Append completed items, then publish the header that counts them."""
        ckpt.append(new_items)
        ckpt.commit(fingerprint=fp, until=until.isoformat(), phase=phase,
                    idx=idx, made=made, bytes_per_sec=bytes_per_sec,
                    remaining_video=remaining_video, video_i=video_i,
                    rng=checkpoint.rng_state_to_json(rng.getstate()))

    def localise(when, city):
        """Anchor a naive capture time to the city it was 'taken' in."""
        return when.replace(tzinfo=city[3])

    def record(name, kind, size, when, gps):
        nonlocal total
        path = os.path.join(out_dir, name)
        item = {
            "name": name,
            "kind": kind,
            "bytes": size,
            "sha256": manifest.sha256_file(path),
            "taken_at": when.isoformat(timespec="seconds"),
            "taken_at_utc": when.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "gps": {"lat": round(gps[0], 6), "lon": round(gps[1], 6)} if gps else None,
        }
        items.append(item)
        total += size
        return item

    # ---- videos -----------------------------------------------------------
    # Driven by the remaining video budget rather than a fixed count, so a
    # small pack still gets real clips instead of one oversized trim file.
    while phase == "video" and remaining_video >= bytes_per_sec * 3 \
            and video_i < n_videos + 4:
        # A clip carries no GPS, but it still needs a zone: MP4 stores an
        # absolute instant, so an unanchored time would put video hours away
        # from the stills shot beside it.
        when = localise(dates[min(idx, len(dates) - 1)], rng.choice(city_pool))
        dur = max(3.0, min(rng.uniform(*clip_seconds), remaining_video / bytes_per_sec))
        if dur < 3.0:
            break
        name = f"TDG_{job_id}_{idx:05d}.mp4"
        size = amplify.render_video(
            os.path.join(out_dir, name), persona, when, idx, job_id,
            vw, vh, vbitrate, dur,
            seed_clip=os.path.join(seed_dir, rng.choice(clips)["file"]) if clips else None,
            preset=preset)
        item = record(name, "video", size, when, None)
        idx += 1
        remaining_video -= size
        bytes_per_sec = size / dur          # learn the real rate
        video_i += 1
        save([item])
        if total >= target_bytes:
            break
        progress(f"  video {video_i}  {sizing.human(size)} ({dur:.1f}s)  "
                 f"total {sizing.human(total)}")

    # ---- photos -----------------------------------------------------------
    # Bounded by the photo budget rather than the whole target, so an
    # under-spent video budget becomes a trim clip instead of silently
    # turning the pack into stills.
    # Clamped to the target: a video that overshot its own budget must not
    # push the photo ceiling past the pack size. Any shortfall is closed by
    # the trim stage below, which can only ever add.
    # Derived from the video total rather than the running total, so a resume
    # part-way through the photo phase computes the same ceiling as the run it
    # is continuing. No trim clips exist yet at this point, so every video byte
    # recorded so far belongs to the phase above.
    video_total = sum(i["bytes"] for i in items if i["kind"] == "video")
    photo_ceiling = min(video_total + photo_budget, target_bytes)
    jobs = jobs or max(1, (os.cpu_count() or 2))
    if phase == "video":
        phase = "photo"

    def next_task():
        nonlocal idx
        city = rng.choice(city_pool)
        when = localise(dates[min(idx, len(dates) - 1)], city)
        # No GPS on a quarter of them — location services off, indoors, or the
        # photo predates the permission. The zone still comes from the city.
        gps = (city[1], city[2]) if rng.random() < 0.75 else None
        name = f"TDG_{job_id}_{idx:05d}.jpg"
        task = (os.path.join(seed_dir, rng.choice(stills)["file"]),
                os.path.join(out_dir, name), persona, when, idx, job_id, gps)
        idx += 1
        return name, when, gps, task

    with ProcessPoolExecutor(max_workers=jobs) as pool:
        while phase == "photo":
            remaining = photo_ceiling - total
            # Size the batch off the LARGEST photo seen, not the average.
            # Averages overshoot: JPEG sizes spread ~1.7-2.8 MB on the same
            # persona, so a batch priced at the mean lands over the ceiling.
            worst = max((i["bytes"] for i in items if i["kind"] == "image"),
                        default=est_photo * 1.4)
            n = int(remaining / worst)
            if n >= 2:
                n = min(n, jobs * 4)
                batch = [next_task() for _ in range(n)]
                fresh = []
                for (name, when, gps, _), (_, size) in zip(
                        batch, pool.map(_photo_task, [b[3] for b in batch])):
                    fresh.append(record(name, "image", size, when, gps))
                made += n
                save(fresh)
                progress(f"  photos {made}  total {sizing.human(total)} / "
                         f"{sizing.human(target_bytes)}")
                continue

            # Tail: one at a time, discarding any file that would overshoot.
            name, when, gps, task = next_task()
            _, size = _photo_task(task)
            if total + size > photo_ceiling:
                os.remove(task[1])
                idx -= 1
                phase = "trim"
                save([])
                break
            made += 1
            save([record(name, "image", size, when, gps)])

    # ---- trim to the exact target ----------------------------------------
    # Video absorbs a large deficit cheaply, but only above a floor: a
    # sub-second 4K clip costs ~9 MB regardless of duration (keyframe, moov
    # atom, faststart), so pricing a small deficit as video overshoots badly.
    # Below that floor, padded JPEGs close the gap exactly.
    if phase == "photo":
        phase = "trim"
    deficit = target_bytes - total
    min_clip_bytes = bytes_per_sec * 8
    while phase == "trim" and deficit >= min_clip_bytes:
        dur = min(deficit / (bytes_per_sec * 1.02), 120)
        when = localise(dates[min(idx, len(dates) - 1)], rng.choice(city_pool))
        name = f"TDG_{job_id}_{idx:05d}.mp4"
        size = amplify.render_video(
            os.path.join(out_dir, name), persona, when, idx, job_id,
            vw, vh, vbitrate, dur,
            seed_clip=os.path.join(seed_dir, rng.choice(clips)["file"]) if clips else None,
            preset=preset)
        item = record(name, "video", size, when, None)
        idx += 1
        bytes_per_sec = size / dur
        deficit = target_bytes - total
        save([item])
        progress(f"  trim video {sizing.human(size)}  deficit {sizing.human(deficit)}")
        if size > deficit + min_clip_bytes:
            break                       # rate estimate is off; stop guessing

    if phase == "trim":
        phase = "pad"
    PAD_CHUNK = 8 * 1024 * 1024
    while phase == "pad" and deficit > 0:
        chunk = min(deficit, PAD_CHUNK)
        city = rng.choice(city_pool)
        when = localise(dates[min(idx, len(dates) - 1)], city)
        name = f"TDG_{job_id}_{idx:05d}.jpg"
        path = os.path.join(out_dir, name)
        gps = (city[1], city[2])
        size = None
        for q, scale in ((70, 0.5), (55, 0.35), (40, 0.22), (30, 0.12), (25, 0.06)):
            w = max(320, int(persona.still_sizes[0][0] * scale))
            h = max(240, int(persona.still_sizes[0][1] * scale))
            size = amplify.render_photo(
                os.path.join(seed_dir, rng.choice(stills)["file"]), path,
                persona, when, idx, job_id, target_size=(w, h), quality=q, gps=gps)
            if size <= chunk:
                break
        if size > chunk:
            os.remove(path)
            break                       # cannot go smaller; accept the delta
        size = sizing.pad_jpeg_to(path, chunk)
        set_mtime(path, when)
        item = record(name, "image", size, when, gps)
        idx += 1
        deficit = target_bytes - total
        save([item])
        progress(f"  pad photo {sizing.human(size)}  deficit {sizing.human(deficit)}")

    doc = manifest.write_manifest(
        out_dir, job_id, persona_key, target_bytes, items,
        notes={"video_mode": [vw, vh, vbitrate], "photo_fraction": photo_fraction,
               "seed_pool": os.path.abspath(seed_dir), "formats": ["jpeg", "mp4/h264"]})
    manifest.write_licenses(out_dir, seed_doc["seeds"])
    # The manifest is the finished article; the checkpoint only existed to get
    # here, and leaving it behind would make a complete pack look partial.
    ckpt.clear()
    return doc
