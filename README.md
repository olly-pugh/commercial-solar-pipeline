# Commercial Solar Lead Pipeline

**Powered by OpenClaw** -- a 12-agent agentic AI workflow that turns a UK local authority
name into a ranked, installer-ready list of the highest-value commercial buildings for
rooftop solar PV installation.

This project was built to solve a concrete problem in **energy optimisation**: the
commercial rooftop solar market in the UK is severely under-penetrated, not because the
economics are weak, but because installers have no systematic way to identify and
prioritise the right buildings. Cold outreach wastes sales effort on unsuitable sites.
This pipeline fixes that.

> **Lead prioritisation, not adoption prediction.** Every score is produced by
> deterministic Python and is traceable to a named formula or public dataset. AI is used
> for narrative summarisation, red-team critique, and orchestration -- never as the source
> of a number.

---

## What is OpenClaw?

OpenClaw is the agentic AI framework that orchestrates this pipeline. Its central agent,
**OZ** (a senior solar-intelligence AI), coordinates 12 specialised sub-agents that run
sequentially -- each one completing its work and passing a structured output to the next
before the next spawns.

OZ operates with a defined identity, hard operational boundaries, and an explicit audit
trail. It cannot silently skip a stage, invent data, or deliver a report when a mandatory
data source is missing. If a stage is blocked, it halts the pipeline and alerts founders
via Telegram before anything continues.

See [docs/openclaw_architecture.md](docs/openclaw_architecture.md) for the full agent
definitions, task directives, and orchestration protocol.

---

## The 12 Agents

Each agent has a defined role, mandatory outputs, and human-approval gates.

| # | Agent | Role |
|---|---|---|
| 1 | Territory Intake | Resolves area name to bounding box, LA code, run config, and ranking mode |
| 2 | Discovery | Finds non-domestic building candidates from OS MasterMap; applies domestic exclusion, reverse-geocode, UPRN matching, and EPC refinement |
| 3 | Roof Geometry | Scores physical solar potential: footprint+height model or measured EA LiDAR (pitch, azimuth, shading, usable area) |
| 4 | Energy Economics | Demand, self-consumption, CAPEX, payback period, carbon saving -- with confidence bands |
| 5 | Project Scale | Saturating, penalised opportunity-size score (feasible kWp); penalises over-large or structurally marginal sites |
| 6 | Business Propensity | Sector ESG orientation, energy intensity, Companies House identity and SIC codes |
| 7 | Ownership and Tenancy | Infers owner-occupier vs tenant to assess decision feasibility |
| 8 | Policy Risk | EPC/MEES compliance duty, ESG/SECR reporting, heritage and planning constraints |
| 9 | Market Momentum | Local installer activity and peer PV adoption signal (deliberately low weight -- commercial peer effects are weaker than residential) |
| 10 | Scoring and Ranking | Weighted final score, confidence/completeness/proxy flags, multiple ranking modes |
| 11 | Enrichment | Appends verified address, Google Maps/Street View/OSM links, and Companies House URLs for top leads |
| 12 | Calibration, Report, and QA | Honest calibration vs known PV adoption; executive HTML/PDF report with embedded charts; deterministic PASS/WARN/FAIL QA checks |

Agents run **sequentially** inside a single orchestrated pipeline run. Each stage writes
structured JSON/CSV outputs that the next stage reads. The audit trail, missing-data
register, and per-agent timing are written to `runs/commercial/<run_id>/` so every run
is fully reproducible and explainable.

---

## The Problem This Solves

UK commercial rooftop solar capacity is a fraction of its technical potential. The gap is
not financial -- payback periods of 4 to 8 years are achievable for many buildings -- it
is informational. Installers cannot cost-effectively identify which buildings are worth
approaching, so they default to reactive inbound sales or inefficient cold calling.

This pipeline generates a ranked lead list where each site has been scored across six
dimensions: physical suitability, project economics, organisational propensity, ownership
feasibility, policy risk, and market momentum. An installer receives a shortlist of
buildings where the return on sales effort is highest, not just the biggest roofs.

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Fill in the API keys you have. The pipeline runs in degraded mode without optional keys.
```

The only hard requirement is a local OS MasterMap buildings DuckDB (`OS_MASTERMAP_BUILDINGS_DB`).
Everything else has a labelled fallback. See [docs/commercial_api_and_data_requirements.md](docs/commercial_api_and_data_requirements.md).

### 3. Check readiness

```bash
python3 scripts/check_commercial_readiness.py --area Oxford --live
```

### 4. Run the pipeline

```bash
# Standard run (footprint+height roof model, fast)
python3 run_pipeline.py --mode commercial --area "Oxford" --leads 100

# High-confidence roofs from EA LiDAR (slower, recommended for large buildings)
python3 run_pipeline.py --mode commercial --area "Oxford" --leads 100 --lidar

# Prioritise the biggest viable projects
python3 run_pipeline.py --mode commercial --area "Oxford" --ranking-mode large-project-first

# Only sites >= 100 kWp
python3 run_pipeline.py --mode commercial --area "Oxford" --min-kwp 100

# Fast dry-run (no live APIs)
python3 run_pipeline.py --mode commercial --area "Oxford" --leads 20 --dry-run --limit-candidates 300
```

### 5. Dashboard

```bash
python3 -m commercial.dashboard --port 5055
# Open http://127.0.0.1:5055
```

The dashboard shows the ranked lead table with clickable company and map links, embeds
the full HTML report, and can launch new runs with live per-agent progress streaming.

### 6. Tests

```bash
pytest tests/commercial -q
```

---

## Outputs

**`runs/commercial/<run_id>/`** -- the full audit trail per run:
- `territory_config.json` -- resolved area and run parameters
- `agent_audit_log.json` -- per-agent timing, record counts, warnings
- `missing_data.json` -- register of absent data sources and fallbacks used
- `score_explainability.json` -- per-lead score breakdown across all six dimensions
- `calibration.json` -- correlation vs known PV adoption (reported as inconclusive if no ground truth)
- `qa_report.json` -- deterministic PASS/WARN/FAIL checks
- `all_scored.csv` -- every candidate with all component scores

**`reports/commercial/<run_id>/`** -- installer-ready deliverables:
- `commercial_report_*.html` -- executive-grade report with embedded charts and location links
- `commercial_report_*.pdf` -- LaTeX PDF build (when pdflatex is present)
- `commercial_leads_*.csv` / `.xlsx` -- ranked lead list with address, Companies House URL, Google Maps/Street View/OSM links
- `charts/` -- individual chart images

---

## Configuration

All tunables are documented YAML files in `config/`:

| File | Controls |
|---|---|
| `commercial_scoring.yml` | Agent weights and scoring parameters |
| `commercial_archetypes.yml` | Building category definitions and sector mappings |
| `commercial_tariffs.yml` | Electricity tariff scenarios (central/high/low) |
| `commercial_pv_costs.yml` | CAPEX, degradation, O+M cost assumptions |
| `commercial_data_sources.yml` | Data source registry with fallbacks and confidence weights |

---

## Data Sources

| Source | Purpose | Key required |
|---|---|---|
| OS MasterMap (local DuckDB) | Building footprints and heights | No (file-based) |
| EA LiDAR (open data, OGL) | High-confidence roof geometry | No |
| EPC API (gov.uk) | Non-domestic EPC ratings and MEES status | Yes (free registration) |
| Companies House API | Business identity and SIC codes | Yes (free) |
| PVGIS (JRC) | Solar irradiance and yield modelling | No |
| planning.data.gov.uk | Conservation areas and listed buildings | No |
| OS Data Hub NGD | Footprints via API (fallback if no local DB) | Yes |

---

## Demo Mode

The dashboard includes a `/demo` route for presentations: a guided walkthrough with
animated per-agent progress, an Oxford lead map, and the existing Oxford report. It never
triggers real pipeline execution.

```bash
python3 -m commercial.dashboard --port 5055
# Open http://127.0.0.1:5055/demo   (append ?fast=1 to compress timing)
```

---

## Methodology

See [docs/commercial_solar_methodology.md](docs/commercial_solar_methodology.md) for
the full methodology, literature review, score construction rationale, calibration
approach, and caveats. The key principles:

- Every number is traceable to a deterministic formula or a named public dataset.
- Missing data is registered and reported in the audit trail, never silently imputed.
- Calibration is reported honestly: "inconclusive" when no PV ground truth is available.
- Confidence bands are mandatory on every score -- uniform bands are treated as a QA failure.
- AI is used only for narrative text (report summaries and red-team critique), never for scores.

---

## Next Steps for Commercial Rollout at Scale

The pipeline is production-capable for single-territory runs. The following steps are
required to move to a multi-installer, multi-territory commercial product:

### Infrastructure
- **Multi-tenant run isolation** -- each installer organisation gets its own run namespace,
  data scope, and report store. Currently all runs share a single `runs/` directory.
- **Async job queue** -- replace the synchronous orchestrator with a task queue (Celery or
  equivalent) so multiple territory runs can execute in parallel without blocking the dashboard.
- **Cloud data layer** -- the OS MasterMap DuckDB and LiDAR tiles are currently local files.
  A scalable deployment needs these served from object storage (S3/GCS) with per-territory
  lazy loading.
- **API layer** -- expose `run_pipeline` and report retrieval as authenticated REST endpoints
  so the pipeline can be triggered from installer CRMs or third-party tools.

### Data Coverage
- **National OS MasterMap** -- the current Oxford DuckDB covers one local authority.
  National coverage requires either an OS commercial licence or systematic OS NGD API
  ingestion at the territory level before each run.
- **National LiDAR staging** -- EA LiDAR tiles must be pre-staged per territory. An
  automated tile-fetching agent (scoped to the run bounding box) would replace the current
  manual download step.
- **EPC bulk download** -- the current pipeline queries the EPC API per building. At scale,
  a one-off bulk EPC ingest per territory is faster and more reliable.

### Commercial Hardening
- **Postcode-level lead deduplication** -- across runs and tenants, to avoid the same
  building appearing in reports sold to competing installers in the same area.
- **Audit logging to a database** -- currently written as JSON files per run. A structured
  store (PostgreSQL or DuckDB served centrally) enables cross-run analytics and billing.
- **Formal QA sign-off gate** -- the current `qa.py` PASS/WARN/FAIL is advisory. In
  production, a FAIL should block report delivery until a human reviews and approves.

---

## The Learning Agent: How This Gets Smarter Over Time

The pipeline's scoring weights are currently calibrated against publicly available EPC
and MCS installer data. This gives a reasonable baseline but cannot capture the
ground-truth signal that matters most: **which leads actually converted**.

The roadmap adds a feedback ingestion layer that closes this loop:

### Installer Feedback Collection
After each report delivery, installers are prompted (via the dashboard or a lightweight
web form) to mark outcomes against leads they acted on:
- **Site visited** -- confirms the building was accessible and structurally viable
- **Proposal sent** -- confirms demand signal and owner engagement
- **Installed** -- the strongest positive signal: the lead converted

Negative signals are equally valuable:
- **Unreachable owner** -- degrades the ownership/tenancy score for similar building types
- **Planning refused** -- upgrades the policy risk weighting for the affected heritage/conservation category
- **Roof unsuitable on survey** -- feeds back into the roof confidence model for the relevant `roof_method`

### The Learning Agent
A new Agent 13 -- the **Learning Agent** -- runs after every batch of feedback is
received. It:

1. Joins feedback outcomes to the `score_explainability.json` records for the relevant run
2. Runs a correlation analysis: which score components best predicted conversion, visit, and refusal
3. Proposes weight adjustments to `config/commercial_scoring.yml` with a stated confidence
   level and the number of feedback records supporting each change
4. Writes a methodology note to the run audit trail
5. Flags proposed changes to founders for review before any weight is updated in production

Weight updates are **never applied automatically**. A human reviews and approves every
change before it takes effect. This preserves the deterministic, auditable character of
the pipeline while allowing it to improve from real-world signal.

### What Improves Over Time
- Sector weights (which SIC codes actually convert vs which score well but stall at owner contact)
- Roof confidence thresholds (LiDAR vs footprint model -- do LiDAR leads convert at a higher rate?)
- Minimum kWp thresholds (do sub-50 kWp sites convert at a rate that justifies inclusion?)
- Ownership inference (are the proxy signals for owner-occupier vs tenant actually predictive?)
- Geographic calibration (do payback assumptions need local tariff or DNO grid constraint adjustments?)

Over time, the pipeline shifts from a methodology calibrated on literature and aggregate
data to one calibrated on observed UK commercial solar conversion rates -- making each
successive report more precise and more valuable to the installer receiving it.

---

## Licence

Code: MIT.

Data: OS MasterMap requires an OS licence (Digimap educational or commercial). EPC data
is Crown Copyright under the Open Government Licence. EA LiDAR is Open Government Licence
v3.0. See [docs/commercial_api_and_data_requirements.md](docs/commercial_api_and_data_requirements.md)
for individual source licences.
