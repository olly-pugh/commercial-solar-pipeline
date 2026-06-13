"""
Agent 4 — Project Scale / Usable Roof Area.

Prioritises commercially meaningful larger rooftop opportunities, WITHOUT letting
raw area dominate. Score is a saturating (log) function of *feasible* kWp — derived
from confirmed usable roof area — then penalised for low usable share, low data
confidence, heavy shading, or existing PV saturation.

Distinct from the technical sub-score: technical asks "is this roof suitable?"
(saturates at ~150 m2 usable); project-scale asks "is the opportunity big enough
to prioritise commercially?" (saturates near ~800 kWp). A tiny excellent roof and
a huge excellent roof differ here, by design.
"""
from __future__ import annotations

import math
import pandas as pd

from . import settings


def _size_category(kwp: float, bands: list) -> str:
    for b in bands:
        if kwp <= b["max_kwp"]:
            return b["name"]
    return bands[-1]["name"]


def score(df: pd.DataFrame, cfg: dict, audit=None) -> tuple[pd.DataFrame, dict]:
    ps = settings.scoring()["project_scale"]
    eco = settings.scoring()["economics"]
    k0, kref = ps["k0_kwp"], ps["kref_kwp"]
    denom = math.log(1 + kref / k0)

    rows = []
    for _, r in df.iterrows():
        kwp = float(r["feasible_kwp"])
        usable = float(r["usable_roof_area_m2"])
        share = float(r["usable_roof_share"])
        # base saturating score
        base = 10.0 * math.log(1 + max(kwp, 0) / k0) / denom
        base = min(10.0, base)

        caveats = []
        factor = 1.0
        if share < ps["usable_share_floor"]:
            factor *= ps["penalty_low_usable_share"]
            caveats.append("low_usable_share")
        # data confidence: footprint-only or poor category confidence
        conf_score = float(r.get("roof_confidence_score", 0.5))
        if conf_score < 0.6 or float(r.get("category_confidence", 0.3)) < 0.35:
            factor *= ps["penalty_low_confidence"]
            caveats.append("low_data_confidence")
        if float(r.get("shading_factor", 1.0)) < 0.8:
            factor *= ps["penalty_high_shading"]
            caveats.append("high_shading")
        if bool(r.get("existing_solar_flag", False)):
            factor *= ps["penalty_existing_pv"]
            caveats.append("existing_pv_expansion_only")
        # fragmentation not measured without LiDAR planes — disclosed, not penalised blind
        caveats.append("fragmentation_not_measured" if r.get("roof_method") != "lidar" else "")

        scale_score = round(min(10.0, base * factor), 2)

        # Project-scale stays an HONEST, monotonic measure of opportunity SIZE — it is
        # NOT inflated for small sites. Strong micro sites are instead surfaced by the
        # economics sub-score and the 'fastest-payback' ranking mode (a transparency
        # caveat is tagged so the report can still highlight them).
        cat = _size_category(kwp, ps["size_bands"])
        payback = r.get("payback_central_yr")
        if (cat == "micro" and payback is not None
                and payback <= ps["micro_strong_economics_floor_payback_yr"]):
            caveats.append("micro_but_strong_economics")

        scale_conf = round(0.5 * conf_score + 0.3 * float(r.get("category_confidence", 0.3))
                           + 0.2 * float(r.get("classification_confidence", 0.3)), 2)

        rows.append({
            "feasible_kwp_scale": round(kwp, 1),
            "project_size_category": cat,
            "project_scale_score": scale_score,
            "project_scale_base": round(base, 2),
            "project_scale_factor": round(factor, 3),
            "scale_confidence": scale_conf,
            "scale_caveats": ";".join(c for c in caveats if c),
            # scale-adjusted opportunity value: usable kWp weighted by confidence
            "scale_opportunity_value": round(kwp * scale_conf, 1),
        })
    res = pd.concat([df.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    res["scale_rank_within_area"] = res["project_scale_score"].rank(
        ascending=False, method="first").astype(int)

    # Optional minimum-scale filter (default: keep all)
    n_before = len(res)
    if cfg.get("min_kwp"):
        res = res[res["feasible_kwp"] >= cfg["min_kwp"]].reset_index(drop=True)
    if cfg.get("min_usable_roof_area"):
        res = res[res["usable_roof_area_m2"] >= cfg["min_usable_roof_area"]].reset_index(drop=True)

    big_cols = [c for c in ["commercial_property_id", "feasible_kwp", "usable_roof_area_m2",
                            "project_size_category", "project_scale_score"] if c in res.columns]
    stats = {
        "size_category_breakdown": res["project_size_category"].value_counts().to_dict(),
        "largest_projects": res.nlargest(5, "feasible_kwp")[big_cols].to_dict("records"),
        "min_scale_filter_removed": int(n_before - len(res)),
    }
    return res, stats
