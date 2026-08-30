"""Plan and generate a pack that lands on an exact byte target."""
import json
import os
import random
import shutil
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
    (seed_path, out_path, persona, when, index, job_id, gps, fmt) = args
    size = amplify.render_photo(seed_path, out_path, persona, when, index, job_id,
                                gps=gps, fmt=fmt)
    return out_path, size


def _video_task(args):
    """Top-level so it survives pickling into the worker pool."""
    (out_path, persona, when, index, job_id, vw, vh, vbitrate, dur,
     seed_clip, preset, codec) = args
    size = amplify.render_video(out_path, persona, when, index, job_id,
                                vw, vh, vbitrate, dur, seed_clip=seed_clip,
                                preset=preset, codec=codec)
    return out_path, size


# How many clips to encode at once.
#
# The bottleneck is not the encoder: a 4K clip spends ~29 of its ~30 seconds in
# the single-threaded lavfi source generating frames, and the whole job uses
# about 1.4 of 10 cores. Running several at once fills the machine.
#
# Measured on a 10-core Apple Silicon laptop (4 performance + 6 efficiency),
# 4K clips at 45 Mbps:
#
#     1 at a time   1.72 MB/s
#     4 at a time   5.21 MB/s     <- 3.0x
#     8 at a time   1.44 MB/s     <- slower than serial
#
# Eight regressed hard — past the performance cores the work lands on
# efficiency cores and the 4K frame buffers start to hurt. So this is
# deliberately capped low rather than set to the core count.
VIDEO_JOBS_CAP = 4


PHOTO_EXT = {"jpeg": "jpg", "heic": "heic"}


def _screenshot(path, when, size=(1170, 2532)):
    """A screenshot, which is nothing like a photograph.

    Flat colour, hard edges, PNG, and **no camera EXIF at all** — that last
    part is the point. An app that assumes every library asset has a Make and a
    DateTimeOriginal falls over on the screenshots every real phone is full of.
    """
    from PIL import Image, ImageDraw
    w, h = size
    img = Image.new("RGB", (w, h), (247, 246, 243))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, int(h * 0.06)], fill=(28, 30, 34))          # status bar
    d.rectangle([0, int(h * 0.06), w, int(h * 0.20)], fill=(47, 93, 80))
    for i in range(7):                                                # list rows
        y = int(h * 0.24) + i * int(h * 0.09)
        d.rounded_rectangle([int(w * 0.06), y, int(w * 0.94), y + int(h * 0.07)],
                            radius=18, fill=(255, 255, 255),
                            outline=(226, 222, 214), width=2)
        d.rectangle([int(w * 0.10), y + int(h * 0.02),
                     int(w * (0.30 + 0.35 * ((i * 7) % 5) / 5)),
                     y + int(h * 0.028)], fill=(200, 198, 192))
    d.rectangle([0, int(h * 0.93), w, h], fill=(240, 238, 234))
    img.save(path, "PNG", optimize=False)
    set_mtime(path, when)
    return os.path.getsize(path)


def _photo_fmt(photo_format, rng):
    """Per-item format. 'mixed' is the interesting case for an app under test:
    a real iPhone library is HEIC, but anything shared into it arrives JPEG."""
    if photo_format == "mixed":
        return "heic" if rng.random() < 0.5 else "jpeg"
    return photo_format


def build_pack(out_dir, seed_dir, target_bytes, persona_key="iphone-15-pro",
               photo_fraction=0.70, since=None, until=None, job_id=None,
               seed=1, video_mode=None, clip_seconds=(20, 60), preset="ultrafast",
               jobs=None, progress=print, restart=False,
               photo_format="jpeg", video_codec="h264", edge_cases=False,
               video_jobs=None):
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
        photo_format=photo_format, video_codec=video_codec,
        edge_cases=edge_cases, video_jobs=video_jobs,
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

    def record(name, kind, size, when, gps, fmt=None, note=None):
        nonlocal total
        path = os.path.join(out_dir, name)
        item = {
            "name": name,
            "kind": kind,
            "format": fmt or ("mp4/" + video_codec if kind == "video"
                              else PHOTO_EXT.get(photo_format, "jpg")),
            "bytes": size,
            "sha256": manifest.sha256_file(path),
            "taken_at": when.isoformat(timespec="seconds"),
            "taken_at_utc": when.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "gps": {"lat": round(gps[0], 6), "lon": round(gps[1], 6)} if gps else None,
        }
        if note:
            item["note"] = note      # why this file is odd, for the edge pack
        items.append(item)
        total += size
        return item

    # ---- videos -----------------------------------------------------------
    # Driven by the remaining video budget rather than a fixed count, so a
    # small pack still gets real clips instead of one oversized trim file.
    vjobs = max(1, min(video_jobs or VIDEO_JOBS_CAP, VIDEO_JOBS_CAP))
    with ProcessPoolExecutor(max_workers=vjobs) as vpool:
        while phase == "video" and remaining_video >= bytes_per_sec * 3 \
                and video_i < n_videos + 4:
            # Plan a batch first, consuming the RNG in exactly the order a
            # serial run would, then encode the batch in parallel. Durations
            # within a batch are priced off one rate estimate rather than
            # re-learned per clip; the trim stage still lands the pack exactly,
            # because that is where exactness has always come from.
            batch, planned = [], remaining_video
            for _ in range(vjobs):
                if planned < bytes_per_sec * 3 or video_i + len(batch) >= n_videos + 4:
                    break
                # A clip carries no GPS, but it still needs a zone: MP4 stores
                # an absolute instant, so an unanchored time would put video
                # hours away from the stills shot beside it.
                when = localise(dates[min(idx, len(dates) - 1)],
                                rng.choice(city_pool))
                dur = max(3.0, min(rng.uniform(*clip_seconds),
                                   planned / bytes_per_sec))
                if dur < 3.0:
                    break
                name = f"TDG_{job_id}_{idx:05d}.mp4"
                seed_clip = (os.path.join(seed_dir, rng.choice(clips)["file"])
                             if clips else None)
                batch.append((name, when, dur, (
                    os.path.join(out_dir, name), persona, when, idx, job_id,
                    vw, vh, vbitrate, dur, seed_clip, preset, video_codec)))
                idx += 1
                planned -= dur * bytes_per_sec
            if not batch:
                break

            fresh, encoded, seconds = [], 0, 0.0
            for (name, when, dur, _), (_, size) in zip(
                    batch, vpool.map(_video_task, [b[3] for b in batch])):
                fresh.append(record(name, "video", size, when, None,
                                    fmt="mp4/" + video_codec))
                remaining_video -= size
                encoded += size
                seconds += dur
                video_i += 1
            if seconds:
                bytes_per_sec = encoded / seconds     # learn the real rate
            save(fresh)
            progress(f"  video {video_i}  {sizing.human(encoded)} in "
                     f"{len(batch)} clip(s)  total {sizing.human(total)}")
            if total >= target_bytes:
                break

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

    def build_edge_cases():
        """The files that break naive gallery code.

        Deliberately generated *before* the bulk of the photos, so their bytes
        are inside the budget the size planner is working to rather than an
        overshoot it has to absorb afterwards.
        """
        nonlocal idx
        fresh = []
        city = rng.choice(city_pool)
        base_when = localise(dates[min(idx, len(dates) - 1)], city)
        gps = (city[1], city[2])
        small = (persona.still_sizes[0][0] // 3, persona.still_sizes[0][1] // 3)

        # A burst: the same scene, a few tenths of a second apart. Timeline and
        # "best of" grouping features are exactly what this exercises.
        for k in range(5):
            when = base_when + timedelta(milliseconds=380 * k)
            name = f"TDG_{job_id}_{idx:05d}.jpg"
            size = amplify.render_photo(
                os.path.join(seed_dir, stills[0]["file"]),
                os.path.join(out_dir, name), persona, when, 7000, job_id,
                target_size=small, quality=88, gps=gps, fmt="jpeg")
            fresh.append(record(name, "image", size, when, gps, fmt="jpeg",
                                note="burst frame %d of 5" % (k + 1)))
            idx += 1

        # An exact byte-for-byte duplicate. Everything else in a pack is
        # re-encoded precisely so no two files collide; this one exists to give
        # a dedupe implementation something to find.
        src = fresh[0]
        dup_name = f"TDG_{job_id}_{idx:05d}.jpg"
        shutil.copy2(os.path.join(out_dir, src["name"]),
                     os.path.join(out_dir, dup_name))
        fresh.append(record(dup_name, "image", src["bytes"],
                            datetime.fromisoformat(src["taken_at"]), gps,
                            fmt="jpeg", note="exact duplicate of " + src["name"]))
        idx += 1

        # A screenshot: no camera EXIF at all.
        when = base_when + timedelta(hours=3)
        shot_name = f"TDG_{job_id}_{idx:05d}.png"
        size = _screenshot(os.path.join(out_dir, shot_name), when)
        fresh.append(record(shot_name, "image", size, when, None, fmt="png",
                            note="screenshot: PNG, no camera EXIF"))
        idx += 1

        # A non-ASCII filename. Every layer that shells out or builds a URL
        # gets a vote on whether this works.
        when = base_when + timedelta(hours=5)
        uni_name = f"TDG_{job_id}_{idx:05d}_café_日本語.jpg"
        size = amplify.render_photo(
            os.path.join(seed_dir, stills[0]["file"]),
            os.path.join(out_dir, uni_name), persona, when, 7100, job_id,
            target_size=small, quality=85, gps=gps, fmt="jpeg")
        fresh.append(record(uni_name, "image", size, when, gps, fmt="jpeg",
                            note="non-ASCII filename"))
        idx += 1

        # Truncated: a valid JPEG header over a body that stops early. Decoders
        # should fail gracefully rather than hang or crash.
        when = base_when + timedelta(hours=7)
        trunc_name = f"TDG_{job_id}_{idx:05d}.jpg"
        tmp = os.path.join(out_dir, trunc_name + ".full")
        amplify.render_photo(
            os.path.join(seed_dir, stills[0]["file"]), tmp, persona, when,
            7200, job_id, target_size=small, quality=80, gps=gps, fmt="jpeg")
        with open(tmp, "rb") as fh:
            blob = fh.read()
        os.remove(tmp)
        with open(os.path.join(out_dir, trunc_name), "wb") as fh:
            fh.write(blob[: max(1024, len(blob) // 3)])
        set_mtime(os.path.join(out_dir, trunc_name), when)
        fresh.append(record(trunc_name, "image",
                            os.path.getsize(os.path.join(out_dir, trunc_name)),
                            when, gps, fmt="jpeg",
                            note="truncated: valid header, body cut short"))
        idx += 1

        # Zero bytes. Real libraries contain these after a failed copy.
        when = base_when + timedelta(hours=9)
        zero_name = f"TDG_{job_id}_{idx:05d}.jpg"
        open(os.path.join(out_dir, zero_name), "wb").close()
        set_mtime(os.path.join(out_dir, zero_name), when)
        fresh.append(record(zero_name, "image", 0, when, None, fmt="jpeg",
                            note="zero bytes"))
        idx += 1

        progress(f"  edge cases {len(fresh)}  total {sizing.human(total)}")
        save(fresh)

    if edge_cases and phase == "photo" and not any(i.get("note") for i in items):
        build_edge_cases()

    def next_task():
        nonlocal idx
        city = rng.choice(city_pool)
        when = localise(dates[min(idx, len(dates) - 1)], city)
        # No GPS on a quarter of them — location services off, indoors, or the
        # photo predates the permission. The zone still comes from the city.
        gps = (city[1], city[2]) if rng.random() < 0.75 else None
        fmt = _photo_fmt(photo_format, rng)
        name = f"TDG_{job_id}_{idx:05d}.{PHOTO_EXT[fmt]}"
        task = (os.path.join(seed_dir, rng.choice(stills)["file"]),
                os.path.join(out_dir, name), persona, when, idx, job_id, gps, fmt)
        idx += 1
        return name, when, gps, task, fmt

    with ProcessPoolExecutor(max_workers=jobs) as pool:
        while phase == "photo":
            remaining = photo_ceiling - total
            # Size the batch off the LARGEST photo seen, not the average.
            # Averages overshoot: JPEG sizes spread ~1.7-2.8 MB on the same
            # persona, so a batch priced at the mean lands over the ceiling.
            # Only *ordinary* photos may inform the estimate. The edge cases
            # are deliberately tiny, and letting a 250 KB burst frame set the
            # price of the next batch made a 40 MB pack land at 124 MB.
            worst = max((i["bytes"] for i in items
                         if i["kind"] == "image" and not i.get("note")),
                        default=est_photo * 1.4)
            n = int(remaining / worst)
            if n >= 2:
                n = min(n, jobs * 4)
                batch = [next_task() for _ in range(n)]
                fresh = []
                for (name, when, gps, _, fmt), (_, size) in zip(
                        batch, pool.map(_photo_task, [b[3] for b in batch])):
                    fresh.append(record(name, "image", size, when, gps, fmt=fmt))
                made += n
                save(fresh)
                progress(f"  photos {made}  total {sizing.human(total)} / "
                         f"{sizing.human(target_bytes)}")
                continue

            # Tail: one at a time, discarding any file that would overshoot.
            name, when, gps, task, fmt = next_task()
            _, size = _photo_task(task)
            if total + size > photo_ceiling:
                os.remove(task[1])
                idx -= 1
                phase = "trim"
                save([])
                break
            made += 1
            save([record(name, "image", size, when, gps, fmt=fmt)])

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
            preset=preset, codec=video_codec)
        item = record(name, "video", size, when, None, fmt="mp4/" + video_codec)
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
                persona, when, idx, job_id, target_size=(w, h), quality=q, gps=gps,
                fmt="jpeg")   # COM padding below is a JPEG trick
            if size <= chunk:
                break
        if size > chunk:
            os.remove(path)
            break                       # cannot go smaller; accept the delta
        size = sizing.pad_jpeg_to(path, chunk)
        set_mtime(path, when)
        item = record(name, "image", size, when, gps, fmt="jpeg")
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
