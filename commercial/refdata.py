"""
Shared reference-data loaders (loaded once per run, reused by several agents).
"""
from __future__ import annotations

from functools import lru_cache

import duckdb
import numpy as np

from . import settings
from .geo import NearestPostcode


@lru_cache(maxsize=1)
def nearest_postcode_index() -> NearestPostcode | None:
    onspd = settings.source_path("onspd")
    if not onspd or not onspd.exists():
        return None
    con = duckdb.connect()
    rows = con.execute(
        f"SELECT postcode, eastings, northings, lsoa_code, msoa_code, ward_code "
        f"FROM read_parquet('{onspd.as_posix()}') "
        f"WHERE eastings IS NOT NULL AND northings IS NOT NULL"
    ).fetchall()
    con.close()
    if not rows:
        return None
    pc = [r[0] for r in rows]
    e = [r[1] for r in rows]
    n = [r[2] for r in rows]
    return NearestPostcode(e, n, pc, lsoa=[r[3] for r in rows],
                           msoa=[r[4] for r in rows], ward=[r[5] for r in rows])


@lru_cache(maxsize=1)
def uprn_points() -> tuple | None:
    """Return (eastings[np], northings[np], uprn[list]) for the Oxford UPRN slice."""
    path = settings.source_path("os_open_uprn")
    if not path or not path.exists():
        return None
    con = duckdb.connect()
    rows = con.execute(
        f"SELECT UPRN, X_COORDINATE, Y_COORDINATE "
        f"FROM read_csv_auto('{path.as_posix()}') "
        f"WHERE X_COORDINATE IS NOT NULL"
    ).fetchall()
    con.close()
    if not rows:
        return None
    uprn = [int(r[0]) for r in rows]
    e = np.asarray([r[1] for r in rows], dtype=float)
    n = np.asarray([r[2] for r in rows], dtype=float)
    return e, n, uprn


@lru_cache(maxsize=1)
def domestic_dwellings_by_postcode() -> dict:
    """Count of domestic EPC dwellings per normalised postcode (residential signal)."""
    epc = settings.DB_DIR / "epc_oxford.duckdb"
    if not epc.exists():
        return {}
    con = duckdb.connect(str(epc), read_only=True)
    try:
        rows = con.execute(
            "SELECT REPLACE(UPPER(postcode),' ','') pc, COUNT(*) "
            "FROM epc_records WHERE postcode IS NOT NULL GROUP BY 1"
        ).fetchall()
    finally:
        con.close()
    return {r[0]: r[1] for r in rows}


def nearest_uprn(easting: float, northing: float, tolerance_m: float = 25.0):
    """Return (uprn, distance_m) for nearest Oxford UPRN within tolerance, else (None, None)."""
    pts = uprn_points()
    if pts is None:
        return None, None
    e, n, uprn = pts
    d2 = (e - easting) ** 2 + (n - northing) ** 2
    i = int(np.argmin(d2))
    dist = float(np.sqrt(d2[i]))
    if dist <= tolerance_m:
        return uprn[i], round(dist, 1)
    return None, round(dist, 1)


@lru_cache(maxsize=1)
def _uprn_tree():
    pts = uprn_points()
    if pts is None:
        return None
    from scipy.spatial import cKDTree
    e, n, uprn = pts
    return cKDTree(np.column_stack([e, n])), uprn


def batch_nearest_uprn(eastings, northings, tolerance_m=25.0):
    """Vectorised nearest-UPRN for many points. Returns (uprn_list, dist_list)."""
    t = _uprn_tree()
    if t is None:
        return [None] * len(eastings), [None] * len(eastings)
    tree, uprn = t
    dist, idx = tree.query(np.column_stack([eastings, northings]), k=1)
    out_u, out_d = [], []
    for d, i in zip(dist, idx):
        out_d.append(round(float(d), 1))
        out_u.append(int(uprn[i]) if d <= tolerance_m else None)
    return out_u, out_d


@lru_cache(maxsize=1)
def _postcode_tree():
    idx = nearest_postcode_index()
    if idx is None:
        return None
    from scipy.spatial import cKDTree
    return cKDTree(np.column_stack([idx.e, idx.n])), idx


def batch_nearest_postcode(eastings, northings):
    """Vectorised nearest-postcode for many points. Returns list[dict]."""
    t = _postcode_tree()
    if t is None:
        return [{"postcode": None, "postcode_confidence": "none",
                 "postcode_distance_m": None, "lsoa_code": None,
                 "msoa_code": None, "ward_code": None} for _ in eastings]
    tree, idx = t
    dist, ii = tree.query(np.column_stack([eastings, northings]), k=1)
    out = []
    for d, i in zip(dist, ii):
        d = float(d)
        out.append({
            "postcode": idx.pc[i],
            "lsoa_code": idx.lsoa[i], "msoa_code": idx.msoa[i], "ward_code": idx.ward[i],
            "postcode_distance_m": round(d, 1),
            "postcode_confidence": "high" if d < 60 else ("medium" if d < 150 else "low"),
        })
    return out
