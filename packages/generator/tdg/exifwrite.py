"""EXIF and container metadata.

Two things matter to the apps under test:
  * DateTimeOriginal — what a gallery groups by
  * Make/Model — what a backup client uses to attribute a device

Android also falls back to file mtime when metadata is absent, so every writer
here sets mtime to match. That fallback is real and worth exercising, but it
should agree with the EXIF rather than contradict it.
"""
import os
import struct
from datetime import datetime, timezone

from PIL import Image

# --- minimal TIFF/EXIF assembly -------------------------------------------
# Written by hand rather than pulling piexif so the generator has no
# dependency beyond Pillow. Only the tags that actually matter.

TIFF_ASCII, TIFF_SHORT, TIFF_LONG, TIFF_RATIONAL = 2, 3, 4, 5
TIFF_BYTE, TIFF_UNDEFINED = 1, 7


def _rational(x, denom=10000):
    return (int(round(x * denom)), denom)


def _dms(value):
    value = abs(value)
    d = int(value)
    m = int((value - d) * 60)
    s = (value - d - m / 60) * 3600
    return [(d, 1), (m, 1), (int(round(s * 1000)), 1000)]


class _IFD:
    def __init__(self):
        self.entries = []

    def add(self, tag, typ, values):
        self.entries.append((tag, typ, values))

    def _payload(self, typ, values):
        if typ == TIFF_ASCII:
            b = values.encode("ascii", "replace") + b"\x00"
            return len(b), b
        if typ == TIFF_SHORT:
            if isinstance(values, int):
                values = [values]
            return len(values), b"".join(struct.pack(">H", v) for v in values)
        if typ == TIFF_LONG:
            if isinstance(values, int):
                values = [values]
            return len(values), b"".join(struct.pack(">I", v) for v in values)
        if typ == TIFF_RATIONAL:
            if isinstance(values, tuple) and len(values) == 2 and isinstance(values[0], int):
                values = [values]
            return len(values), b"".join(struct.pack(">II", n, d) for n, d in values)
        if typ in (TIFF_BYTE, TIFF_UNDEFINED):
            return len(values), bytes(values)
        raise ValueError(typ)

    def render(self, offset_base):
        """Return (ifd_bytes, overflow_bytes). offset_base = file offset of ifd start."""
        entries = sorted(self.entries, key=lambda e: e[0])
        n = len(entries)
        ifd_len = 2 + n * 12 + 4
        overflow = b""
        out = struct.pack(">H", n)
        for tag, typ, values in entries:
            count, payload = self._payload(typ, values)
            if len(payload) <= 4:
                payload = payload + b"\x00" * (4 - len(payload))
                out += struct.pack(">HHI", tag, typ, count) + payload
            else:
                ptr = offset_base + ifd_len + len(overflow)
                out += struct.pack(">HHI", tag, typ, count) + struct.pack(">I", ptr)
                overflow += payload
                if len(overflow) % 2:
                    overflow += b"\x00"
        out += struct.pack(">I", 0)  # no next IFD
        return out, overflow


def offset_string(when: datetime) -> str:
    """'+09:00' for EXIF OffsetTime tags. Empty for a naive datetime."""
    off = when.utcoffset()
    if off is None:
        return ""
    total = int(off.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


def build_exif(persona, when: datetime, width: int, height: int,
               gps=None, iso=200, exposure=1 / 120):
    """Return APP1 EXIF bytes suitable for Pillow's exif= argument.

    ``when`` should be timezone-aware. DateTimeOriginal is wall-clock with no
    zone of its own, so without the accompanying OffsetTimeOriginal tag an
    importer falls back to its own timezone and the asset lands at a different
    absolute instant on every machine that imports it.
    """
    ts = when.strftime("%Y:%m:%d %H:%M:%S")
    subsec = f"{when.microsecond // 1000:03d}"
    offset = offset_string(when)

    ifd0 = _IFD()
    ifd0.add(0x010F, TIFF_ASCII, persona.make)
    ifd0.add(0x0110, TIFF_ASCII, persona.model)
    ifd0.add(0x0112, TIFF_SHORT, 1)                      # Orientation: normal
    ifd0.add(0x0131, TIFF_ASCII, persona.software)
    ifd0.add(0x0132, TIFF_ASCII, ts)                     # DateTime
    ifd0.add(0x011A, TIFF_RATIONAL, (72, 1))
    ifd0.add(0x011B, TIFF_RATIONAL, (72, 1))
    ifd0.add(0x0128, TIFF_SHORT, 2)

    exif = _IFD()
    exif.add(0x829A, TIFF_RATIONAL, _rational(exposure, 100000))   # ExposureTime
    exif.add(0x829D, TIFF_RATIONAL, _rational(persona.f_number, 100))
    exif.add(0x8827, TIFF_SHORT, int(iso))                          # ISO
    exif.add(0x9000, TIFF_UNDEFINED, b"0232")                       # ExifVersion
    exif.add(0x9003, TIFF_ASCII, ts)                                # DateTimeOriginal
    exif.add(0x9004, TIFF_ASCII, ts)                                # DateTimeDigitized
    exif.add(0x9291, TIFF_ASCII, subsec)                            # SubSecTimeOriginal
    if offset:
        # EXIF 2.31 added these. Without them DateTimeOriginal is ambiguous.
        exif.add(0x9010, TIFF_ASCII, offset)                        # OffsetTime
        exif.add(0x9011, TIFF_ASCII, offset)                        # OffsetTimeOriginal
        exif.add(0x9012, TIFF_ASCII, offset)                        # OffsetTimeDigitized
    exif.add(0x920A, TIFF_RATIONAL, _rational(persona.focal_length, 100))
    exif.add(0xA002, TIFF_LONG, width)
    exif.add(0xA003, TIFF_LONG, height)
    exif.add(0xA434, TIFF_ASCII, persona.lens)                      # LensModel
    exif.add(0xA433, TIFF_ASCII, persona.make)                      # LensMake

    gpsifd = None
    if gps:
        lat, lon = gps
        gpsifd = _IFD()
        gpsifd.add(0x0000, TIFF_BYTE, [2, 3, 0, 0])
        gpsifd.add(0x0001, TIFF_ASCII, "N" if lat >= 0 else "S")
        gpsifd.add(0x0002, TIFF_RATIONAL, _dms(lat))
        gpsifd.add(0x0003, TIFF_ASCII, "E" if lon >= 0 else "W")
        gpsifd.add(0x0004, TIFF_RATIONAL, _dms(lon))
        # GPSTimeStamp and GPSDateStamp are defined as UTC, not local time.
        # Writing wall-clock here contradicts DateTimeOriginal + OffsetTime and
        # is exactly the kind of inconsistency a metadata-reading app trips on.
        utc = when.astimezone(timezone.utc) if when.utcoffset() is not None else when
        gpsifd.add(0x0007, TIFF_RATIONAL,
                   [(utc.hour, 1), (utc.minute, 1), (utc.second, 1)])
        gpsifd.add(0x001D, TIFF_ASCII, utc.strftime("%Y:%m:%d"))

    # Layout: header(8) | IFD0 | IFD0 overflow | EXIF IFD | overflow | GPS IFD | overflow
    header = b"MM\x00\x2a" + struct.pack(">I", 8)

    # Two-pass: we must know sub-IFD offsets before rendering IFD0.
    ifd0.add(0x8769, TIFF_LONG, 0)          # placeholder ExifIFDPointer
    if gpsifd is not None:
        ifd0.add(0x8825, TIFF_LONG, 0)      # placeholder GPSIFDPointer

    ifd0_bytes, ifd0_over = ifd0.render(8)
    exif_off = 8 + len(ifd0_bytes) + len(ifd0_over)
    exif_bytes, exif_over = exif.render(exif_off)
    gps_off = exif_off + len(exif_bytes) + len(exif_over)
    if gpsifd is not None:
        gps_bytes, gps_over = gpsifd.render(gps_off)
    else:
        gps_bytes = gps_over = b""

    # Re-render IFD0 with the real pointers.
    ifd0.entries = [e for e in ifd0.entries if e[0] not in (0x8769, 0x8825)]
    ifd0.add(0x8769, TIFF_LONG, exif_off)
    if gpsifd is not None:
        ifd0.add(0x8825, TIFF_LONG, gps_off)
    ifd0_bytes, ifd0_over = ifd0.render(8)

    tiff = header + ifd0_bytes + ifd0_over + exif_bytes + exif_over + gps_bytes + gps_over
    # JPEG APP1 payload must carry the "Exif\0\0" identifier; Pillow writes the
    # segment verbatim and readers reject it without the prefix.
    return b"Exif\x00\x00" + tiff


def set_mtime(path, when: datetime):
    """Set mtime to the capture instant.

    An aware datetime gives the same absolute instant on every machine. A naive
    one would be read as the build host's local time, which is how a pack ends
    up with mtimes that disagree with its own EXIF.
    """
    ts = when.timestamp()
    os.utime(path, (ts, ts))


def ffmpeg_time(when: datetime) -> str:
    """ffmpeg creation_time wants ISO-8601 UTC.

    MP4 has no local-time-plus-offset form the way EXIF does: the container
    stores an absolute instant. So this is where a naive datetime does real
    damage — it would be labelled UTC while the JPEGs beside it are labelled
    local, putting photos and videos in the same pack hours apart.
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
