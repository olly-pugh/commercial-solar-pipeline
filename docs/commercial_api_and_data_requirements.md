# Commercial Pipeline — API Keys & Data Requirements

What the commercial pipeline needs, where to get it, how to test it, and what happens
when it is absent. The registry of record is `config/commercial_data_sources.yml`.
Check live status any time with:

```bash
python3 scripts/check_commercial_readiness.py --area Oxford --live
```

Secrets live in `.env` (git-ignored). Names only are in `.env.example`. The checker
prints key **presence**, never values.

---

## Required (pipeline cannot run without these)

### 1. OS MasterMap buildings (footprints + heights)
- **Why:** authoritative building geometry — footprint area, eaves/ridge height, WKT.
  Drives discovery, roof, and project-scale.
- **Env/path:** `OS_MASTERMAP_BUILDINGS_DB` → `data/db/buildings_oxford.duckdb` (table `buildings`).
- **Format:** DuckDB; columns `toid, descriptivegroup, descriptiveterm, area_m2, relh2,
  relhmax, abshmax, easting, northing, lat, lon, wkt`.
- **Test:** readiness checker reports building count (Oxford: ~106,260).
- **If absent:** **cannot run.** Provide the DB or set `OS_DATA_HUB_API_KEY` to fetch via OS NGD/Features.

### 2. ONS Postcode Directory (slice)
- **Why:** postcode ↔ lat/lon + LSOA/MSOA/ward; reverse-geocode building → postcode; LA resolution.
- **Env/path:** `ONSPD_PATH` → `data/onspd/onspd_oxford.parquet`.
- **Test:** readiness checker (Oxford: ~20,463 postcodes).
- **If absent:** falls back to `postcodes.io`; if that fails, postcode = UNRESOLVED (lead still located by lat/lon + OS grid ref).

---

## Recommended (pipeline runs in **degraded** mode without them)

### 3. EA LiDAR DSM/DTM — `EA_LIDAR_DSM_DIR` → `data/lidar/dsm`
- HIGH-confidence roof geometry (measured pitch/azimuth/usable area). Enable with `--lidar`.
- **Present** for Oxford (23 × 1 m DSM tiles). Without it/`--lidar`: footprint+height model (MEDIUM confidence, labelled `roof_method=footprint_height`).

### 4. Non-domestic EPC — `EPC_BEARER_TOKEN`, `EPC_API`
- Confirms commercial use, floor area, asset rating, MEES relevance; raises category & economics confidence.
- **Status (2026-06):** legacy host `epc.opendatacommunities.org` **retired 30 May 2026**; the new host
  `api.get-energy-performance-data.communities.gov.uk` requires the correct path + Basic `base64(email:apikey)`
  auth. The supplied token returned 401/404 in probing. **Action needed:** confirm the new endpoint and
  supply `email:apikey` (Basic) — see `gov.uk` "Get energy performance of buildings data".
- **If absent:** category from geometry heuristic; EPC band/MEES = UNKNOWN (caveat); confidence reduced.

### 5. Companies House — `COMPANIES_HOUSE_API_KEY`
- Lawful public-register occupier identity/SIC/status/age for top leads. **Working** (verified live).
- **If absent:** propensity scored at sector level; occupier = UNKNOWN.

### 6. PVGIS — `PVGIS_API` (no key needed). **Working** (verified live; Oxford ~908 kWh/kWp).
- **If absent:** documented climatology fallback in `commercial_pv_costs.yml`.

### 7. Planning constraints — `PLANNING_DATA_API` / `data/heritage/*.json`
- Conservation-area / listed-building proximity. **Present** (Oxford heritage JSON, 10,281 features).
- **If absent:** planning_risk = UNKNOWN (never assumed clear).

### 8. OS Open UPRN — `OS_OPEN_UPRN_CSV` → `data/os_uprn/oxford_uprn.csv`
- UPRN identity via spatial join. **Present** (~132,190 Oxford UPRNs).
- **If absent:** lead identified by OS TOID + grid ref; UPRN = UNKNOWN.

### 9. MCS installer register — `MCS_INSTALLERS_CSV` / `data/db/mcs.duckdb`
- Local installer availability (market momentum, small weight). **Present** (12 installers).
- ⚠ Holds installer **companies**, not installation locations → **not** PV-adoption ground truth.

---

## Optional / not currently available

### 10. VOA non-domestic rating list — `VOA_RATING_LIST_PATH`
- Would confirm hereditament type/floor area/rateable value and improve tenure inference.
- **Status:** the local `voa.duckdb` holds **council-tax (domestic)** bands only. Non-domestic
  list not present → tenure inferred from type+size (low confidence). Obtain from VOA open data.

### 11. Commercial PV ground truth (calibration only) — `COMMERCIAL_PV_GROUND_TRUTH` → `data/commercial/ground_truth_pv.csv`
- Columns: `uprn_or_toid, has_commercial_pv (0/1), system_kwp`.
- **If absent (current):** calibration = `INCONCLUSIVE_NO_GROUNDTRUTH` + QA warning. Correlations are never fabricated.
- **To enable:** export MCS *installations* (with location), FiT/SEG, or a verified imagery survey for the area.

---

## Degraded-mode summary for Oxford (today)
Can run **now**: ✅ discovery, roof (footprint+height or `--lidar`), project-scale, economics,
propensity (sector), feasibility, policy (heritage), momentum, scoring, report, QA.
Honest gaps: ❌ non-domestic EPC (auth/endpoint), ❌ VOA non-domestic, ❌ calibration ground truth.
