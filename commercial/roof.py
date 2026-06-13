"""
Agent 3 — Roof Geometry & Solar Potential.

Derives roof geometry and PV potential from OS MasterMap footprint area + building
height. Without EA LiDAR, roofs are modelled (flat-roof assumption for large
commercial buildings, pitched otherwise) and labelled roof_method='footprint_height'
at MEDIUM confidence — never presented as LiDAR-confirmed.

Outputs the Technical Roof Suitability sub-score (0-10) plus physical PV potential
(usable area, feasible kWp, annual generation) used downstream.
"""
from __future__ import annotations

import math
import pandas as pd

from . import settings, connectors, lidar_roof
from .geo import pitched_plane_area


def _specific_yields(cfg: dict) -> dict:
    """One PVGIS fetch per roof archetype at the territory centroid (city-scale
    irradiance is uniform); falls back to the documented climatology table."""
    pv = settings.pv_costs()
    fb = pv["specific_yield_kwh_per_kwp"]
    bbox = cfg["bbox"]
    lat = (bbox["south"] + bbox["north"]) / 2
    lon = (bbox["west"] + bbox["east"]) / 2
    out = {"source": "fallback_climatology"}
    if not cfg.get("dry_run"):
        flat, st1 = connectors.pvgis_specific_yield(lat, lon, tilt=10, azimuth_deg_from_south=0)
        pitch, st2 = connectors.pvgis_specific_yield(lat, lon, tilt=25, azimuth_deg_from_south=0)
        if flat and pitch:
            out = {"flat": round(flat, 0), "pitched": round(pitch, 0),
                   "mixed": round((flat + pitch) / 2, 0), "source": "pvgis"}
            return out
    out.update({"flat": fb["flat_roof"], "pitched": fb["pitched_south"],
                "mixed": fb["default"]})
    return out


def _roof_type_for(category: str, area: float, height: float, arche: dict,
                   large_flat_m2: float) -> str:
    # Large-footprint buildings are flat-roofed in practice regardless of category.
    if area >= large_flat_m2:
        return "flat"
    base = arche["archetypes"].get(category, arche["archetypes"]["generic_commercial"])["roof_type"]
    if base != "mixed":
        return base
    # mixed → large low buildings are effectively flat-roofed; tall/small → pitched
    if area >= 800 and height <= 15:
        return "flat"
    return "pitched"


def _score_pitch(pitch_deg: float) -> float:
    if pitch_deg <= 5:    return 0.9   # flat: panels tilted to optimum, slight loss
    if pitch_deg <= 15:   return 0.95
    if pitch_deg <= 38:   return 1.0
    if pitch_deg <= 50:   return 0.8
    return 0.5


def analyse(cands: pd.DataFrame, cfg: dict, ground_truth_pv: set | None = None,
            use_lidar: bool = False, lidar_min_footprint_m2: float = 1000.0,
            audit=None) -> tuple[pd.DataFrame, dict]:
    pv = settings.pv_costs()
    arche = settings.archetypes()
    sc = settings.scoring()["technical"]
    usable_frac = pv["usable_fraction"]
    pitch_assume = pv["assumed_pitch_deg"]
    m2pk = pv["m2_per_kwp"]
    sy = _specific_yields(cfg)
    lidar_on = use_lidar and lidar_roof.lidar_available()
    n_lidar = 0

    df = cands.copy()
    out = []
    for _, r in df.iterrows():
        area = float(r["footprint_area_m2"])
        h = float(r["height_m"] or 0)
        cat = r["property_category"]
        rtype = _roof_type_for(cat, area, h, arche, pv["large_footprint_flat_m2"])
        pitch = pitch_assume[rtype]

        # ── Optional LiDAR measurement for large priority buildings ──────────
        lidar_m = None
        if lidar_on and area >= lidar_min_footprint_m2 and isinstance(r.get("wkt"), str):
            lidar_m = lidar_roof.refine(r["wkt"], r["easting"], r["northing"], area)

        if lidar_m:
            n_lidar += 1
            total_roof = lidar_m["lidar_measured_roof_m2"]
            usable = lidar_m["lidar_usable_area_m2"]
            pitch = lidar_m["lidar_pitch_deg"]
            aspect = lidar_m["lidar_aspect_deg"]
            roof_method = "lidar_derived"
            roof_conf_score = 0.9
            usable_share = round(usable / total_roof, 3) if total_roof else 0.0
            # mounting density follows measured pitch
            m2_per_kwp = m2pk["flat"] if pitch <= 10 else m2pk["pitched"]
            kwp = usable / m2_per_kwp
            spec_yield = sy["flat"] if pitch <= 10 else sy["pitched"]
        else:
            aspect = None
            # Total roof (slope) area
            total_roof = area if rtype == "flat" else pitched_plane_area(area, pitch)
            usable = total_roof * usable_frac[rtype]
            usable_share = round(usable / total_roof, 3) if total_roof else 0.0
            kwp = usable / m2pk[rtype]
            spec_yield = sy[rtype]
            roof_method = "footprint_height" if h > 0 else "footprint_only"
            roof_conf_score = 0.6 if h > 0 else 0.4  # neither is LiDAR (which would be ~0.9)
        generation = kwp * spec_yield

        # Existing PV (only if ground-truth supplied; otherwise honestly unassessed)
        existing_pv = bool(ground_truth_pv and (r["building_id"] in ground_truth_pv
                                                 or (r["uprn"] is not None and r["uprn"] in ground_truth_pv)))

        # ── Technical sub-score components (0-1) ─────────────────────────────
        # Orientation: measured aspect (LiDAR) scored vs south; else flat roof tilts
        # panels to optimum → strong; pitched with unknown azimuth → moderate.
        if aspect is not None:
            diff = min(abs(aspect - 180), 360 - abs(aspect - 180))
            s_orient = max(0.55, 1 - diff / 180) if pitch > 10 else sc["flat_roof_orientation_score"]
        else:
            s_orient = sc["flat_roof_orientation_score"] if rtype == "flat" else 0.7
        s_pitch = _score_pitch(pitch)
        # Usable-area ADEQUACY (saturates fast at ~150 m2 → avoids double-count with project scale)
        s_usable = min(1.0, usable / 150.0)
        # Shading: LiDAR run → measured-coverage-based light shading; else modelled. Low confidence without LiDAR.
        if roof_method == "lidar_derived":
            s_shade = 0.92
        else:
            s_shade = 0.95 if (rtype == "flat" and area >= 1000) else 0.85
        shading_factor = s_shade
        # Irradiance (regional, uniform within city) normalised vs strong UK yield (1000 kWh/kWp).
        s_irr = min(1.0, spec_yield / 1000.0)

        tech = (sc["w_orientation"] * s_orient + sc["w_pitch"] * s_pitch
                + sc["w_usable_area"] * s_usable + sc["w_shading"] * s_shade
                + sc["w_irradiance"] * s_irr)
        if existing_pv:
            tech *= sc["existing_pv_penalty"]

        out.append({
            "roof_type": rtype,
            "roof_method": roof_method,
            "roof_pitch_deg": pitch,
            "total_roof_area_m2": round(total_roof, 1),
            "usable_roof_area_m2": round(usable, 1),
            "usable_roof_share": usable_share,
            "shading_factor": shading_factor,
            "feasible_kwp": round(kwp, 1),
            "specific_yield_kwh_per_kwp": round(spec_yield, 0),
            "annual_generation_kwh": round(generation, 0),
            "existing_solar_flag": existing_pv,
            "existing_solar_assessed": bool(ground_truth_pv is not None),
            "s_tech_orientation": round(s_orient, 3),
            "s_tech_pitch": round(s_pitch, 3),
            "s_tech_usable": round(s_usable, 3),
            "s_tech_shading": round(s_shade, 3),
            "s_tech_irradiance": round(s_irr, 3),
            "technical_score": round(min(10.0, tech * 10), 2),
            "roof_confidence_score": roof_conf_score,
        })
    res = pd.concat([df.reset_index(drop=True), pd.DataFrame(out)], axis=1)

    if audit is not None and not lidar_on:
        audit.record_missing(
            "ea_lidar", required=False,
            impact="Roof pitch/azimuth/shading modelled from footprint+height, not "
                   "measured (LiDAR refinement off). Roof confidence capped at MEDIUM.",
            degraded_behaviour="Flat-roof assumption for large commercial; pitched model "
                               "otherwise. Enable --lidar to measure large roofs from EA DSM.")
    stats = {
        "specific_yield_source": sy["source"],
        "specific_yields": {k: v for k, v in sy.items() if k != "source"},
        "roof_method_breakdown": res["roof_method"].value_counts().to_dict(),
        "lidar_refined_count": int(n_lidar),
        "total_usable_roof_area_m2": round(float(res["usable_roof_area_m2"].sum()), 0),
        "total_feasible_kwp": round(float(res["feasible_kwp"].sum()), 0),
    }
    return res, stats
