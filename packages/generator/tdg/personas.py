"""Device personas — the camera identity stamped into generated media.

Kept deliberately small and data-only so adding a device is a dict, not code.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Persona:
    key: str
    make: str
    model: str
    lens: str
    software: str
    # (width, height) of the main camera's stills, landscape orientation
    still_sizes: tuple = ((4032, 3024),)
    # (width, height, bitrate_bps) video modes
    video_modes: tuple = ((3840, 2160, 45_000_000), (1920, 1080, 16_000_000))
    # typical JPEG quality band this device writes
    jpeg_quality: tuple = (82, 92)
    f_number: float = 1.8
    focal_length: float = 6.86
    iso_range: tuple = (32, 800)

    @property
    def label(self) -> str:
        return f"{self.make} {self.model}"


PERSONAS = {
    p.key: p
    for p in [
        Persona(
            key="iphone-15-pro",
            make="Apple",
            model="iPhone 15 Pro",
            lens="iPhone 15 Pro back triple camera 6.86mm f/1.78",
            software="17.5.1",
            still_sizes=((4032, 3024), (8064, 6048)),
            video_modes=((3840, 2160, 45_000_000), (1920, 1080, 16_000_000)),
            jpeg_quality=(84, 93),
            f_number=1.78,
            focal_length=6.86,
        ),
        Persona(
            key="pixel-8",
            make="Google",
            model="Pixel 8",
            lens="Pixel 8 back camera 6.9mm f/1.68",
            software="google/shiba/shiba:14",
            still_sizes=((4080, 3072), (2048, 1536)),
            video_modes=((3840, 2160, 42_000_000), (1920, 1080, 15_000_000)),
            jpeg_quality=(80, 92),
            f_number=1.68,
            focal_length=6.9,
        ),
        Persona(
            key="galaxy-s24",
            make="samsung",
            model="SM-S921B",
            lens="Samsung Galaxy S24 main camera 6.3mm f/1.8",
            software="S921BXXU1AXBA",
            still_sizes=((4000, 3000), (8160, 6120)),
            video_modes=((3840, 2160, 48_000_000), (1920, 1080, 17_000_000)),
            jpeg_quality=(78, 90),
            f_number=1.8,
            focal_length=6.3,
        ),
        Persona(
            key="galaxy-a54",
            make="samsung",
            model="SM-A546B",
            lens="Samsung Galaxy A54 main camera 5.4mm f/1.8",
            software="A546BXXU7BXA1",
            still_sizes=((4000, 3000),),
            video_modes=((1920, 1080, 14_000_000),),
            jpeg_quality=(72, 86),
            f_number=1.8,
            focal_length=5.4,
        ),
    ]
}

# Rough city anchors for GPS jitter. Nothing precise — enough to exercise
# location grouping and reverse-geocoding paths.
CITIES = [
    ("Bridgewater NJ", 40.5940, -74.6046),
    ("New York NY", 40.7128, -74.0060),
    ("Dublin IE", 53.3498, -6.2603),
    ("Toronto CA", 43.6532, -79.3832),
    ("Lisbon PT", 38.7223, -9.1393),
    ("Seoul KR", 37.5665, 126.9780),
    ("Denver CO", 39.7392, -104.9903),
    ("Bengaluru IN", 12.9716, 77.5946),
]
