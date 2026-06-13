"""
Agent 1 — Territory Intake.

Resolves a free-text area (town / postcode / outward code / local authority) to a
concrete run configuration: bounding box, local-authority code, postcode prefixes.
Never invents a bounding box — if an area cannot be resolved from local data it
fails explicitly with guidance.
"""
from __future__ import annotations

import re
import duckdb

from . import settings


VALID_CATEGORIES = [
    "warehouse", "industrial", "office", "retail", "school", "university",
    "leisure", "public", "mixed",
]
VALID_RANKING_MODES = ["balanced", "large-project-first", "fastest-payback", "highest-confidence"]


def _onspd_bbox_for_prefixes(prefixes: list[str]) -> dict | None:
    """Derive a bbox from the local ONSPD slice for given outward-code prefixes."""
    onspd = settings.source_path("onspd")
    if not onspd or not onspd.exists():
        return None
    like = " OR ".join(["REPLACE(postcode,' ','') LIKE ?" for _ in prefixes])
    params = [p.upper().replace(" ", "") + "%" for p in prefixes]
    con = duckdb.connect()
    try:
        row = con.execute(
            f"SELECT MIN(latitude),MAX(latitude),MIN(longitude),MAX(longitude),COUNT(*) "
            f"FROM read_parquet('{onspd.as_posix()}') WHERE {like}",
            params,
        ).fetchone()
    finally:
        con.close()
    if not row or not row[4]:
        return None
    return {"south": row[0], "north": row[1], "west": row[2], "east": row[3]}


def resolve_territory(area: str, *, categories=None, leads=100, scenario="central",
                      ranking_mode="balanced", min_kwp=None, min_usable_roof_area=None,
                      clean_run=False, dry_run=False, limit_candidates=None,
                      use_lidar=False) -> dict:
    """Return a run-config dict or raise ValueError with actionable guidance."""
    raw = (area or "").strip()
    if not raw:
        raise ValueError("No area provided. Example: --area \"Oxford\".")

    key = raw.lower().strip()
    territory = None

    # 1) Known territory registry (curated, with verified bbox + LAD code).
    if key in settings.TERRITORIES:
        territory = dict(settings.TERRITORIES[key])

    # 2) Outward code / postcode (e.g. "OX2", "OX1 2JD") → derive bbox from ONSPD.
    if territory is None and re.match(r"^[A-Z]{1,2}\d", raw.upper()):
        outward = raw.upper().split()[0]
        bbox = _onspd_bbox_for_prefixes([outward])
        if bbox:
            territory = {"name": outward, "local_authority": None, "lad_code": None,
                         "postcode_prefixes": [outward], "bbox": bbox}

    # 3) Town/LAD name partial match against the registry's prefixes via ONSPD.
    if territory is None:
        # Best effort: if the name matches a registry entry loosely, use it.
        for tkey, tval in settings.TERRITORIES.items():
            if tkey in key or key in tval["name"].lower():
                territory = dict(tval)
                break

    if territory is None:
        raise ValueError(
            f"Could not resolve area '{area}' from local data. "
            f"Supported now: {sorted(settings.TERRITORIES)} or a postcode/outward code "
            f"(e.g. 'OX2'). To add a town, extend commercial.settings.TERRITORIES "
            f"with its LAD code and bbox, or load the national ONSPD."
        )

    # Validate / normalise options.
    cats = [c for c in (categories or []) if c in VALID_CATEGORIES]
    if categories and not cats:
        raise ValueError(f"No valid categories in {categories}. Valid: {VALID_CATEGORIES}")
    if ranking_mode not in VALID_RANKING_MODES:
        raise ValueError(f"ranking_mode '{ranking_mode}' invalid. Valid: {VALID_RANKING_MODES}")
    if scenario not in ("conservative", "central", "high"):
        raise ValueError(f"scenario '{scenario}' invalid. Valid: conservative|central|high")

    cfg = {
        "area": territory["name"],
        "area_input": raw,
        "local_authority": territory.get("local_authority"),
        "lad_code": territory.get("lad_code"),
        "postcode_prefixes": territory.get("postcode_prefixes", []),
        "bbox": territory["bbox"],
        "categories": cats,                 # empty list = all categories
        "leads": int(leads),
        "scenario": scenario,
        "ranking_mode": ranking_mode,
        "min_kwp": float(min_kwp) if min_kwp else None,
        "min_usable_roof_area": float(min_usable_roof_area) if min_usable_roof_area else None,
        "clean_run": bool(clean_run),
        "dry_run": bool(dry_run),
        "limit_candidates": int(limit_candidates) if limit_candidates else None,
        "use_lidar": bool(use_lidar),
    }
    return cfg
