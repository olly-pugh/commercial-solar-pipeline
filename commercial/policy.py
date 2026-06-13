"""
Agent 8 — Policy, Planning & Compliance.

Scores regulatory/reputational pressure (sector ESG, non-domestic EPC/MEES where
known, public-sector estate duty) AND flags planning/heritage risk (conservation
area / listed building) from the local heritage dataset. Heritage proximity is
APPROXIMATE (point proximity from Overpass data) and disclosed as such — never an
assertion of planning approval or refusal.
"""
from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
import pandas as pd

from . import settings
from .geo import wgs_to_bng


def _is_heritage(tags: dict) -> str | None:
    """Classify an OSM element's tags as a heritage feature, or None."""
    if (tags.get("boundary") == "conservation_area") or ("protect_class" in tags):
        return "conservation"
    if ("listed_status" in tags) or ("heritage" in tags) or ("ref:mhs" in tags) \
            or (tags.get("historic") in ("building", "yes", "monument", "memorial")):
        return "listed"
    return None


@lru_cache(maxsize=1)
def _heritage_points():
    """Return (KDTree, kinds[list]) of heritage feature points (BNG), or None.

    Oxford's Overpass extract tags heritage features mostly on WAYS (no centroid in
    the file) plus untagged geometry NODES. We reconstruct each tagged way's centroid
    from its member-node coordinates so listed buildings/heritage actually register.
    """
    path = settings.source_path("planning_constraints")
    if not path or not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    els = data.get("elements", []) if isinstance(data, dict) else []
    node_xy = {e["id"]: (e["lat"], e["lon"]) for e in els
               if e.get("type") == "node" and "lat" in e}
    es, ns, kinds = [], [], []
    for el in els:
        kind = _is_heritage(el.get("tags", {}))
        if not kind:
            continue
        # direct point (tagged node) or way/relation centroid from member nodes
        if el.get("lat") is not None:
            lat, lon = el["lat"], el["lon"]
        elif (el.get("center") or {}).get("lat") is not None:
            lat, lon = el["center"]["lat"], el["center"]["lon"]
        else:
            pts = [node_xy[r] for r in el.get("nodes", []) if r in node_xy]
            if not pts:
                continue
            lat = sum(p[0] for p in pts) / len(pts)
            lon = sum(p[1] for p in pts) / len(pts)
        e, n = wgs_to_bng(lat, lon)
        es.append(e); ns.append(n)
        kinds.append(kind)
    if not es:
        return None
    from scipy.spatial import cKDTree
    return cKDTree(np.column_stack([es, ns])), kinds


def _planning_risk(easting, northing):
    t = _heritage_points()
    if t is None:
        return "unknown", None
    tree, kinds = t
    dist, idx = tree.query([easting, northing], k=1)
    dist = float(dist)
    if dist <= 30:
        return "high", round(dist, 1)
    if dist <= 100:
        return "moderate", round(dist, 1)
    return "low", round(dist, 1)


def score(df: pd.DataFrame, cfg: dict, audit=None) -> tuple[pd.DataFrame, dict]:
    arche = settings.archetypes()["archetypes"]
    have_heritage = _heritage_points() is not None
    rows = []
    high = 0
    for _, r in df.iterrows():
        adef = arche.get(r["property_category"], arche["generic_commercial"])
        esg = adef["esg_pressure_0_10"]

        # MEES / EPC pressure: only if a non-domestic EPC band was attached.
        epc_band = r.get("epc_asset_rating")
        mees_pressure = 0.0
        mees_note = "EPC band unknown (non-domestic EPC not attached) — MEES not assessed"
        if isinstance(epc_band, str) and epc_band:
            band = epc_band.strip().upper()[:1]
            if band in ("E", "F", "G"):
                mees_pressure = 2.0
                mees_note = f"EPC {band}: MEES-relevant — solar+efficiency bundle indicated"
            else:
                mees_note = f"EPC {band}: not MEES-critical"

        risk, dist = _planning_risk(r["easting"], r["northing"]) if have_heritage else ("unknown", None)
        if risk == "high":
            high += 1

        policy_score = min(10.0, 0.8 * esg + mees_pressure)
        rows.append({
            "policy_compliance_score": round(policy_score, 2),
            "epc_band": epc_band if isinstance(epc_band, str) else None,
            "mees_note": mees_note,
            "planning_risk": risk,
            "planning_nearest_heritage_m": dist,
            "heritage_flag": risk in ("high", "moderate"),
            "policy_confidence": 0.5 if have_heritage else 0.3,
        })
    res = pd.concat([df.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    if not have_heritage and audit is not None:
        audit.record_missing(
            "planning_constraints", required=False,
            impact="Conservation-area / listed-building risk not assessed.",
            degraded_behaviour="planning_risk=UNKNOWN; never assumed clear. Requires "
                               "planning confirmation before recommendation.")
    stats = {"heritage_dataset_loaded": have_heritage,
             "high_planning_risk_count": int(high)}
    return res, stats
