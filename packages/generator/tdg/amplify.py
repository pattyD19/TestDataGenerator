"""Turn one seed asset into many distinct outputs.

Every transform is derived from (job_id, index) so a pack is reproducible: the
same command produces byte-identical output, which makes a failing test
re-runnable. Re-encoding also guarantees no two outputs share a checksum —
important when the app under test does content hashing — except where the
edge-case pack asks for exact duplicates on purpose.
"""
import hashlib
import math
import os
import random
import shutil
import subprocess

from PIL import Image, ImageEnhance, ImageOps

from .exifwrite import build_exif, ffmpeg_time, set_mtime

# Decoding a 25 MB seed PNG for every output dominated the first profile run
# (4.3 MB/s, which is four hours for a 64 GB pack). Seeds are reused heavily,
# so a small per-process cache removes almost all of that cost.
_SEED_CACHE = {}
_SEED_CACHE_MAX = 8


def load_seed(path):
    img = _SEED_CACHE.get(path)
    if img is None:
        img = Image.open(path).convert("RGB")
        img.load()
        if len(_SEED_CACHE) >= _SEED_CACHE_MAX:
            _SEED_CACHE.pop(next(iter(_SEED_CACHE)))
        _SEED_CACHE[path] = img
    return img


# --- HEIC ------------------------------------------------------------------
# HEIC is HEVC inside a HEIF container. Hand-rolling that is not the same kind
# of job as hand-rolling an EXIF block, so this is the one place the generator
# takes an encoder from outside — and it takes whichever is present rather than
# requiring one.
_HEIC_BACKEND = None


def heic_backend():
    """'pillow-heif', 'sips' or 'none'."""
    global _HEIC_BACKEND
    if _HEIC_BACKEND is None:
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
            _HEIC_BACKEND = "pillow-heif"
        except Exception:
            _HEIC_BACKEND = "sips" if shutil.which("sips") else "none"
    return _HEIC_BACKEND


def require_heic():
    if heic_backend() == "none":
        raise SystemExit(
            "no HEIC encoder available.\n"
            "  macOS ships `sips`, which this uses automatically.\n"
            "  Anywhere else (a Linux build box, the Docker image):\n"
            "    pip install pillow-heif")


def _to_heic(jpeg_path, heic_path, quality):
    """Convert a rendered JPEG to HEIC, carrying its EXIF across.

    Verified against sips on macOS 26: DateTimeOriginal, the OffsetTime tags
    and Make/Model all survive the conversion, which is what makes a HEIC pack
    worth generating at all.
    """
    backend = heic_backend()
    if backend == "pillow-heif":
        img = Image.open(jpeg_path)
        exif = img.info.get("exif")
        img.save(heic_path, "HEIF", quality=quality,
                 **({"exif": exif} if exif else {}))
    else:
        p = subprocess.run(
            ["sips", "-s", "format", "heic", "-s", "formatOptions", str(quality),
             jpeg_path, "--out", heic_path],
            capture_output=True)
        if p.returncode != 0 or not os.path.exists(heic_path):
            raise RuntimeError(f"sips could not write HEIC: "
                               f"{p.stderr.decode()[-300:]}")
    os.remove(jpeg_path)
    return os.path.getsize(heic_path)


def _rng(job_id, index):
    h = hashlib.sha256(f"{job_id}:{index}".encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def dhash(img, size=8):
    """A 64-bit perceptual hash — does this LOOK like one already made?

    Byte uniqueness is free: different EXIF alone guarantees it, and says
    nothing about whether two files are the same picture. This is the cheap
    proxy for the question a dedupe engine actually asks, and it is computed
    from the image already in memory, so it costs no extra decode.
    """
    g = img.convert("L").resize((size + 1, size), Image.LANCZOS)
    raw = g.tobytes()                      # mode "L": one byte per pixel
    bits = 0
    for r in range(size):
        base = r * (size + 1)
        for c in range(size):
            bits = (bits << 1) | (1 if raw[base + c] < raw[base + c + 1] else 0)
    return bits


def render_photo(seed_path, out_path, persona, when, index, job_id,
                 target_size=None, quality=None, gps=None, fmt="jpeg",
                 jitter=True, with_hash=False):
    """Crop/scale/jitter a seed still and write it with full EXIF.

    `fmt` is "jpeg" or "heic". HEIC goes via a JPEG so the hand-rolled EXIF
    writer stays the single source of metadata for both formats.

    `jitter=False` reuses the same crop as index-1 would produce, which is what
    makes a burst look like a burst rather than a set of unrelated frames.
    """
    rng = _rng(job_id, index)
    src = load_seed(seed_path)
    tw, th = target_size or persona.still_sizes[0]
    if rng.random() < 0.28:                      # portrait orientation
        tw, th = th, tw

    # random crop window, then resample to the target
    scale = rng.uniform(0.32, 1.0)      # wider than the frame it came from
    ar = tw / th
    cw = min(src.width, int(src.width * scale))
    ch = int(cw / ar)
    if ch > src.height:
        ch = src.height
        cw = int(ch * ar)
    x = rng.randint(0, max(0, src.width - cw))
    y = rng.randint(0, max(0, src.height - ch))
    img = src.crop((x, y, x + cw, y + ch)).resize((tw, th), Image.LANCZOS)

    if rng.random() < 0.35:
        img = img.rotate(rng.uniform(-1.5, 1.5), resample=Image.BICUBIC, expand=False)
    if rng.random() < 0.5:
        img = ImageOps.mirror(img)

    # Tone, not just geometry. A phone's auto white balance and exposure are
    # never identical twice, and two crops of one seed that overlap will still
    # diverge once their colour does — which is what stops a finite seed pool
    # producing pictures a dedupe engine would call the same.
    img = ImageEnhance.Color(img).enhance(rng.uniform(0.72, 1.32))
    img = ImageEnhance.Brightness(img).enhance(rng.uniform(0.86, 1.16))
    img = ImageEnhance.Contrast(img).enhance(rng.uniform(0.84, 1.22))

    q = quality if quality is not None else rng.randint(*persona.jpeg_quality)
    iso = int(rng.triangular(persona.iso_range[0], persona.iso_range[1], 120))
    exposure = rng.choice([1 / 30, 1 / 60, 1 / 120, 1 / 250, 1 / 500])

    exif = build_exif(persona, when, tw, th, gps=gps, iso=iso, exposure=exposure)
    jpeg_path = out_path if fmt == "jpeg" else out_path + ".tmp.jpg"
    img.save(jpeg_path, "JPEG", quality=q, exif=exif, subsampling=rng.choice([0, 1, 2]),
             optimize=False, progressive=rng.random() < 0.2)
    if fmt == "heic":
        _to_heic(jpeg_path, out_path, q)
    set_mtime(out_path, when)
    size = os.path.getsize(out_path)
    # Hashed from the pixels as saved, before any JPEG comment padding the size
    # planner may add later — padding changes bytes, never the picture.
    return (size, dhash(img)) if with_hash else size


def _ffmpeg(args):
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
                       capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode()[-800:])


def render_video(out_path, persona, when, index, job_id,
                 width, height, bitrate, duration, seed_clip=None,
                 preset="ultrafast", codec="h264"):
    """Encode an MP4 at a near-exact byte size.

    bytes ~= (video_bitrate + audio_bitrate) * duration / 8, which is why video
    is the knob the size planner uses to land on a target.

    `codec` is "h264" or "hevc". HEVC is what a modern iPhone actually records,
    so it is the video half of closing the v1 format gap.
    """
    rng = _rng(job_id, index)
    abr = 128_000
    # lavfi's `life` and `noise` both default their seed to -1, meaning "pick a
    # random one at run time". Left alone they make every clip different on
    # every run, and CBR hides it: the file size moves by only a few bytes, but
    # the planner prices the next clip off that measured size, so the
    # difference compounds into visibly different later clips. Seeding them
    # from the per-item RNG is what makes --job + --seed actually reproducible
    # for packs containing video.
    noise_seed = rng.randrange(2 ** 31)
    life_seed = rng.randrange(2 ** 32)
    if codec == "hevc":
        # `hvc1` rather than the default `hev1`: QuickTime and iOS Photos will
        # not play an hev1-tagged track, and a clip the gallery cannot render
        # is not much of a test asset.
        vcodec = ["-c:v", "libx265", "-tag:v", "hvc1",
                  "-x265-params",
                  f"log-level=error:vbv-maxrate={bitrate // 1000}:"
                  f"vbv-bufsize={bitrate // 500}"]
    else:
        vcodec = ["-c:v", "libx264",
                  "-x264-params", "nal-hrd=cbr:force-cfr=1"]
    common = [
        *vcodec, "-preset", preset, "-pix_fmt", "yuv420p",
        "-b:v", str(bitrate), "-minrate", str(bitrate), "-maxrate", str(bitrate),
        "-bufsize", str(bitrate * 2),
        "-c:a", "aac", "-b:a", str(abr), "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        "-metadata", f"creation_time={ffmpeg_time(when)}",
        "-metadata", f"make={persona.make}",
        "-metadata", f"model={persona.model}",
        "-t", f"{duration:.3f}",
    ]
    if seed_clip:
        start = rng.uniform(0, 3)
        args = ["-ss", f"{start:.2f}", "-stream_loop", "-1", "-i", seed_clip,
                "-f", "lavfi", "-i", f"sine=frequency={rng.randint(180, 700)}:sample_rate=48000",
                "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                       f"crop={width}:{height},"
                       f"noise=alls={rng.randint(6, 14)}:allf=t:all_seed={noise_seed}",
                "-map", "0:v:0", "-map", "1:a:0", *common, out_path]
    else:
        # Synthetic source: structured motion plus grain encodes to a realistic
        # bitrate, unlike a flat test pattern which would undershoot wildly.
        # Only mandelbrot is reproducible. `testsrc2` and `testsrc` seed an
        # internal PRNG from the clock with no way to override it, and `life`
        # and `cellauto` stay random even when their documented seed option is
        # set — all verified against ffmpeg 9. Variety therefore comes from
        # varying the fractal itself, which is deterministic: a different
        # region at a different zoom looks nothing like the last one and still
        # carries the fine detail that keeps the encoder honest.
        zoom, maxiter = rng.uniform(0.6, 3.2), rng.choice([80, 140, 220, 400])
        src = (f"mandelbrot=size={width}x{height}:rate=30"
               f":start_scale={zoom:.4f}:maxiter={maxiter}"
               f":start_x={rng.uniform(-0.9, 0.4):.6f}"
               f":start_y={rng.uniform(-0.9, 0.9):.6f}")
        args = ["-f", "lavfi", "-i", src,
                "-f", "lavfi", "-i", f"sine=frequency={rng.randint(180, 700)}:sample_rate=48000",
                "-vf", f"noise=alls={rng.randint(8, 18)}:allf=t:all_seed={noise_seed}",
                *common, out_path]
    _ffmpeg(args)
    set_mtime(out_path, when)
    return os.path.getsize(out_path)
