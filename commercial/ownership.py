"""
Agent 7 — Ownership & Decision Feasibility.

Scores how practically actionable a project is (owner-occupied vs tenant, single
vs multi-tenant, decision complexity). Without VOA non-domestic rating list or
Land Registry title data, tenure is INFERRED from building type + size, at LOW
confidence — every lead carries an explicit decision-feasibility caveat that
distinguishes building suitability from sales feasibility.
"""
from __future__ import annotations

import pandas as pd

# Base feasibility (0-10) and likely-occupancy by archetype.
# Single-occupier owner-operators (warehouse/industrial/retail shed) are the most
# actionable; multi-tenant city offices carry split-incentive risk.
_BASE = {
    "warehouse_logistics":     (7.5, "likely owner-occupier / single operator"),
    "cold_storage":            (7.5, "likely owner-occupier / single operator"),
    "manufacturing_industrial":(7.5, "likely owner-occupier / single operator"),
    "retail_shed":             (7.0, "likely single retail operator (may be leased)"),
    "supermarket_food":        (6.5, "corporate estate — central decision, longer cycle"),
    "office":                  (5.0, "possible multi-tenant — split-incentive risk"),
    "retail_highstreet":       (5.5, "often leased unit — landlord consent needed"),
    "school":                  (6.5, "institutional single decision-maker, bureaucratic"),
    "university_higher_ed":    (6.5, "estates team decision, capital cycles"),
    "leisure_gym":             (6.0, "operator or landlord — varies"),
    "public_sector":           (6.5, "public estate decision, procurement"),
    "mixed_commercial":        (5.0, "mixed tenure — multiple stakeholders"),
    "generic_commercial":      (5.0, "tenure unknown — requires confirmation"),
}


def score(df: pd.DataFrame, cfg: dict, audit=None) -> tuple[pd.DataFrame, dict]:
    rows = []
    for _, r in df.iterrows():
        base, occ = _BASE.get(r["property_category"], _BASE["generic_commercial"])
        adj = base
        # Very large single buildings → simpler single-decision sites (+).
        if r["footprint_area_m2"] >= 3000 and r["property_category"] in (
                "warehouse_logistics", "manufacturing_industrial", "retail_shed", "cold_storage"):
            adj += 0.5
        # Tall city offices → more likely multi-tenant (−).
        if r["property_category"] == "office" and float(r.get("height_m") or 0) >= 15:
            adj -= 0.5
        adj = max(0.0, min(10.0, adj))
        rows.append({
            "likely_occupancy": occ,
            "tenure_inference": "owner_occupier_likely" if adj >= 7 else (
                "mixed_or_tenant_risk" if adj < 5.5 else "uncertain"),
            "decision_feasibility_score": round(adj, 2),
            "decision_feasibility_confidence": 0.3,
            "decision_feasibility_caveat": (
                "Tenure inferred from building type/size (no VOA non-domestic / Land "
                "Registry title). Confirm owner-occupier vs landlord/tenant and "
                "split-incentive before approach."),
        })
    res = pd.concat([df.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    if audit is not None:
        audit.record_missing(
            "voa_nondomestic_rates", required=False,
            impact="Owner/occupier tenure and hereditament detail not confirmed.",
            degraded_behaviour="Decision feasibility inferred from building type/size at "
                               "low confidence; caveat on every lead.")
    stats = {"tenure_basis": "type_size_inference"}
    return res, stats
