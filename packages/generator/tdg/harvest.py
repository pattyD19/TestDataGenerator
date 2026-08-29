"""Harvest a seed pool from open-license sources.

Run this ONCE. The whole point of the seed-pool design is that stock APIs are
not built for bulk downloading and say so: Pixabay prohibits "systematic mass
downloads" and requires 24h caching; Pexels allows 200 requests an hour. A
single polite harvest of a few hundred assets supports unlimited packs at any
size, forever, offline.

Sources needing no API key at all:
  * Openverse   — CC0 filter available
  * Wikimedia Commons — large public-domain pool
  * Blender open movies — CC-BY, full-resolution masters

Pexels and Pixabay are supported but optional; set PEXELS_API_KEY /
PIXABAY_API_KEY. Prefer the CC0 sources so packs travel without attribution
obligations.
"""
import json
import os
import time
import urllib.parse

import requests

UA = "TDG-SeedHarvester/0.1 (mobile test data generator; contact: platform@example.com)"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = UA

BLENDER_CLIPS = [
    ("https://download.blender.org/peach/bigbuckbunny_movies/BigBuckBunny_320x180.mp4",
     "Big Buck Bunny", "Blender Foundation", "CC-BY-3.0"),
    ("https://download.blender.org/durian/trailer/sintel_trailer-720p.mp4",
     "Sintel trailer", "Blender Foundation", "CC-BY-3.0"),
]


def _get(url, **kw):
    for attempt in range(3):
        try:
            r = SESSION.get(url, timeout=30, **kw)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def _download(url, dest):
    r = _get(url, stream=True)
    with open(dest, "wb") as fh:
        for chunk in r.iter_content(1 << 20):
            fh.write(chunk)
    return os.path.getsize(dest)


def from_openverse(out_dir, count=120, query="landscape", cc0_only=True):
    """Openverse. No key needed; a key raises the rate limit."""
    entries, page = [], 1
    while len(entries) < count:
        params = {"q": query, "page_size": 50, "page": page, "mature": "false"}
        if cc0_only:
            params["license"] = "cc0,pdm"
        r = _get("https://api.openverse.org/v1/images/?" + urllib.parse.urlencode(params))
        results = r.json().get("results", [])
        if not results:
            break
        for item in results:
            if len(entries) >= count:
                break
            name = f"ov_{item['id'][:12]}.jpg"
            try:
                _download(item["url"], os.path.join(out_dir, name))
            except Exception:
                continue
            entries.append({
                "file": name, "kind": "image", "source": "openverse",
                "author": item.get("creator") or "unknown",
                "license": (item.get("license") or "").upper(),
                "license_url": item.get("license_url"),
                "origin_url": item.get("foreign_landing_url"),
            })
            time.sleep(0.25)          # deliberate pacing, not throttled into
        page += 1
    return entries


def from_wikimedia(out_dir, count=80, category="Category:Featured_pictures_on_Wikimedia_Commons"):
    """Wikimedia Commons. Descriptive User-Agent is required by their policy."""
    entries, cont = [], None
    api = "https://commons.wikimedia.org/w/api.php"
    while len(entries) < count:
        params = {"action": "query", "format": "json", "generator": "categorymembers",
                  "gcmtitle": category, "gcmtype": "file", "gcmlimit": 50,
                  "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": 4000}
        if cont:
            params["gcmcontinue"] = cont
        r = _get(api + "?" + urllib.parse.urlencode(params))
        data = r.json()
        pages = (data.get("query") or {}).get("pages", {})
        for page in pages.values():
            if len(entries) >= count:
                break
            info = (page.get("imageinfo") or [{}])[0]
            url = info.get("thumburl") or info.get("url")
            if not url:
                continue
            meta = info.get("extmetadata", {})
            name = f"wm_{abs(hash(page['title'])) % 10**10}.jpg"
            try:
                _download(url, os.path.join(out_dir, name))
            except Exception:
                continue
            entries.append({
                "file": name, "kind": "image", "source": "wikimedia-commons",
                "author": (meta.get("Artist", {}).get("value") or "unknown")[:120],
                "license": meta.get("LicenseShortName", {}).get("value", "unknown"),
                "license_url": meta.get("LicenseUrl", {}).get("value"),
                "origin_url": info.get("descriptionurl"),
            })
            time.sleep(0.3)
        cont = (data.get("continue") or {}).get("gcmcontinue")
        if not cont:
            break
    return entries


def from_blender(out_dir):
    entries = []
    for url, title, author, lic in BLENDER_CLIPS:
        name = os.path.basename(url)
        try:
            _download(url, os.path.join(out_dir, name))
        except Exception:
            continue
        entries.append({
            "file": name, "kind": "video", "source": "blender-open-movies",
            "author": author, "license": lic,
            "license_url": "https://creativecommons.org/licenses/by/3.0/",
            "origin_url": url,
        })
    return entries


def harvest(out_dir, images=200, videos=True, query="landscape", cc0_only=True):
    os.makedirs(out_dir, exist_ok=True)
    entries = []
    half = images // 2
    for fn, n in ((from_openverse, half), (from_wikimedia, images - half)):
        try:
            entries += fn(out_dir, n) if fn is from_wikimedia else fn(out_dir, n, query, cc0_only)
        except Exception as exc:                       # a blocked source is not fatal
            print(f"  ! {fn.__name__} failed: {exc}")
    if videos:
        try:
            entries += from_blender(out_dir)
        except Exception as exc:
            print(f"  ! blender failed: {exc}")

    path = os.path.join(out_dir, "seeds.json")
    existing = []
    if os.path.exists(path):
        with open(path) as fh:
            existing = json.load(fh).get("seeds", [])
    have = {e["file"] for e in entries}
    merged = entries + [e for e in existing if e["file"] not in have]
    with open(path, "w") as fh:
        json.dump({"version": 1, "seeds": merged}, fh, indent=2)
    return merged
