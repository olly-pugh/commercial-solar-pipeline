"""
Agent 5 — Commercial Energy Demand & Economics.

Estimates electricity demand (archetype intensity × floor area), self-consumption,
CAPEX (size-banded with economies of scale), savings, payback and avoided carbon
under conservative/central/high price scenarios. Outputs the Economics sub-score.

All figures are INDICATIVE for lead prioritisation — not quotes. Requires bill
validation and a site survey before commitment.
"""
from __future__ import annotations

import pandas as pd

from . import settings


def _capex_per_kwp(kwp: float, bands: list) -> dict:
    for b in bands:
        if kwp <= b["band_max_kwp"]:
            return b
    return bands[-1]


def _estimate_floor_area(row, arche_def) -> tuple[float, str]:
    fa = row.get("epc_floor_area_m2")
    if fa and fa > 0:
        return float(fa), "epc"
    footprint = float(row["footprint_area_m2"])
    height = float(row.get("height_m") or 0)
    if row.get("roof_type") == "flat":
        storeys = 1                       # single-storey high-bay
    elif height > 0:
        storeys = max(1, min(round(height / 3.5), arche_def["typical_storeys"] * 2))
    else:
        storeys = arche_def["typical_storeys"]
    return footprint * storeys, "footprint_x_storeys"


def _payback_score(payback_yr, e) -> float:
    if payback_yr is None or payback_yr <= 0:
        return 0.0
    pts = [(e["payback_excellent_yr"], 10), (e["payback_good_yr"], 7),
           (e["payback_marginal_yr"], 4), (e["payback_poor_yr"], 1)]
    if payback_yr <= pts[0][0]:
        return 10.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if payback_yr <= x1:
            return y0 + (y1 - y0) * (payback_yr - x0) / (x1 - x0)
    return 0.5


def model(df: pd.DataFrame, cfg: dict, audit=None) -> tuple[pd.DataFrame, dict]:
    arche = settings.archetypes()["archetypes"]
    pv = settings.pv_costs()
    tar = settings.tariffs()
    esc = settings.scoring()["economics"]
    bands = pv["capex_gbp_per_kwp"]
    opex_pct = pv["opex_pct_of_capex_per_year"]
    carbon = tar["grid_carbon_kgco2e_per_kwh"]
    sc_min, sc_max = pv["self_consumption_min"], pv["self_consumption_max"]

    imp = tar["import_price_gbp_per_kwh"]
    exp = tar["export_price_gbp_per_kwh"]
    scen = cfg.get("scenario", "central")

    missing_demand = 0
    rows = []
    for _, r in df.iterrows():
        cat = r["property_category"]
        adef = arche.get(cat, arche["generic_commercial"])
        gen = float(r["annual_generation_kwh"])
        kwp = float(r["feasible_kwp"])

        floor_area, fa_src = _estimate_floor_area(r, adef)
        demand = floor_area * adef["elec_intensity_kwh_m2_yr"]
        if demand <= 0:
            missing_demand += 1

        sc_ratio = min(max(adef["self_consumption"], sc_min), sc_max)
        self_consumed = min(gen * sc_ratio, demand) if demand > 0 else gen * sc_ratio
        effective_sc = round(self_consumed / gen, 3) if gen > 0 else 0.0
        exported = max(0.0, gen - self_consumed)

        cb = _capex_per_kwp(kwp, bands)

        def economics(price_imp, price_exp, capex_pkwp):
            capex = kwp * capex_pkwp
            opex = capex * opex_pct
            saving = self_consumed * price_imp + exported * price_exp
            net = saving - opex
            payback = round(capex / net, 1) if net > 0 else None
            return capex, saving, net, payback

        # central scenario (reported headline)
        capex_c, sav_c, net_c, pay_c = economics(imp[scen], exp[scen], cb["central"])
        # optimistic (fast payback): high price + low capex
        capex_lo, sav_hi, net_hi, pay_fast = economics(imp["high"], exp["high"], cb["low"])
        # pessimistic (slow payback): low price + high capex
        capex_hi, sav_lo, net_lo, pay_slow = economics(imp["conservative"], exp["conservative"], cb["high"])

        s_pay = _payback_score(pay_c, esc)
        # Economics score blends payback (dominant) with self-consumption quality —
        # commercial PV economics hinge on displacing import, so high self-consumption
        # is itself a value signal. effective_sc 0.8 → full 10 on that component.
        econ_score = round(min(10.0, 0.85 * s_pay + 0.15 * min(10.0, effective_sc * 12.5)), 2)

        rows.append({
            "archetype": cat,
            "floor_area_m2": round(floor_area, 0),
            "floor_area_source": fa_src,
            "estimated_annual_demand_kwh": round(demand, 0),
            "self_consumption_ratio": effective_sc,
            "annual_self_consumed_kwh": round(self_consumed, 0),
            "annual_exported_kwh": round(exported, 0),
            "electricity_price_scenario": scen,
            "import_price_gbp_kwh": imp[scen],
            "capex_central_gbp": round(capex_c, 0),
            "capex_low_gbp": round(capex_lo, 0),
            "capex_high_gbp": round(capex_hi, 0),
            "annual_savings_central_gbp": round(sav_c, 0),
            "annual_savings_low_gbp": round(sav_lo, 0),
            "annual_savings_high_gbp": round(sav_hi, 0),
            "payback_central_yr": pay_c,
            "payback_low_yr": pay_fast,      # fastest / optimistic
            "payback_high_yr": pay_slow,     # slowest / pessimistic
            "carbon_savings_kgco2e_yr": round(gen * carbon, 0),
            "economics_score": econ_score,
            "economics_confidence": round(0.7 if fa_src == "epc" else 0.45, 2),
        })
    res = pd.concat([df.reset_index(drop=True), pd.DataFrame(rows)], axis=1)

    if missing_demand and audit is not None:
        audit.warn("economics", f"{missing_demand} buildings had non-positive demand estimate")

    stats = {
        "scenario": scen,
        "median_payback_central_yr": round(float(res["payback_central_yr"].dropna().median()), 1)
            if res["payback_central_yr"].notna().any() else None,
        "total_annual_savings_central_gbp": round(float(res["annual_savings_central_gbp"].sum()), 0),
        "total_carbon_savings_tco2e_yr": round(float(res["carbon_savings_kgco2e_yr"].sum()) / 1000, 1),
        "floor_area_from_epc": int((res["floor_area_source"] == "epc").sum()),
    }
    return res, stats
