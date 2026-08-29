"""Exact byte-size control.

JPEG size is content-dependent, so the generator measures rather than
estimates. Two mechanisms land a pack on its target:

  * video duration — bytes ~= (video_bitrate + audio_bitrate) * duration / 8,
    accurate to a few KB under CBR, so a trim clip absorbs most of the deficit
  * JPEG COM padding — a JPEG may carry arbitrary comment segments, which
    decoders ignore. Inserting them after SOI makes the final file land on an
    exact byte count without corrupting the image.
"""
import os

SOI = b"\xff\xd8"
COM = b"\xff\xfe"
MAX_COM_PAYLOAD = 65533 - 2  # segment length field counts itself


def pad_jpeg_to(path, target_bytes):
    """Grow a JPEG to exactly target_bytes using COM segments. Returns actual size.

    A shortfall of 1-3 bytes cannot be expressed (the smallest segment is 4
    bytes), so the caller gets back what was achievable and records the delta.
    """
    size = os.path.getsize(path)
    need = target_bytes - size
    if need <= 0:
        return size
    if need < 4:
        return size

    segments = []
    while need > 0:
        if need < 4:
            # Fold the remainder into the previous segment rather than leaving
            # an unrepresentable tail.
            if segments:
                grow = min(need, MAX_COM_PAYLOAD - len(segments[-1]))
                segments[-1] += b"\x00" * grow
                need -= grow
            break
        payload = min(need - 4, MAX_COM_PAYLOAD)
        segments.append(b"\x00" * payload)
        need -= payload + 4

    with open(path, "rb") as fh:
        data = fh.read()
    assert data[:2] == SOI, f"{path} is not a JPEG"
    blob = b"".join(COM + (len(s) + 2).to_bytes(2, "big") + s for s in segments)
    with open(path, "wb") as fh:
        fh.write(SOI + blob + data[2:])
    return os.path.getsize(path)


def parse_size(text):
    """'25GB', '512MB', '1.5 gb', '900000000' -> bytes."""
    t = str(text).strip().lower().replace(" ", "")
    mult = 1
    for suffix, m in (("tb", 1 << 40), ("gb", 1 << 30), ("mb", 1 << 20), ("kb", 1 << 10),
                      ("t", 1 << 40), ("g", 1 << 30), ("m", 1 << 20), ("k", 1 << 10)):
        if t.endswith(suffix):
            mult = m
            t = t[: -len(suffix)]
            break
    return int(float(t) * mult)


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:,.1f} {unit}" if unit != "B" else f"{n:,.0f} B"
        n /= 1024
