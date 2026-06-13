"""
Lead enrichment — addresses, map/location links, and company links.

For the top-ranked leads we attach:
  * a human-readable ADDRESS (registered-office street from Companies House where a
    company is registered at the lead's postcode, else an approximate
    grid-reference + postcode label),
  * exact LOCATION links (Google Maps pin, Street View, OpenStreetMap) from the
    building centroid lat/lon,
  * COMPANY links — the companies whose statutory registered office sits at the
    lead's postcode (Companies House public register), each with a direct link.

IMPORTANT (honesty / lawfulness): a registered office at a postcode is NOT proof
that the company owns or occupies the building. Every company link is labelled as a
registered-office match and carries a caveat. We use the public statutory register
only — no personal contact details are scraped.
"""
from __future__ import annotations

import json

import pandas as pd

from . import connectors, settings

CH_COMPANY_URL = "https://find-and-update.company-information.service.gov.uk/company/{}"
CH_POSTCODE_SEARCH = ("https://find-and-update.company-information.service.gov.uk/"
                      "advanced-search/get-results?registeredOfficeAddress={}")

# SIC-prefix → plain-English industry (first two digits of the 5-digit SIC code).
_SIC2 = {
    "01": "Agriculture", "10": "Food manufacturing", "11": "Beverage manufacturing",
    "13": "Textiles", "14": "Apparel", "16": "Wood products", "17": "Paper",
    "18": "Printing", "20": "Chemicals", "21": "Pharmaceuticals", "22": "Plastics/rubber",
    "23": "Glass/ceramics", "24": "Metals", "25": "Fabricated metal", "26": "Electronics",
    "27": "Electrical equipment", "28": "Machinery", "29": "Motor vehicles",
    "30": "Other transport equip.", "31": "Furniture", "32": "Other manufacturing",
    "33": "Repair/installation", "35": "Energy/utilities", "41": "Construction (buildings)",
    "42": "Civil engineering", "43": "Specialised construction", "45": "Motor trade",
    "46": "Wholesale", "47": "Retail", "49": "Land transport", "52": "Warehousing/logistics",
    "55": "Hotels/accommodation", "56": "Food & beverage service", "58": "Publishing",
    "59": "Film/TV", "61": "Telecoms", "62": "IT/software", "63": "Information services",
    "64": "Financial services", "68": "Real estate", "69": "Legal/accounting",
    "70": "Head office/consulting", "71": "Architecture/engineering", "72": "Scientific R&D",
    "73": "Advertising", "74": "Professional/technical", "77": "Rental/leasing",
    "78": "Employment", "80": "Security/investigation", "82": "Business support",
    "84": "Public administration", "85": "Education", "86": "Health", "87": "Residential care",
    "90": "Arts", "91": "Libraries/museums", "93": "Sports/recreation", "94": "Membership orgs",
    "96": "Personal services",
}


def _industry(sic_codes) -> str:
    if not sic_codes:
        return ""
    out = []
    for c in (sic_codes if isinstance(sic_codes, list) else [sic_codes]):
        out.append(_SIC2.get(str(c)[:2], ""))
    out = [o for o in dict.fromkeys(out) if o]
    return " / ".join(out[:2])


def _office_address(addr: dict) -> str:
    parts = [addr.get("address_line_1"), addr.get("address_line_2"),
             addr.get("locality"), addr.get("postal_code")]
    return ", ".join(p for p in parts if p)


def _maps(lat, lon) -> dict:
    if lat is None or lon is None or pd.isna(lat) or pd.isna(lon):
        return {}
    return {
        "maps_url": f"https://www.google.com/maps/search/?api=1&query={lat:.6f},{lon:.6f}",
        "streetview_url": ("https://www.google.com/maps/@?api=1&map_action=pano"
                           f"&viewpoint={lat:.6f},{lon:.6f}"),
        "osm_url": f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=19/{lat:.6f}/{lon:.6f}",
    }


def _companies_for_postcode(postcode: str, max_companies: int = 6) -> list[dict]:
    """Active companies registered at this postcode, normalised + linked."""
    items, status = connectors.companies_house_by_postcode(postcode, size=20)
    if not items:
        return []
    active = [it for it in items if it.get("company_status") == "active"]
    rank = active or items
    out = []
    for it in rank[:max_companies]:
        num = it.get("company_number")
        out.append({
            "name": (it.get("company_name") or "").title(),
            "number": num,
            "status": it.get("company_status"),
            "type": it.get("company_type"),
            "incorporated": it.get("date_of_creation"),
            "industry": _industry(it.get("sic_codes")),
            "office": _office_address(it.get("registered_office_address") or {}),
            "url": CH_COMPANY_URL.format(num) if num else None,
        })
    return out


def enrich_leads(leads: pd.DataFrame, cfg: dict, audit=None,
                 max_leads: int = 120) -> tuple[pd.DataFrame, dict]:
    """Attach address, location links and company links to the top `max_leads`
    leads. Returns (leads, stats). Safe to call with no CH key (degrades to map
    links + approximate address only)."""
    # All of these hold strings/JSON; force object dtype so an all-NaN float column
    # (e.g. address, which discovery leaves empty) can accept string assignment.
    for col in ("address", "address_source", "location_maps_url", "location_streetview_url",
                "location_osm_url", "primary_company_name", "primary_company_number",
                "primary_company_url", "primary_company_industry", "companies_json",
                "n_companies_at_postcode", "ch_postcode_search_url"):
        if col not in leads.columns:
            leads[col] = None
        leads[col] = leads[col].astype(object)

    have_ch = settings.env_present("COMPANIES_HOUSE_API_KEY")
    top_idx = list(leads.index[:max_leads])
    n_company_hits = 0
    n_addr_real = 0

    for i in top_idx:
        r = leads.loc[i]
        pc = str(r.get("postcode") or "").strip()
        links = _maps(r.get("lat"), r.get("lon"))
        leads.at[i, "location_maps_url"] = links.get("maps_url")
        leads.at[i, "location_streetview_url"] = links.get("streetview_url")
        leads.at[i, "location_osm_url"] = links.get("osm_url")
        leads.at[i, "ch_postcode_search_url"] = (
            CH_POSTCODE_SEARCH.format(pc.replace(" ", "+")) if pc else None)

        companies = _companies_for_postcode(pc) if (have_ch and pc) else []
        leads.at[i, "companies_json"] = json.dumps(companies)
        leads.at[i, "n_companies_at_postcode"] = len(companies)

        if companies:
            n_company_hits += 1
            primary = companies[0]
            leads.at[i, "primary_company_name"] = primary["name"]
            leads.at[i, "primary_company_number"] = primary["number"]
            leads.at[i, "primary_company_url"] = primary["url"]
            leads.at[i, "primary_company_industry"] = primary["industry"]
            if primary.get("office"):
                leads.at[i, "address"] = primary["office"]
                leads.at[i, "address_source"] = "companies_house_registered_office"
                n_addr_real += 1

        cur = leads.at[i, "address"]
        if cur is None or (isinstance(cur, float) and pd.isna(cur)) or not str(cur).strip():
            grid = str(r.get("os_grid_ref") or "").strip()
            leads.at[i, "address"] = (f"Near {grid}, {pc}".strip(", ")
                                      if (grid or pc) else "Location by grid reference")
            leads.at[i, "address_source"] = "approximate_grid_postcode"

    # Mandatory honesty caveat on enriched leads carrying a company link.
    if "caveat_tags" in leads.columns:
        mask = leads["primary_company_name"].notna()
        leads.loc[mask, "caveat_tags"] = leads.loc[mask, "caveat_tags"].fillna("").astype(str).apply(
            lambda s: s + (";" if s else "") + "company_is_registered_office_at_postcode_not_confirmed_owner")

    if audit is not None and not have_ch:
        audit.record_missing(
            "companies_house", required=False,
            impact="Company links not attached to leads.",
            degraded_behaviour="Address falls back to grid-reference + postcode; map links still provided.")

    stats = {
        "enriched_leads": len(top_idx),
        "leads_with_company_link": n_company_hits,
        "leads_with_real_address": n_addr_real,
        "companies_house_available": have_ch,
    }
    return leads, stats
