"""
Agent 2 — Commercial Candidate Discovery.

Identifies non-domestic ("commercial") buildings in the territory from OS MasterMap
footprints + building heights, excludes obvious domestic stock (with a logged
exclusion register, never a silent drop), reverse-geocodes postcode, joins UPRN,
assigns a PROVISIONAL building category, and (where available) confirms/refines
against non-domestic EPC.

Honesty: with no non-domestic EPC, commercial classification is HEURISTIC
(footprint size + height + absence of domestic match). category_confidence and
classification_confidence reflect that explicitly.
"""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from . import settings, refdata, connectors
from .geo import bng_to_wgs, os_grid_ref


def _assign_archetype(area_m2: float, height_m: float, rules: list) -> tuple[str, float]:
    h = height_m if height_m and height_m > 0 else 0.0
    for r in rules:
        if (area_m2 >= r["min_area_m2"] and r["min_height_m"] <= h <= r["max_height_m"]):
            return r["archetype"], float(r["category_confidence"])
    return "generic_commercial", 0.20


def _refine_archetype_from_text(text: str, kw_map: dict) -> tuple[str, float] | None:
    if not text:
        return None
    t = text.lower()
    for kw, arch in kw_map.items():
        if kw in t:
            return arch, 0.75
    return None


def discover(cfg: dict, audit=None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Return (candidates_df, excluded_df, stats)."""
    arche = settings.archetypes()
    scoring = settings.scoring()
    disc = scoring["discovery"]
    rules = arche["geometry_rules"]
    kw_map = arche["keyword_to_archetype"]

    db = settings.source_path("os_mastermap_buildings")
    if not db or not db.exists():
        raise FileNotFoundError(
            f"OS MasterMap buildings DB not found ({db}). This source is REQUIRED. "
            f"Set OS_MASTERMAP_BUILDINGS_DB or place data/db/buildings_oxford.duckdb."
        )

    bbox = cfg["bbox"]
    excl_terms = disc["exclude_descriptive_terms"]
    placeholders = ",".join(["?"] * len(excl_terms))

    con = duckdb.connect(str(db), read_only=True)
    q = f"""
        SELECT toid, descriptivegroup, descriptiveterm, area_m2,
               relh2, relhmax, abshmax, easting, northing, lat, lon, wkt
        FROM buildings
        WHERE descriptivegroup LIKE '%Building%'
          AND area_m2 >= ?
          AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
          AND (descriptiveterm IS NULL OR descriptiveterm NOT IN ({placeholders}))
        ORDER BY area_m2 DESC
    """
    params = [disc["min_footprint_m2"], bbox["south"], bbox["north"],
              bbox["west"], bbox["east"], *excl_terms]
    df = con.execute(q, params).fetchdf()
    con.close()

    if cfg.get("limit_candidates"):
        df = df.head(cfg["limit_candidates"]).copy()

    if df.empty:
        return df, pd.DataFrame(), {"raw_candidates": 0, "commercial": 0, "excluded_domestic": 0}

    # Height: prefer ridge (relhmax), fall back to eaves (relh2).
    df["height_m"] = df["relhmax"].fillna(df["relh2"]).fillna(0.0)
    df["footprint_area_m2"] = df["area_m2"].round(1)

    # Reverse geocode (postcode) + UPRN join — vectorised.
    pc = refdata.batch_nearest_postcode(df["easting"].values, df["northing"].values)
    pcdf = pd.DataFrame(pc, index=df.index)
    df = pd.concat([df, pcdf], axis=1)
    uprn, udist = refdata.batch_nearest_uprn(
        df["easting"].values, df["northing"].values, disc["uprn_join_tolerance_m"])
    df["uprn"] = uprn
    df["uprn_distance_m"] = udist

    dwellings = refdata.domestic_dwellings_by_postcode()
    df["pc_norm"] = df["postcode"].fillna("").str.replace(" ", "", regex=False).str.upper()
    df["domestic_density"] = df["pc_norm"].map(lambda p: dwellings.get(p, 0))

    # ── Classification (commercial vs residential) with explicit evidence ─────
    rows_keep, rows_excl = [], []
    for _, r in df.iterrows():
        area = r["footprint_area_m2"]
        h = r["height_m"]
        dens = r["domestic_density"]
        comm_ev, dom_ev = [], []

        if area >= disc["clearly_commercial_m2"]:
            comm_ev.append("large_footprint>=%dm2" % disc["clearly_commercial_m2"])
        if h >= disc["office_min_height_m"] and area >= disc["office_min_area_m2"]:
            comm_ev.append("office_like_height")
        if dens < disc["domestic_dense_postcode_dwellings"]:
            comm_ev.append("not_in_dense_residential_postcode")
        if dens >= disc["domestic_dense_postcode_dwellings"]:
            dom_ev.append("dense_residential_postcode(%d)" % dens)
        if (area < disc["medium_band_max_m2"] and h < disc["residential_max_height_m"]
                and dens >= disc["domestic_dense_postcode_dwellings"]):
            dom_ev.append("medium_low_building_in_residential_area")

        # Decision: clearly-commercial always kept; medium band needs commercial evidence
        if area >= disc["clearly_commercial_m2"]:
            keep = True
        elif (h >= disc["office_min_height_m"] and area >= disc["office_min_area_m2"]):
            keep = True
        else:  # medium band 250..700, lower buildings
            keep = len(comm_ev) > len(dom_ev)

        arch, aconf = _assign_archetype(area, h, rules)
        # mixed-use flag for medium buildings in residential areas
        mixed = bool(dom_ev) and keep
        cls_conf = round(min(0.9, 0.3 + 0.15 * len(comm_ev) - 0.1 * len(dom_ev)
                             + (0.2 if area >= 1500 else 0)), 2)
        cls_conf = max(0.1, cls_conf)

        e, n = r["easting"], r["northing"]
        rec = {
            "commercial_property_id": f"OXF-C-{r['toid']}",
            "building_id": r["toid"],
            "uprn": r["uprn"],
            "uprn_distance_m": r["uprn_distance_m"],
            "postcode": r["postcode"],
            "postcode_confidence": r["postcode_confidence"],
            "lsoa_code": r["lsoa_code"], "msoa_code": r["msoa_code"], "ward_code": r["ward_code"],
            "local_authority": cfg.get("local_authority"),
            "lat": round(float(r["lat"]), 6), "lon": round(float(r["lon"]), 6),
            "easting": e, "northing": n,
            "os_grid_ref": os_grid_ref(e, n),
            "descriptive_term": r["descriptiveterm"],
            "footprint_area_m2": area,
            "height_m": round(float(h), 1),
            "wkt": r["wkt"],
            "property_category": arch,
            "category_confidence": aconf,
            "category_source": "geometry_heuristic",
            "classification_confidence": cls_conf,
            "commercial_evidence": ";".join(comm_ev) or "none",
            "domestic_evidence": ";".join(dom_ev) or "none",
            "mixed_use_flag": mixed,
            "address": None,            # filled by EPC/VOA enrichment if available
            "os_mastermap_match_confidence": 0.9,  # authoritative footprint present
        }
        (rows_keep if keep else rows_excl).append(
            rec if keep else {**rec, "exclusion_reason": ";".join(dom_ev) or "below_commercial_evidence"})

    cands = pd.DataFrame(rows_keep)
    excluded = pd.DataFrame(rows_excl)

    # ── Optional non-domestic EPC enrichment (confirm + refine category) ──────
    epc_status = "not_attempted"
    epc_matched = 0
    if cfg.get("lad_code") and not cfg.get("dry_run"):
        epc_rows, epc_status = connectors.epc_nondomestic(cfg["lad_code"], max_rows=5000)
        if epc_rows:
            epc_matched = _enrich_with_epc(cands, epc_rows, kw_map)

    if epc_status in ("unavailable", "not_attempted") and audit is not None:
        audit.record_missing(
            "epc_nondomestic", required=False,
            impact="Building category, floor area, EPC band and MEES pressure are "
                   "geometry-inferred only; confidence reduced.",
            degraded_behaviour="Category from footprint+height heuristic; EPC fields=UNKNOWN.")

    # Category-filter if the user requested specific categories.
    if cfg.get("categories"):
        wanted = _expand_category_filter(cfg["categories"])
        cands = cands[cands["property_category"].isin(wanted)].reset_index(drop=True)

    stats = {
        "raw_candidates": int(len(df)),
        "commercial": int(len(cands)),
        "excluded_domestic": int(len(excluded)),
        "epc_status": epc_status,
        "epc_matched": int(epc_matched),
        "category_breakdown": cands["property_category"].value_counts().to_dict() if not cands.empty else {},
        "min_footprint_m2": disc["min_footprint_m2"],
    }
    return cands.reset_index(drop=True), excluded, stats


def _expand_category_filter(user_categories: list) -> set:
    """Map user-facing category names to internal archetypes."""
    m = {
        "warehouse": {"warehouse_logistics", "cold_storage"},
        "industrial": {"manufacturing_industrial"},
        "office": {"office"},
        "retail": {"retail_shed", "retail_highstreet", "supermarket_food"},
        "school": {"school"},
        "university": {"university_higher_ed"},
        "leisure": {"leisure_gym"},
        "public": {"public_sector"},
        "mixed": {"mixed_commercial", "generic_commercial"},
    }
    out = set()
    for c in user_categories:
        out |= m.get(c, set())
    return out


def _enrich_with_epc(cands: pd.DataFrame, epc_rows: list, kw_map: dict) -> int:
    """Match EPC records to candidates by UPRN, then refine category/address/floor area."""
    by_uprn = {}
    for e in epc_rows:
        u = e.get("uprn") or e.get("building-reference-number")
        if u:
            try:
                by_uprn[int(u)] = e
            except (ValueError, TypeError):
                pass
    matched = 0
    for i, row in cands.iterrows():
        u = row["uprn"]
        if u is None or int(u) not in by_uprn:
            continue
        e = by_uprn[int(u)]
        matched += 1
        cands.at[i, "address"] = e.get("address") or e.get("address1")
        fa = e.get("floor-area") or e.get("floor_area")
        if fa:
            try:
                cands.at[i, "epc_floor_area_m2"] = float(fa)
            except (ValueError, TypeError):
                pass
        cands.at[i, "epc_asset_rating"] = e.get("asset-rating-band") or e.get("current-energy-rating")
        cands.at[i, "category_source"] = "epc_confirmed"
        cands.at[i, "category_confidence"] = 0.85
        ref = _refine_archetype_from_text(e.get("property-type", ""), kw_map)
        if ref:
            cands.at[i, "property_category"] = ref[0]
    return matched
