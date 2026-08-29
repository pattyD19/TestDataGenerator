"""Bootstrap seed pool — synthetic stills generated locally.

The real seed pool is harvested from open-license sources (see harvest.py).
This module exists so the whole pipeline runs with no network at all, which
matters for two reasons: development machines behind a restrictive egress
policy, and CI.

Synthetic seeds are deliberately *textured*. A smooth gradient compresses to a
few KB and would make every size estimate a lie, so each image layers a
low-frequency colour field, mid-frequency structure and fine grain — which
compresses within the same order of magnitude as a real photograph.
"""
import json
import math
import os
import random

import numpy as np
from PIL import Image

PALETTES = [
    ((28, 46, 74), (198, 168, 122), (240, 238, 230)),
    ((12, 58, 52), (176, 204, 142), (250, 246, 228)),
    ((72, 24, 38), (214, 118, 74), (246, 226, 200)),
    ((38, 38, 46), (120, 138, 168), (228, 234, 242)),
    ((16, 42, 30), (92, 140, 96), (222, 232, 210)),
    ((58, 40, 16), (206, 156, 70), (244, 234, 208)),
]


def _fbm(h, w, rng, octaves=5, persistence=0.55):
    """Fractal noise by successive upsampling — cheap and photo-like."""
    out = np.zeros((h, w), dtype=np.float32)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        gh = max(2, int(h / (2 ** (octaves - o))))
        gw = max(2, int(w / (2 ** (octaves - o))))
        layer = rng.random((gh, gw)).astype(np.float32)
        img = Image.fromarray((layer * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
        out += amp * (np.asarray(img, dtype=np.float32) / 255.0)
        total += amp
        amp *= persistence
    return out / total


def synth_still(width, height, seed):
    rng = np.random.default_rng(seed)
    py_rng = random.Random(seed)
    dark, mid, light = py_rng.choice(PALETTES)

    base = _fbm(height, width, rng, octaves=6)

    # mid-frequency structure: a few soft bands at an arbitrary angle
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    ang = py_rng.uniform(0, math.pi)
    freq = py_rng.uniform(2.0, 7.0)
    bands = 0.5 + 0.5 * np.sin(
        (xx * math.cos(ang) + yy * math.sin(ang)) / max(width, height) * freq * math.tau
    )
    field = np.clip(0.62 * base + 0.38 * bands, 0, 1)

    # map through the palette
    dark = np.array(dark, np.float32)
    mid = np.array(mid, np.float32)
    light = np.array(light, np.float32)
    f = field[..., None]
    lower = dark + (mid - dark) * np.clip(f * 2, 0, 1)
    upper = mid + (light - mid) * np.clip(f * 2 - 1, 0, 1)
    rgb = np.where(f < 0.5, lower, upper)

    # fine grain — this is what keeps JPEG sizes honest
    grain = rng.normal(0, 6.5, size=(height, width, 1)).astype(np.float32)
    chroma = rng.normal(0, 2.5, size=(height, width, 3)).astype(np.float32)
    rgb = np.clip(rgb + grain + chroma, 0, 255).astype(np.uint8)
    return Image.fromarray(rgb, "RGB")


def build_bootstrap_pool(out_dir, count=48, size=(4032, 3024), seed=1):
    """Write `count` synthetic seed stills plus a seeds.json describing them."""
    os.makedirs(out_dir, exist_ok=True)
    entries = []
    for i in range(count):
        name = f"synth_{i:04d}.png"
        path = os.path.join(out_dir, name)
        if not os.path.exists(path):
            synth_still(size[0], size[1], seed * 10_000 + i).save(path, "PNG", compress_level=1)
        entries.append({
            "file": name,
            "kind": "image",
            "source": "synthetic",
            "author": "TDG bootstrap generator",
            "license": "CC0-1.0",
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            "origin_url": None,
        })
    with open(os.path.join(out_dir, "seeds.json"), "w") as fh:
        json.dump({"version": 1, "seeds": entries}, fh, indent=2)
    return entries
