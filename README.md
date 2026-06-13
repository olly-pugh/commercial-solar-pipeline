# Commercial Solar Lead Pipeline

A deterministic Python pipeline that identifies, scores, and prioritises commercial
buildings (warehouses, offices, retail, industrial, schools, leisure, public, mixed-use)
for rooftop solar PV installation.

**This is lead prioritisation, not adoption prediction.** Every number is produced by
deterministic Python. AI is used only for narrative summarisation and red-team critique,
never as a source of a score.

---

## What it does

The pipeline takes a UK local authority area and outputs a ranked list of the most
commercially viable non-domestic sites for solar, complete with:

- Roof geometry and PV potential (footprint+height model or EA LiDAR)
- Project economics: demand, self-consumption, CAPEX, payback, carbon saving
- Business context: sector ESG orientation, energy intensity, Companies House data
- Ownership and tenancy inference (owner-occupier vs tenant)
- Policy risk: EPC/MEES duty, heritage and planning constraints
- Confidence and data-completeness scores per lead
- Executive HTML/PDF reports, ranked CSV/XLSX, and an interactive dashboard

---

## Pipeline stages

```
territory -> discovery -> roof -> economics -> project_scale -> business
         -> ownership -> policy -> momentum -> scoring -> enrich
         -> calibration -> report -> qa
```

| Module | Role |
|---|---|
| `territory.py` | Resolve area/postcode to bounding box, LA, run config |
| `discovery.py` | Non-domestic candidates from OS MasterMap; domestic exclusion; reverse-geocode; UPRN; EPC refinement |
| `roof.py` / `lidar_roof.py` | Roof geometry and PV potential (footprint+height or measured EA LiDAR) |
| `economics.py` | Demand, self-consumption, CAPEX, payback, carbon saving |
| `project_scale.py` | Saturating, penalised opportunity-size score (feasible kWp) |
| `business.py` | Sector ESG + energy intensity + Companies House |
| `ownership.py` | Owner-occupier vs tenant inference |
| `policy.py` | EPC/MEES + ESG + heritage/planning risk |
| `momentum.py` | Local installer and peer PV signal (small weight) |
| `scoring.py` | Weighted final score, confidence/completeness/proxy, ranking modes |
| `enrich.py` | Address, location links, Companies House URLs for top leads |
| `calibration.py` | Honest correlation vs known PV adoption |
| `report.py` | Markdown/HTML report, charts, CSV/XLSX |
| `qa.py` | Deterministic blocking checks: PASS/WARN/FAIL |
| `pipeline.py` | Orchestrator with audit trail, progress, missing-data register |

---

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in the API keys you have.
# The pipeline runs in degraded mode without optional keys.
```

Required: `OS_MASTERMAP_BUILDINGS_DB` (local DuckDB of OS MasterMap building footprints).
Everything else is optional with graceful fallbacks. See `docs/commercial_api_and_data_requirements.md`.

### 3. Check readiness

```bash
python3 scripts/check_commercial_readiness.py --area Oxford --live
```

### 4. Run the pipeline

```bash
# Standard run (footprint+height roof model, fast)
python3 run_pipeline.py --mode commercial --area "Oxford" --leads 100

# High-confidence roofs from EA LiDAR for large buildings (slower)
python3 run_pipeline.py --mode commercial --area "Oxford" --leads 100 --lidar

# Prioritise the biggest viable projects
python3 run_pipeline.py --mode commercial --area "Oxford" --ranking-mode large-project-first

# Only sites >= 100 kWp
python3 run_pipeline.py --mode commercial --area "Oxford" --min-kwp 100

# Fast dry-run (no live APIs, limited candidates)
python3 run_pipeline.py --mode commercial --area "Oxford" --leads 20 --dry-run --limit-candidates 300
```

### 5. Launch the dashboard

```bash
python3 -m commercial.dashboard --port 5055
# Open http://127.0.0.1:5055
```

The dashboard auto-loads the latest run, embeds the full HTML report, shows a ranked
lead table with clickable company and map links, and can launch new runs with live
per-stage progress streaming.

---

## Outputs

Each run writes to two directories:

**`runs/commercial/<run_id>/`**
- `territory_config.json` - resolved area and run parameters
- `agent_audit_log.json` - per-stage timing, record counts, warnings
- `missing_data.json` - register of absent data sources and fallbacks used
- `score_explainability.json` - per-lead score breakdown
- `calibration.json` - correlation vs known PV adoption
- `qa_report.json` - PASS/WARN/FAIL checks
- `all_scored.csv` - every candidate with all scores
- `result.json` - summary

**`reports/commercial/<run_id>/`**
- `commercial_report_*.html` - executive-grade report with embedded charts and links
- `commercial_report_*.pdf` - LaTeX PDF build (when pdflatex is present)
- `commercial_leads_*.csv` / `.xlsx` - ranked lead list with address, company, and map URLs
- `charts/` - individual chart images

---

## Demo mode

The dashboard includes a `/demo` route for presentations: a guided simulated walkthrough
showing a fake progress animation across the 12 pipeline stages, an animated Oxford lead
map, and the existing Oxford report. It never triggers real pipeline execution.

```bash
python3 -m commercial.dashboard --port 5055
# Then open http://127.0.0.1:5055/demo
# Append ?fast=1 to compress timings for testing
```

---

## Tests

```bash
# Unit and integration tests
pytest tests/commercial -q

# Browser end-to-end test for demo mode (requires Playwright)
npm i -D playwright && npx playwright install chromium
python3 -m commercial.dashboard --port 5055 &
node tests/demo/demo_e2e.js
```

---

## Configuration

All tunables are documented YAML files in `config/`:

| File | Controls |
|---|---|
| `commercial_scoring.yml` | Stage weights and scoring parameters |
| `commercial_archetypes.yml` | Building category definitions and sector mappings |
| `commercial_tariffs.yml` | Electricity tariff scenarios (central/high/low) |
| `commercial_pv_costs.yml` | CAPEX, degradation, O+M assumptions |
| `commercial_data_sources.yml` | Data source registry with fallbacks and confidence weights |

---

## Data sources and API keys

See `docs/commercial_api_and_data_requirements.md` for the full registry.

Summary of optional APIs (all have graceful fallbacks):

| Source | Purpose | Key required |
|---|---|---|
| OS MasterMap (local DuckDB) | Building footprints and heights | No (file-based) |
| EA LiDAR (open data) | High-confidence roof geometry | No |
| EPC API (gov.uk) | Non-domestic EPC ratings | Yes (free registration) |
| Companies House API | Business identity and SIC codes | Yes (free) |
| PVGIS (JRC) | Irradiance modelling | No |
| planning.data.gov.uk | Conservation areas, listed buildings | No |
| OS Data Hub NGD | Footprints via API (if no local DB) | Yes |

---

## Methodology

See `docs/commercial_solar_methodology.md` for the full methodology, literature review,
score construction, calibration approach, and caveats.

Key design principles:

- Every number is traceable to a deterministic formula or a named public dataset.
- Missing data is registered and reported, never silently imputed.
- Calibration is reported honestly: "inconclusive" when no PV ground truth is available.
- AI is used only for text (narrative and red-team critique), never for scores.

---

## Licence

Code: MIT. Data derived from OS MasterMap requires an OS licence (Digimap educational
or commercial). EPC data is Crown Copyright. See individual source licences in
`docs/commercial_api_and_data_requirements.md`.
