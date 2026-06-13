"""
Geometry / geodesy helpers for the commercial pipeline.

Kept dependency-light: pyproj for CRS transforms (already available via geopandas),
shapely for footprint geometry, numpy for vectorised nearest-postcode lookups.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

try:
    from pyproj import Transformer
    _BNG_TO_WGS = Transformer.from_crs(27700, 4326, always_xy=True)
    _WGS_TO_BNG = Transformer.from_crs(4326, 27700, always_xy=True)
except Exception:  # pragma: no cover - pyproj always present with geopandas
    _BNG_TO_WGS = _WGS_TO_BNG = None


def bng_to_wgs(easting: float, northing: float) -> tuple[float, float]:
    """British National Grid (EPSG:27700) → (lat, lon)."""
    lon, lat = _BNG_TO_WGS.transform(easting, northing)
    return lat, lon


def wgs_to_bng(lat: float, lon: float) -> tuple[float, float]:
    """(lat, lon) → BNG (easting, northing)."""
    e, n = _WGS_TO_BNG.transform(lon, lat)
    return e, n


def os_grid_ref(easting: float, northing: float, figures: int = 6) -> str:
    """
    Convert BNG easting/northing to an OS alphanumeric grid reference
    (e.g. 'SP 513 062'). Lets installers locate a lead even when the
    postcode/address is unresolved. Honest fallback identity.

    Standard OSGB36 lettered-grid algorithm (two letters + numeric eastings/northings),
    skipping the unused letter 'I'.
    """
    if easting is None or northing is None:
        return ""
    try:
        e, n = int(easting), int(northing)
        e100, n100 = e // 100000, n // 100000
        l1 = (19 - n100) - (19 - n100) % 5 + (e100 + 10) // 5
        l2 = (19 - n100) % 5 * 5 + (e100 % 5)
        letters = ""
        for li in (l1, l2):
            li = int(li)
            if li > 7:          # skip 'I'
                li += 1
            letters += chr(ord("A") + li)
        d = max(1, figures // 2)
        div = 10 ** (5 - d)
        return f"{letters} {(e % 100000)//div:0{d}d} {(n % 100000)//div:0{d}d}"
    except Exception:
        return f"E{int(easting)} N{int(northing)}"


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in metres."""
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


class NearestPostcode:
    """
    Vectorised nearest-postcode-centroid lookup over an ONSPD slice.
    Used to reverse-geocode a building centroid to an approximate postcode.
    Distances are computed in BNG metres (planar) — accurate at city scale.
    """

    def __init__(self, eastings, northings, postcodes, lsoa=None, msoa=None, ward=None):
        self.e = np.asarray(eastings, dtype=float)
        self.n = np.asarray(northings, dtype=float)
        self.pc = list(postcodes)
        self.lsoa = list(lsoa) if lsoa is not None else [None] * len(self.pc)
        self.msoa = list(msoa) if msoa is not None else [None] * len(self.pc)
        self.ward = list(ward) if ward is not None else [None] * len(self.pc)

    def query(self, easting: float, northing: float) -> dict:
        d2 = (self.e - easting) ** 2 + (self.n - northing) ** 2
        i = int(np.argmin(d2))
        dist = float(math.sqrt(d2[i]))
        return {
            "postcode": self.pc[i],
            "lsoa_code": self.lsoa[i],
            "msoa_code": self.msoa[i],
            "ward_code": self.ward[i],
            "postcode_distance_m": round(dist, 1),
            # > ~150 m from any unit centroid → treat postcode as low confidence
            "postcode_confidence": "high" if dist < 60 else ("medium" if dist < 150 else "low"),
        }


def pitched_plane_area(footprint_m2: float, pitch_deg: float) -> float:
    """Roof slope area for a pitched roof given the plan (footprint) area."""
    return footprint_m2 / max(math.cos(math.radians(pitch_deg)), 0.1)
