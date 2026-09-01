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
import urllib.error
import urllib.parse
import urllib.request

# stdlib only, like the rest of the generator. This module used `requests`,
# which meant the one tool that needs a network could not run on a machine
# where pip is externally managed — exactly the machine you want to harvest on.
UA = "TDG-SeedHarvester/0.1 (mobile test data generator; contact: platform@example.com)"

BLENDER_CLIPS = [
    ("https://download.blender.org/peach/bigbuckbunny_movies/BigBuckBunny_320x180.mp4",
     "Big Buck Bunny", "Blender Foundation", "CC-BY-3.0"),
    ("https://download.blender.org/durian/trailer/sintel_trailer-720p.mp4",
     "Sintel trailer", "Blender Foundation", "CC-BY-3.0"),
]


def _open(url):
    """A response, retrying on the documented rate limit and on transient
    network errors. The caller closes it; every use here is inside a `with`."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            return urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except urllib.error.URLError:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def _get_json(url):
    with _open(url) as r:
        return json.loads(r.read().decode())


def _download(url, dest):
    with _open(url) as r, open(dest, "wb") as fh:
        while True:                       # a master must not be buffered whole
            chunk = r.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
    return os.path.getsize(dest)


def from_openverse(out_dir, count=120, query="landscape", cc0_only=True):
    """Openverse. No key needed; a key raises the rate limit."""
    entries, page = [], 1
    while len(entries) < count:
        params = {"q": query, "page_size": 50, "page": page, "mature": "false"}
        if cc0_only:
            params["license"] = "cc0,pdm"
        results = _get_json("https://api.openverse.org/v1/images/?"
                            + urllib.parse.urlencode(params)).get("results", [])
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
        data = _get_json(api + "?" + urllib.parse.urlencode(params))
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


def harvest(out_dir, images=200, videos=False, query="landscape", cc0_only=True):
    """Fetch seed stills, and optionally clips.

    `videos` defaults off. A harvested clip makes render_video seek into real
    footage, and that seek is not frame-reproducible — which silently costs the
    byte-for-byte rebuild that --job plus --seed otherwise guarantees. Two
    suites catch it, but the default should not need catching.
    """
    os.makedirs(out_dir, exist_ok=True)
    entries = []
    # Openverse first, then Wikimedia for whatever is still missing. The split
    # used to be fixed up front, so a blocked source cost half the pool in
    # silence — when Openverse began requiring a key, asking for 200 produced
    # 100 and said nothing. Wikimedia paginates, so it can cover the shortfall.
    try:
        entries += from_openverse(out_dir, images // 2, query, cc0_only)
    except Exception as exc:                           # a blocked source is not fatal
        print(f"  ! from_openverse failed: {exc}")
    if len(entries) < images:
        try:
            entries += from_wikimedia(out_dir, images - len(entries))
        except Exception as exc:
            print(f"  ! from_wikimedia failed: {exc}")
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
