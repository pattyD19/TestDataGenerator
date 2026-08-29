#!/usr/bin/env python3
"""Generate the TDG mark for every platform from one definition.

Two sheets of media, stacked. The back one half-visible behind the front, and a
photo glyph on the front — the product makes *files*, and specifically photos
and video, so the mark says both.

There is no SVG rasteriser on the build machines, so the geometry lives here in
normalised 0..1 space and is emitted twice: drawn with Pillow for the PNGs the
app stores need, and written out as SVG for the web. One definition, so the
favicon and the app icon cannot drift apart.

    python3 assets/make_logo.py

Everything it writes is committed, so nobody needs to run it to build.
"""
import os
import sys
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# The web UI's accent, so the icon and the control plane are obviously the same
# product.
GREEN = (47, 93, 80)
GREEN_DEEP = (33, 68, 58)
WHITE = (255, 255, 255)
# Opaque pale sage rather than translucent white. Translucency looked right
# against the green background but made the two sheets merge into one shape —
# the whole point of the mark is that there is more than one file.
SHEET_BACK = (170, 202, 189, 255)
GLYPH = (47, 93, 80)

# --- geometry, in a 0..1 square -------------------------------------------
BACK_SHEET = (0.250, 0.150, 0.660, 0.700)
FRONT_SHEET = (0.340, 0.300, 0.750, 0.850)
SHEET_R = 0.045
BG_R = 0.225                            # rounded-square background

# How far to shrink the mark for Android's adaptive-icon foreground.
#
# The documented safe zone is the middle 66% of the canvas, but that is a
# *circle*, not a square: a Pixel launcher masks to a circle, so it is the
# mark's diagonal that has to fit, not its width. Sizing to the square clipped
# the bottom of the front sheet on a real emulator.
#
#   half-diagonal at full size = hypot(0.25, 0.35) = 0.430
#   circular mask radius       = 0.333
#   max scale                  = 0.333 / 0.430 = 0.774
ADAPTIVE_SCALE = 0.74                   # 0.774 with a little margin


def _lerp(a, b, t):
    return a + (b - a) * t


def _glyph(front):
    """A mountain and a sun, sized to the front sheet."""
    x0, y0, x1, y1 = front
    w, h = x1 - x0, y1 - y0
    pad = 0.17
    ix0, iy0 = x0 + w * pad, y0 + h * pad
    ix1, iy1 = x1 - w * pad, y1 - h * pad
    iw, ih = ix1 - ix0, iy1 - iy0
    sun = (ix0 + iw * 0.20, iy0 + ih * 0.16, iw * 0.13)          # cx, cy, r
    mountain = [
        (ix0, iy1),
        (ix0 + iw * 0.40, iy0 + ih * 0.42),
        (ix0 + iw * 0.62, iy1),
    ]
    hill = [
        (ix0 + iw * 0.42, iy1),
        (ix0 + iw * 0.74, iy0 + ih * 0.60),
        (ix1, iy1),
    ]
    return sun, mountain, hill


def draw(size, background=True, content_scale=1.0):
    """Render the mark at `size` px.

    `content_scale` shrinks the sheets toward the centre for Android's adaptive
    icons. See ADAPTIVE_SCALE for why the number is what it is.
    """
    ss = 4                                   # supersample; Pillow has no AA
    n = size * ss
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def pt(x, y):
        cx = _lerp(0.5, x, content_scale)
        cy = _lerp(0.5, y, content_scale)
        return cx * n, cy * n

    def box(rect):
        x0, y0, x1, y1 = rect
        a, b = pt(x0, y0)
        c, e = pt(x1, y1)
        return [a, b, c, e]

    if background:
        d.rounded_rectangle([0, 0, n - 1, n - 1], radius=BG_R * n, fill=GREEN)

    # Back sheet first, so the front overlaps it.
    d.rounded_rectangle(box(BACK_SHEET), radius=SHEET_R * n * content_scale,
                        fill=SHEET_BACK)
    d.rounded_rectangle(box(FRONT_SHEET), radius=SHEET_R * n * content_scale,
                        fill=WHITE)

    sun, mountain, hill = _glyph(FRONT_SHEET)
    cx, cy, r = sun
    sx, sy = pt(cx, cy)
    rr = r * n * content_scale
    d.ellipse([sx - rr, sy - rr, sx + rr, sy + rr], fill=GLYPH)
    d.polygon([pt(*p) for p in hill], fill=GLYPH)
    d.polygon([pt(*p) for p in mountain], fill=GLYPH)

    return img.resize((size, size), Image.LANCZOS)


def svg():
    """The same geometry as SVG, for the web."""
    def r(rect, radius, fill, extra=""):
        x0, y0, x1, y1 = rect
        return (f'  <rect x="{x0*100:.2f}" y="{y0*100:.2f}" '
                f'width="{(x1-x0)*100:.2f}" height="{(y1-y0)*100:.2f}" '
                f'rx="{radius*100:.2f}" fill="{fill}"{extra}/>')

    def hexcol(c):
        return "#%02x%02x%02x" % c

    sun, mountain, hill = _glyph(FRONT_SHEET)
    cx, cy, rad = sun
    poly = lambda p: " ".join(f"{x*100:.2f},{y*100:.2f}" for x, y in p)
    return "\n".join([
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
        'role="img" aria-label="TDG">',
        '  <title>TDG</title>',
        f'  <rect width="100" height="100" rx="{BG_R*100:.2f}" '
        f'fill="{hexcol(GREEN)}"/>',
        r(BACK_SHEET, SHEET_R, hexcol(SHEET_BACK[:3])),
        r(FRONT_SHEET, SHEET_R, "#ffffff"),
        f'  <circle cx="{cx*100:.2f}" cy="{cy*100:.2f}" r="{rad*100:.2f}" '
        f'fill="{hexcol(GLYPH)}"/>',
        f'  <polygon points="{poly(hill)}" fill="{hexcol(GLYPH)}"/>',
        f'  <polygon points="{poly(mountain)}" fill="{hexcol(GLYPH)}"/>',
        '</svg>',
        '',
    ])


def write(path, img):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)
    print(f"  {os.path.relpath(path, REPO)}  {img.size[0]}px")


def main():
    print("master")
    master = os.path.join(HERE, "logo.svg")
    with open(master, "w") as fh:
        fh.write(svg())
    print(f"  {os.path.relpath(master, REPO)}")
    write(os.path.join(HERE, "logo-1024.png"), draw(1024))

    # ---- iOS: one 1024 icon; Xcode derives the rest ----------------------
    print("iOS")
    ios = os.path.join(REPO, "clients/ios/TdgLoader/Assets.xcassets/AppIcon.appiconset")
    write(os.path.join(ios, "icon-1024.png"), draw(1024).convert("RGB"))

    # ---- Android: legacy mipmaps plus an adaptive foreground -------------
    print("Android")
    res = os.path.join(REPO, "clients/android/app/src/main/res")
    for bucket, px in [("mdpi", 48), ("hdpi", 72), ("xhdpi", 96),
                       ("xxhdpi", 144), ("xxxhdpi", 192)]:
        icon = draw(px)
        write(os.path.join(res, f"mipmap-{bucket}", "ic_launcher.png"), icon)
        write(os.path.join(res, f"mipmap-{bucket}", "ic_launcher_round.png"), icon)
        # 108dp foreground for the adaptive icon, content inside the safe zone.
        fg = int(px * 108 / 48)
        write(os.path.join(res, f"mipmap-{bucket}", "ic_launcher_foreground.png"),
              draw(fg, background=False, content_scale=ADAPTIVE_SCALE))

    # ---- web ------------------------------------------------------------
    print("web")
    static = os.path.join(REPO, "packages/web/tdgweb/static")
    with open(os.path.join(static, "icon.svg"), "w") as fh:
        fh.write(svg())
    print(f"  {os.path.relpath(os.path.join(static, 'icon.svg'), REPO)}")
    write(os.path.join(static, "icon-180.png"), draw(180))   # apple-touch-icon
    write(os.path.join(static, "favicon-32.png"), draw(32))

    print("\ndone")


if __name__ == "__main__":
    sys.exit(main())
