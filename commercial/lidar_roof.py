"""
LiDAR roof refinement (optional, --lidar).

Where EA LiDAR DSM coverage exists, this measures real roof pitch, dominant aspect
and usable area for a building footprint, upgrading roof_method to 'lidar_derived'
(HIGH confidence) instead of the footprint+height model. Applied to large commercial
candidates (the priority leads) to keep runtime bounded.

Algorithm mirrors the proven domestic roof_lidar_scorer: mask DSM by footprint,
derive slope/aspect via a Sobel gradient, take roof pixels, measure flat/usable area.
DSM and OS MasterMap geometry are both EPSG:27700 (1 m), so masking is direct.
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import settings


@lru_cache(maxsize=1)
def _tile_index():
    """List of (path, bounds, res) for DSM tiles, or empty if none."""
    d = settings.ROOT / "data" / "lidar" / "dsm"
    if not d.exists():
        return ()
    import rasterio
    idx = []
    for t in sorted(d.glob("*.tif")):
        try:
            with rasterio.open(t) as s:
                idx.append((str(t), tuple(s.bounds), float(s.res[0])))
        except Exception:
            continue
    return tuple(idx)


def lidar_available() -> bool:
    return len(_tile_index()) > 0


def _slope_aspect(dsm: np.ndarray, cell: float):
    z = np.pad(dsm.astype(float), 1, mode="edge")
    dzdx = ((z[:-2, 2:] + 2*z[1:-1, 2:] + z[2:, 2:]) -
            (z[:-2, :-2] + 2*z[1:-1, :-2] + z[2:, :-2])) / (8*cell)
    dzdy = ((z[2:, :-2] + 2*z[2:, 1:-1] + z[2:, 2:]) -
            (z[:-2, :-2] + 2*z[:-2, 1:-1] + z[:-2, 2:])) / (8*cell)
    slope = np.degrees(np.arctan(np.sqrt(dzdx**2 + dzdy**2)))
    aspect = (90 - np.degrees(np.arctan2(-dzdy, dzdx))) % 360
    return slope, aspect


def _circular_mean(a: np.ndarray) -> float:
    r = np.radians(a)
    return float(np.degrees(np.arctan2(np.mean(np.sin(r)), np.mean(np.cos(r)))) % 360)


def refine(geom_wkt: str, easting: float, northing: float, footprint_m2: float) -> dict | None:
    """Return measured roof metrics or None if no LiDAR coverage / mask fails."""
    idx = _tile_index()
    if not idx:
        return None
    try:
        import rasterio
        from rasterio.mask import mask as rio_mask
        from rasterio.merge import merge as rio_merge
        from shapely import wkt as shp_wkt
        from shapely.geometry import mapping
    except Exception:
        return None

    geom = shp_wkt.loads(geom_wkt)
    minx, miny, maxx, maxy = geom.bounds
    matching = [p for (p, b, _) in idx
                if not (maxx < b[0] or minx > b[2] or maxy < b[1] or miny > b[3])]
    if not matching:
        return None

    try:
        import warnings
        if len(matching) == 1:
            src = rasterio.open(matching[0])
            arr, _ = rio_mask(src, [mapping(geom)], crop=True, nodata=np.nan)
            cell = src.res[0]; src.close()
        else:
            # Merge ONLY the building's bbox window (with a small pad) — never whole
            # 5km tiles — so boundary-spanning buildings stay fast.
            pad = 2.0
            dss = [rasterio.open(p) for p in matching]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                merged, transform = rio_merge(
                    dss, bounds=(minx - pad, miny - pad, maxx + pad, maxy + pad), nodata=np.nan)
            profile = dss[0].profile
            for d in dss:
                d.close()
            import rasterio.io
            with rasterio.io.MemoryFile() as mem:
                profile.update(height=merged.shape[1], width=merged.shape[2],
                               transform=transform, nodata=np.nan)
                with mem.open(**profile) as tmp:
                    tmp.write(merged)
                    arr, _ = rio_mask(tmp, [mapping(geom)], crop=True, nodata=np.nan)
                    cell = tmp.res[0]
        dsm = arr[0].astype(float)
        dsm[dsm < -100] = np.nan
        valid = ~np.isnan(dsm)
        if valid.sum() < 8:
            return None

        cell2 = cell * cell
        covered_m2 = float(valid.sum() * cell2)
        coverage = min(1.0, covered_m2 / max(footprint_m2, 1.0))
        if coverage < 0.4:
            return None

        slope, aspect = _slope_aspect(np.nan_to_num(dsm, nan=np.nanmedian(dsm)), cell)
        roof = valid
        pitch = float(np.median(slope[roof]))
        aspect_mean = _circular_mean(aspect[roof])

        # Usable pixels: low-slope (flat roof deck) OR favourably-oriented pitched planes,
        # then reduce for inter-row spacing / walkways / plant keep-outs.
        flat_like = slope <= 10
        good_aspect = (np.abs(((aspect - 180 + 180) % 360) - 180) <= 90) & (slope > 10) & (slope <= 45)
        usable_px = roof & (flat_like | good_aspect)
        usable_raw_m2 = float(usable_px.sum() * cell2)
        # Spacing/keep-out reduction (flat decks need inter-row gaps).
        pack = 0.62 if pitch <= 10 else 0.78
        usable_m2 = usable_raw_m2 * pack

        return {
            "lidar_pitch_deg": round(pitch, 1),
            "lidar_aspect_deg": round(aspect_mean, 1),
            "lidar_measured_roof_m2": round(covered_m2, 1),
            "lidar_usable_area_m2": round(usable_m2, 1),
            "lidar_coverage": round(coverage, 2),
            "roof_method": "lidar_derived",
        }
    except Exception:
        return None
