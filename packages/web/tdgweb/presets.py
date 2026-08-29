"""Presets and request validation.

The form is the part of the tool most people will touch, so the defaults have
to be the useful ones and the validation has to reject nonsense before a
two-hour build starts rather than after it.
"""
import os
import sys
import uuid
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "generator")))

from tdg import sizing                      # noqa: E402
from tdg.personas import PERSONAS           # noqa: E402

# The sizes people actually ask for, from the plan's own table.
PRESETS = [
    {"key": "smoke", "label": "Smoke test", "size": "500MB",
     "photo_fraction": 0.7,
     "hint": "A couple of minutes. Proves a device path end to end."},
    {"key": "small", "label": "Small library", "size": "5GB",
     "photo_fraction": 0.7,
     "hint": "~1,000 photos and a handful of clips."},
    {"key": "typical", "label": "Typical phone", "size": "25GB",
     "photo_fraction": 0.7,
     "hint": "~5,100 photos, 32 clips. The everyday case."},
    {"key": "full", "label": "Full device", "size": "64GB",
     "photo_fraction": 0.7,
     "hint": "~13,100 photos. Hours to build; run it on the LAN box."},
    {"key": "many-small", "label": "Many small files", "size": "5GB",
     "photo_fraction": 1.0,
     "hint": "Stills only. File count, not bytes, is what strains a backup client."},
    {"key": "video-heavy", "label": "Video heavy", "size": "25GB",
     "photo_fraction": 0.2,
     "hint": "Mostly 4K clips — the slow, large-file path."},
]

MAX_BYTES = 512 * (1 << 30)          # a sanity ceiling, not a device limit


DEFAULT_PROFILE = "iphone-15-pro"

# `persona.model` is what goes in the EXIF, so it is a part number for the
# Samsungs — "SM-A546B" tells you nothing in a dropdown. These are display
# names only; the generator keeps owning what the file actually says.
PROFILE_NAMES = {
    "iphone-15-pro": "iPhone 15 Pro",
    "pixel-8": "Pixel 8",
    "galaxy-s24": "Galaxy S24",
    "galaxy-a54": "Galaxy A54 (budget tier)",
}


def profiles():
    out = []
    for key, p in sorted(PERSONAS.items()):
        name = PROFILE_NAMES.get(key, p.model)
        out.append({
            "key": key,
            "label": name if name == p.model else f"{name} — {p.model}",
            "default": key == DEFAULT_PROFILE,
        })
    return out


def job_slug():
    return uuid.uuid4().hex[:6]


def _date(value, field):
    if value in (None, ""):
        return None
    try:
        datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an ISO date such as 2023-01-01")
    return value


def normalise(body):
    """Validate a create-job request. Returns (params, target_bytes)."""
    if not isinstance(body, dict):
        raise ValueError("expected a JSON object")

    size = body.get("size")
    if not size:
        raise ValueError("size is required, e.g. '25GB'")
    try:
        target = sizing.parse_size(size)
    except (ValueError, TypeError):
        raise ValueError(f"could not read a size from {size!r}")
    if target <= 0:
        raise ValueError("size must be greater than zero")
    if target > MAX_BYTES:
        raise ValueError(f"size above the {sizing.human(MAX_BYTES)} ceiling")

    profile = body.get("profile", "iphone-15-pro")
    if profile not in PERSONAS:
        raise ValueError(f"unknown profile {profile!r}; "
                         f"one of {', '.join(sorted(PERSONAS))}")

    try:
        fraction = float(body.get("photo_fraction", 0.70))
    except (TypeError, ValueError):
        raise ValueError("photo_fraction must be a number between 0 and 1")
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("photo_fraction must be between 0 and 1")

    since = _date(body.get("since"), "since")
    until = _date(body.get("until"), "until")
    if since and until and datetime.fromisoformat(since) >= datetime.fromisoformat(until):
        raise ValueError("since must be earlier than until")

    params = {
        "size": size,
        "profile": profile,
        "photo_fraction": fraction,
        "since": since,
        "until": until,
        "preset": body.get("preset", "ultrafast"),
        "seed": int(body.get("seed", 1)),
        "min_clip": body.get("min_clip"),
        "max_clip": body.get("max_clip"),
        "jobs": body.get("jobs"),
        "label": (body.get("label") or "").strip()[:80],
    }
    return params, target
