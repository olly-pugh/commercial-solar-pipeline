# Commercial Solar Lead Prioritisation pipeline

A parallel, **commercial-property** solar lead pipeline alongside the original
domestic workflow (which is preserved unchanged under
`data/pipeline/_archive/`). Deterministic Python produces every number; AI is used
only for narrative/red-team. **Lead prioritisation — not adoption prediction.**

## Quick start
```bash
# Are we ready? (presence only — never prints secrets)
python3 scripts/check_commercial_readiness.py --area Oxford --live

# Run Oxford (footprint+height roof model, fast)
python3 run_pipeline.py --mode commercial --area "Oxford" --leads 100 --scenario central

# High-confidence roofs from EA LiDAR for large buildings (slower)
python3 run_pipeline.py --mode commercial --area "Oxford" --leads 100 --lidar

# Prioritise the biggest viable projects
python3 run_pipeline.py --mode commercial --area "Oxford" --ranking-mode large-project-first

# Only sites >= 100 kWp
python3 run_pipeline.py --mode commercial --area "Oxford" --min-kwp 100

# Fast dry-run (no live APIs)
python3 run_pipeline.py --mode commercial --area "Oxford" --leads 20 --dry-run --limit-candidates 300

# Dashboard
python3 -m commercial.dashboard --port 5055        # http://127.0.0.1:5055

# Tests
pytest tests/commercial -q
```

## Agent / stage sequence
`territory → discovery → roof → economics → project_scale → business → ownership →
policy → momentum → scoring → enrich → calibration → report → qa`

(Economics runs before project-scale deliberately — see methodology §4.)

The **enrich** stage attaches, for the top leads (lawful public-register only):
- a real **address** — the registered-office street of a company registered at the
  lead's postcode (Companies House `advanced-search`), else an approximate
  grid-reference + postcode label (`address_source` records which);
- exact **location links** (Google Maps pin, Street View, OpenStreetMap) from the
  building centroid;
- **company links** — each company registered at the postcode with a direct
  Companies House URL. A registered office at a postcode is **not** proof of
  ownership/occupancy and is labelled as such (caveat tag added per lead).

| Module | Role |
|---|---|
| `territory.py` | Resolve area/postcode → bbox, LA, run config |
| `discovery.py` | Non-domestic candidates from OS MasterMap; domestic exclusion; reverse-geocode; UPRN; EPC refine |
| `roof.py` + `lidar_roof.py` | Roof geometry & PV potential (footprint+height model, or measured EA LiDAR) → technical sub-score |
| `economics.py` | Demand, self-consumption, CAPEX, payback, carbon → economics sub-score |
| `project_scale.py` | Saturating, penalised opportunity-SIZE score (feasible kWp) |
| `business.py` | Sector ESG + energy intensity (+ Companies House) → propensity |
| `ownership.py` | Owner-occupier vs tenant inference → decision feasibility |
| `policy.py` | EPC/MEES + ESG + heritage/planning risk |
| `momentum.py` | Local installer / peer PV (small weight) + calibration sample |
| `scoring.py` | Weighted final score, confidence/completeness/proxy, ranking modes |
| `calibration.py` | Honest correlation vs known PV adoption (inconclusive if no ground truth) |
| `report.py` | Markdown/HTML report, charts, CSV/XLSX |
| `qa.py` | Deterministic blocking checks → PASS/WARN/FAIL |
| `pipeline.py` | Orchestrator (audit trail, progress, missing-data register) |

## Outputs (per run)
- `runs/commercial/<run_id>/` — `territory_config.json`, `agent_audit_log.json`,
  `missing_data.json`, `progress.json`, `score_explainability.json`,
  `calibration.json`, `qa_report.json`, `red_team_report.md`, `all_scored.csv`,
  `domestic_exclusions.csv`, `result.json`
- `reports/commercial/<run_id>/` — `commercial_report_*.html` (executive-grade,
  per-lead company + map links, embedded charts), `commercial_report_*.pdf`
  (McKinsey/academic LaTeX build, when `pdflatex` is present), `*.md`,
  `commercial_leads_*.csv` / `.xlsx` (incl. `address`, `primary_company_*`,
  `location_*_url`), `charts/`

## Dashboard
`python3 -m commercial.dashboard --port 5055 --host 0.0.0.0` serves a single-page UI
that auto-loads the latest run, embeds the full interactive HTML report, shows a
ranked lead table with clickable company + map links, and links HTML/PDF/CSV/XLSX.
It can also launch new runs and stream per-stage progress.

## Demo mode (`/demo`) — presentation only, fully isolated
`/demo` is a guided, **simulated** walkthrough for showcasing the platform: a
chat-style entry page → a "Great! / Oxford report coming right up!" transition →
a ~60 s fake weighted progress bar across the 12 agents → an animated Oxford lead
map → the **existing** Oxford report (reused, never regenerated).

It is structurally incapable of running the pipeline: the page makes only GET
requests, never references `/api/run` or `run_pipeline`, and the sole data route
`/api/demo/leads` is read-only (it reads coordinates from the already-generated
showcase CSV; falls back to placeholder Oxford coords). Append `?fast=1` to
compress timings for testing.

- Python contract tests: `pytest tests/commercial -q` (isolation + content).
- Browser E2E (Playwright): `npm i -D playwright && npx playwright install chromium`,
  then with the dashboard running, `node tests/demo/demo_e2e.js`.

## Config (all tunables, documented)
`config/commercial_data_sources.yml`, `commercial_scoring.yml`,
`commercial_archetypes.yml`, `commercial_tariffs.yml`, `commercial_pv_costs.yml`.

See `docs/commercial_solar_methodology.md` and
`docs/commercial_api_and_data_requirements.md`.
