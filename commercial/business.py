"""
Agent 6 — Business / Occupier Intelligence (Adoption Propensity).

Scores how likely the occupier is to value solar, from sector energy-intensity and
ESG/regulatory pressure (archetype-level). Where an occupier name is resolvable
(e.g. from EPC), Companies House confirms identity/SIC/status/age — lawful public
register only, no personal contact scraping. Without an occupier name, propensity
is scored at SECTOR level with reduced confidence and an explicit provenance caveat.
"""
from __future__ import annotations

import pandas as pd

from . import settings, connectors

_INTENSITY = {"very_high": 1.0, "high": 0.85, "medium": 0.65, "low": 0.45}


def score(df: pd.DataFrame, cfg: dict, audit=None) -> tuple[pd.DataFrame, dict]:
    arche = settings.archetypes()["archetypes"]
    rows = []
    for _, r in df.iterrows():
        adef = arche.get(r["property_category"], arche["generic_commercial"])
        esg = adef["esg_pressure_0_10"]
        intensity = _INTENSITY.get(adef["energy_intensity_class"], 0.6)
        # Sector propensity: ESG pressure + energy-cost salience (intensity).
        prop = 0.55 * esg + 0.45 * (intensity * 10)
        rows.append({
            "occupier_name": None,                    # unknown unless CH-enriched
            "occupier_source": "unknown",
            "companies_house_id": None,
            "sic_code": None,
            "sector": adef["label"],
            "company_status": None,
            "company_age_years": None,
            "energy_intensity_class": adef["energy_intensity_class"],
            "esg_pressure_class": adef["esg_pressure_class"],
            "adoption_propensity_score": round(min(10.0, prop), 2),
            "adoption_propensity_confidence": 0.35,   # sector-level only
        })
    res = pd.concat([df.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    if audit is not None:
        audit.record_missing(
            "companies_house", required=False,
            impact="Occupier identity/SIC/company status not attached at scale "
                   "(no building→occupier link without addressed non-domestic EPC).",
            degraded_behaviour="Propensity scored at SECTOR level from archetype ESG + "
                               "energy intensity; occupier_name=UNKNOWN with caveat. "
                               "Top leads optionally enriched via Companies House by name.")
    stats = {"propensity_basis": "sector_level", "companies_house_used": False}
    return res, stats


def enrich_top_leads_companies_house(df_top: pd.DataFrame, max_calls: int = 40) -> int:
    """Best-effort Companies House enrichment of top leads that carry an occupier/
    address string. Returns count enriched. Lawful public-register lookups only."""
    if not settings.env_present("COMPANIES_HOUSE_API_KEY"):
        return 0
    enriched = 0
    for i, r in df_top.iterrows():
        if enriched >= max_calls:
            break
        name = r.get("address") or r.get("occupier_name")
        if not name or not isinstance(name, str):
            continue
        items, status = connectors.companies_house_search(name, items=1)
        if items:
            it = items[0]
            df_top.at[i, "occupier_name"] = it.get("title")
            df_top.at[i, "companies_house_id"] = it.get("company_number")
            df_top.at[i, "company_status"] = it.get("company_status")
            df_top.at[i, "occupier_source"] = "companies_house_search"
            enriched += 1
    return enriched
