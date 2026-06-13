"""
Agent 10 — Scoring & Ranking.

Combines the seven transparent sub-scores into a final 0-10 Commercial Solar Lead
Score, computes confidence / data-completeness / proxy-quality, assembles caveat
tags, applies heritage penalty, and ranks under the selected mode:
balanced | large-project-first | fastest-payback | highest-confidence.

The formula is fully exposed in the methodology and in score_explainability.json.
"""
from __future__ import annotations

import pandas as pd

from . import settings, MODEL_VERSION

SUBSCORES = [
    ("technical", "technical_score"),
    ("economics", "economics_score"),
    ("project_scale", "project_scale_score"),
    ("adoption_propensity", "adoption_propensity_score"),
    ("decision_feasibility", "decision_feasibility_score"),
    ("policy_compliance", "policy_compliance_score"),
    ("market_momentum", "market_momentum_score"),
]

# Evidence strength per sub-score (A=1, B=.8, C=.5, D=.3) → proxy-quality.
_EVIDENCE = {"technical": 0.8, "economics": 0.8, "project_scale": 0.8,
             "adoption_propensity": 0.5, "decision_feasibility": 0.5,
             "policy_compliance": 0.6, "market_momentum": 0.3}

_CONF_COLS = {
    "classification_confidence": 0.15, "category_confidence": 0.15,
    "roof_confidence_score": 0.2, "economics_confidence": 0.15,
    "scale_confidence": 0.1, "policy_confidence": 0.1,
    "adoption_propensity_confidence": 0.05, "market_momentum_confidence": 0.05,
    "decision_feasibility_confidence": 0.05,
}


def _mode_weights(base: dict, mode: str, modes_cfg: dict) -> dict:
    factors = modes_cfg.get(mode, {})
    w = {k: base[k] * factors.get(k, 1.0) for k in base}
    tot = sum(w.values())
    return {k: v / tot for k, v in w.items()}


def _data_completeness(r, src_w: dict, pvgis_ok: bool) -> float:
    present = {
        "geometry": 1.0,
        "height": 1.0 if float(r.get("height_m") or 0) > 0 else 0.0,
        "postcode": 1.0 if (r.get("postcode") and r.get("postcode_confidence") != "low") else 0.4,
        "uprn": 1.0 if r.get("uprn") is not None else 0.0,
        "nondom_epc": 1.0 if r.get("category_source") == "epc_confirmed" else 0.0,
        "companies_house": 1.0 if r.get("occupier_source") == "companies_house_search" else 0.0,
        "planning": 1.0 if r.get("planning_risk") not in (None, "unknown") else 0.0,
        "pvgis": 1.0 if pvgis_ok else 0.0,
    }
    num = sum(src_w[k] * present.get(k, 0) for k in src_w)
    return round(num / sum(src_w.values()), 3)


def _caveats(r) -> list:
    c = []
    if r.get("category_source") == "geometry_heuristic":
        c.append("category_inferred_from_geometry")
    if r.get("roof_method") != "lidar":
        c.append("roof_not_lidar_confirmed")
    if r.get("postcode_confidence") == "low":
        c.append("approximate_postcode")
    if r.get("uprn") is None:
        c.append("uprn_unresolved")
    if not r.get("category_source") == "epc_confirmed":
        c.append("epc_band_unknown")
    if r.get("occupier_source") != "companies_house_search":
        c.append("occupier_unknown")
    if r.get("planning_risk") in ("high", "moderate"):
        c.append(f"planning_risk_{r.get('planning_risk')}")
    if r.get("planning_risk") == "unknown":
        c.append("planning_not_assessed")
    if bool(r.get("existing_solar_flag")):
        c.append("existing_pv_expansion_only")
    if r.get("scale_caveats"):
        c += [x for x in str(r["scale_caveats"]).split(";") if x and x != "fragmentation_not_measured"]
    c.append("requires_site_survey_and_bill_validation")
    return sorted(set(c))


def rank(df: pd.DataFrame, cfg: dict, run_id: str, pvgis_ok: bool,
         audit=None) -> tuple[pd.DataFrame, dict]:
    sc = settings.scoring()
    base_w = sc["weights"]
    mode = cfg.get("ranking_mode", "balanced")
    w = _mode_weights(base_w, mode, sc["ranking_modes"])
    src_w = sc["confidence"]["source_weights"]
    ps = sc["project_scale"]

    res = df.copy()
    finals, confs, completes, proxyq, caveats = [], [], [], [], []
    for _, r in res.iterrows():
        final = sum(w[name] * float(r[col]) for name, col in SUBSCORES)
        # Heritage penalty (planning constraint reduces lead priority).
        if r.get("planning_risk") == "high":
            final *= ps["penalty_heritage"]
        elif r.get("planning_risk") == "moderate":
            final *= (1 + ps["penalty_heritage"]) / 2
        final = round(min(10.0, final), 3)
        finals.append(final)

        cw = {k: v for k, v in _CONF_COLS.items() if k in r and pd.notna(r[k])}
        conf = sum(_CONF_COLS[k] * float(r[k]) for k in cw) / (sum(cw.values()) or 1)
        confs.append(round(conf, 3))
        completes.append(_data_completeness(r, src_w, pvgis_ok))
        proxyq.append(round(sum(w[name] * _EVIDENCE[name] for name, _ in SUBSCORES), 3))
        caveats.append(";".join(_caveats(r)))

    res["final_score_10"] = finals
    res["confidence_score"] = confs
    res["data_completeness_score"] = completes
    res["proxy_quality_score"] = proxyq
    res["caveat_tags"] = caveats
    res["ranking_mode"] = mode
    res["model_version"] = MODEL_VERSION
    res["run_id"] = run_id

    # Ordering per mode.
    if mode == "fastest-payback":
        res = res.sort_values(
            ["payback_central_yr", "final_score_10"],
            ascending=[True, False], na_position="last")
    elif mode == "highest-confidence":
        res = res.sort_values(["confidence_score", "final_score_10"], ascending=[False, False])
    else:  # balanced, large-project-first
        res = res.sort_values("final_score_10", ascending=False)

    res = res.reset_index(drop=True)
    res["rank"] = res.index + 1
    res["included_in_top_leads"] = res["rank"] <= cfg.get("leads", 100)

    weight_formula = " + ".join(f"{w[n]:.3f}·{n}" for n, _ in SUBSCORES)
    stats = {
        "ranking_mode": mode,
        "effective_weights": {k: round(v, 4) for k, v in w.items()},
        "formula": f"final_10 = {weight_formula}  (× heritage penalty if applicable)",
        "distinct_final_scores": int(res["final_score_10"].round(2).nunique()),
        "distinct_final_scores_top100": int(res.head(100)["final_score_10"].round(2).nunique()),
        "distinct_project_scale_top100": int(res.head(100)["project_scale_score"].round(2).nunique()),
        "final_score_range": [float(res["final_score_10"].min()), float(res["final_score_10"].max())],
        "median_confidence": round(float(res["confidence_score"].median()), 3),
    }
    return res, stats
