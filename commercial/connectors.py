"""
External data-source connectors with on-disk caching and graceful degradation.

Every connector:
  * uses the env keys declared in config/commercial_data_sources.yml,
  * caches responses under data/cache/commercial/<source>/,
  * NEVER raises on network failure — returns (data, status) where status is one
    of: ok | degraded | unavailable, so the pipeline can record missing data
    instead of silently inventing it,
  * NEVER logs secret values.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import settings


def _cache_dir(name: str) -> Path:
    d = settings.CACHE_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get(url: str, headers: dict | None = None, timeout: int = 25) -> tuple[bytes | None, int]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), r.status
    except Exception as e:  # noqa: BLE001 - all network errors are non-fatal here
        return None, getattr(e, "code", -1)


# ─────────────────────────────────────────────────────────────────────────────
# Non-domestic EPC  (official gov EPC Open Data API)
# ─────────────────────────────────────────────────────────────────────────────
def epc_nondomestic(local_authority_code: str, max_rows: int = 5000,
                    refresh: bool = False) -> tuple[list[dict], str]:
    """
    Fetch non-domestic EPC certificates for a local authority.
    Returns (rows, status). status in {ok, degraded, unavailable}.
    """
    cache = _cache_dir("epc_nondom") / f"{local_authority_code}.json"
    if cache.exists() and not refresh:
        try:
            return json.loads(cache.read_text()), "ok"
        except Exception:
            pass

    token = settings.env("EPC_BEARER_TOKEN")
    if not token:
        return [], "unavailable"

    # The non-domestic EPC service moved hosts (legacy epc.opendatacommunities.org
    # retired 30 May 2026 → new get-energy-performance-data service). The exact
    # host/path/auth scheme for the provided token is not statically known, so we
    # probe a small documented set of (base, auth) combinations and use the first
    # that returns rows. On total failure we degrade (never invent EPC data).
    env_api = (settings.env("EPC_API") or "").rstrip("/")
    bases = [b for b in [
        env_api,
        env_api + "/api/v1" if env_api and "/api/v1" not in env_api else None,
        "https://api.get-energy-performance-data.communities.gov.uk/api/v1",
        "https://epc.opendatacommunities.org/api/v1",
    ] if b]
    basic = token if token.lower().startswith("basic ") else f"Basic {token}"
    auths = [f"Bearer {token}", basic]

    def _try(base: str, auth: str) -> list[dict]:
        rows: list[dict] = []
        after = None
        for _ in range(max_rows // 1000 + 1):
            q = {"local-authority": local_authority_code, "size": 1000}
            if after:
                q["search-after"] = after
            url = f"{base}/non-domestic/search?" + urllib.parse.urlencode(q)
            body, status = _get(url, headers={"Authorization": auth, "Accept": "application/json"})
            if body is None:
                break
            try:
                data = json.loads(body)
            except Exception:
                break
            chunk = data.get("rows", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            if not chunk:
                break
            rows.extend(chunk)
            after = chunk[-1].get("lmk-key") or chunk[-1].get("lmk_key")
            if len(rows) >= max_rows or not after:
                break
            time.sleep(0.2)
        return rows

    for base in bases:
        for auth in auths:
            rows = _try(base, auth)
            if rows:
                cache.write_text(json.dumps(rows))
                return rows, "ok"
    return [], "unavailable"


# ─────────────────────────────────────────────────────────────────────────────
# Companies House
# ─────────────────────────────────────────────────────────────────────────────
def companies_house_search(query: str, items: int = 5) -> tuple[list[dict], str]:
    key = settings.env("COMPANIES_HOUSE_API_KEY")
    if not key:
        return [], "unavailable"
    cache = _cache_dir("companies_house") / (
        urllib.parse.quote(query.lower(), safe="") + ".json")
    if cache.exists():
        try:
            return json.loads(cache.read_text()), "ok"
        except Exception:
            pass
    auth = "Basic " + base64.b64encode(f"{key}:".encode()).decode()
    url = ("https://api.company-information.service.gov.uk/search/companies?"
           + urllib.parse.urlencode({"q": query, "items_per_page": items}))
    body, status = _get(url, headers={"Authorization": auth})
    if body is None:
        return [], "degraded"
    try:
        data = json.loads(body)
        out = data.get("items", [])
        cache.write_text(json.dumps(out))
        return out, "ok"
    except Exception:
        return [], "degraded"


def companies_house_by_postcode(postcode: str, size: int = 20) -> tuple[list[dict], str]:
    """Companies whose REGISTERED OFFICE is at a given postcode (Companies House
    advanced-search). Public statutory register only. NOTE: a registered office at
    a postcode is NOT proof of property ownership/occupancy — callers must label it
    as such. Returns (items, status). Cached on disk by postcode."""
    key = settings.env("COMPANIES_HOUSE_API_KEY")
    if not key:
        return [], "unavailable"
    pc = (postcode or "").strip().upper()
    if not pc:
        return [], "unavailable"
    cache = _cache_dir("companies_house") / ("loc_" + urllib.parse.quote(pc, safe="") + ".json")
    if cache.exists():
        try:
            return json.loads(cache.read_text()), "ok"
        except Exception:
            pass
    auth = "Basic " + base64.b64encode(f"{key}:".encode()).decode()
    url = ("https://api.company-information.service.gov.uk/advanced-search/companies?"
           + urllib.parse.urlencode({"location": pc, "size": size}))
    body, status = _get(url, headers={"Authorization": auth})
    if body is None:
        return [], "degraded"
    try:
        items = json.loads(body).get("items", [])
        cache.write_text(json.dumps(items))
        return items, "ok"
    except Exception:
        return [], "degraded"


# ─────────────────────────────────────────────────────────────────────────────
# PVGIS specific yield (kWh per kWp per year)
# ─────────────────────────────────────────────────────────────────────────────
def pvgis_specific_yield(lat: float, lon: float, tilt: float = 12.0,
                         azimuth_deg_from_south: float = 0.0,
                         losses: float = 14.0) -> tuple[float | None, str]:
    """
    Returns (specific_yield_kwh_per_kwp, status). Falls back to None/'unavailable'
    so the economics agent uses the documented climatology table instead.
    PVGIS 'aspect' is degrees from south (-90 E .. +90 W).
    """
    cache = _cache_dir("pvgis") / f"{lat:.3f}_{lon:.3f}_{int(tilt)}_{int(azimuth_deg_from_south)}.json"
    if cache.exists():
        try:
            return float(cache.read_text()), "ok"
        except Exception:
            pass
    api = settings.env("PVGIS_API", "https://re.jrc.ec.europa.eu/api/v5_2")
    url = (f"{api.rstrip('/')}/PVcalc?" + urllib.parse.urlencode({
        "lat": lat, "lon": lon, "peakpower": 1, "loss": losses,
        "angle": tilt, "aspect": azimuth_deg_from_south,
        "outputformat": "json", "pvtechchoice": "crystSi", "mountingplace": "building",
    }))
    body, status = _get(url, timeout=20)
    if body is None:
        return None, "unavailable"
    try:
        data = json.loads(body)
        ey = data["outputs"]["totals"]["fixed"]["E_y"]  # kWh/yr for 1 kWp
        cache.write_text(str(ey))
        return float(ey), "ok"
    except Exception:
        return None, "degraded"
