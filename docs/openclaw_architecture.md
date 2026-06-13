# OpenClaw Agentic Architecture

This document describes the OpenClaw agentic framework that powers the commercial solar
lead pipeline and defines the identity, operating principles, and orchestration protocol
of OZ, the central AI orchestrator.

---

## What is OpenClaw?

OpenClaw is an agentic AI orchestration framework. Rather than a single monolithic AI
producing outputs, OpenClaw decomposes a complex workflow into discrete specialised agents,
each with a defined task directive, mandatory outputs, and human-approval gates. Agents
run sequentially under the coordination of a central orchestrator (OZ) and cannot proceed
until the previous agent signals completion.

The framework is designed around three principles:

- **No silent failure.** If a stage is blocked -- missing data, failed API, bad output --
  the pipeline halts and founders are alerted before anything continues.
- **No invented data.** A pipeline stage that cannot produce a commercially defensible
  result must block, not fall back to a placeholder. A report with a uniform placeholder
  roof score is worse than no report.
- **Full audit trail.** Every agent writes structured outputs. Every run produces an
  `agent_audit_log.json`, a `missing_data.json`, and a `score_explainability.json`
  so the source of every number in the final report is traceable.

---

## OZ: The Central Orchestrator

OZ is a senior solar-intelligence AI with a defined identity and hard operational
boundaries. Its core character:

- **Call it like you see it.** If a territory has poor data coverage, too few credible
  leads, or a scoring component with zero variance, OZ says so directly. Honest feedback
  beats comfortable silence.
- **Lead with the answer.** OZ is not a chatbot. It produces ranked lead reports,
  methodology notes, and pipeline status updates. Filler is cut.
- **Methodology integrity is commercial integrity.** This product sells intelligence. A
  lead list produced without real roof data is a demographic filter, not a solar lead list.
- **Earn trust through competence.** Property data and installer pricing stay private.
  Reports are written to disk only -- no external delivery without explicit founder approval.
- **Reuse before recompute.** Shared regional property features are computed once per
  territory and reused across ranking overlays and installer-specific views.

OZ specialises in: solar economics, geospatial analysis, LiDAR-derived roof suitability,
PVGIS yield modelling, socioeconomic assessment, conservation area and planning constraint
analysis, regional lead scoring, and data governance.

---

## Orchestration Protocol

When a territory run is triggered, OZ:

1. Spawns exactly one sub-agent per pipeline stage, sequentially.
2. Does not spawn the next agent until the previous one returns a structured completion
   message (`COMPLETE | agent-XX-slug | {run_id} | ...`).
3. If any agent returns `BLOCKED` or an error, OZ halts the pipeline, sends a Telegram
   alert to founders with the agent label and the exact reason, and does not spawn the
   next stage.
4. Passes each agent its Task Directive with the resolved `{run_id}` and `{territory}`.
5. Writes the outcome of every stage to the run audit log.

Human-approval gates exist at: installer assumption sign-off (Agent 1), data lineage
review (Agent 2), low-confidence join exclusion (Agent 3), and any proposed methodology
weight update from the Learning Agent (Agent 13).

---

## The 12 Agents: Commercial Pipeline

### Agent 1 -- Territory Intake
Resolves the area name (or postcode) to a machine-readable run configuration: bounding
box, local authority code, lead count target, ranking mode, minimum kWp threshold, and
data source availability summary.

**Outputs:** `territory_config.json`
**Gate:** Human approval of any non-default ranking assumptions.

---

### Agent 2 -- Discovery
Queries the OS MasterMap buildings DuckDB for all non-domestic building candidates within
the bounding box. Applies domestic exclusion (by descriptive group and EPC tenure),
reverse-geocodes building centroids to postcodes, matches UPRNs, and refines with
non-domestic EPC data where available.

**Outputs:** Candidate building table with footprint area, height, postcode, UPRN, EPC
rating, and category classification.
**Gate:** Blocks if OS MasterMap DB is absent and OS NGD API key is not configured.

---

### Agent 3 -- Roof Geometry
Scores physical solar potential for each candidate. Two modes:

- **Footprint+height model** (default, fast): infers usable roof area from footprint
  geometry and relative height; assumes large commercial buildings have predominantly
  flat or low-pitch roofs. Labelled `roof_method=footprint_height`, MEDIUM confidence.
- **EA LiDAR** (`--lidar` flag): extracts measured roof planes (pitch, azimuth, ridge
  geometry, shading factors) from Environment Agency 1m DSM/DTM tiles. Labelled
  `roof_method=lidar`, HIGH confidence.

**Outputs:** Per-building `usable_roof_area_m2`, `shading_factor`, `roof_confidence_score`,
`feasible_kwp` (pre-economics), `roof_method`.

---

### Agent 4 -- Energy Economics
For each candidate, models:
- Annual electricity demand (from sector archetype and floor-area proxy)
- Self-consumption fraction (demand profile vs generation profile)
- CAPEX (from `commercial_pv_costs.yml`, size-banded)
- Simple payback period under central/high/low tariff scenarios
- Carbon saving (tCO2e/yr)

**Outputs:** `payback_central_yr`, `self_consumption_fraction`, `capex_gbp`,
`carbon_saving_tco2e`, `economics_score`.

---

### Agent 5 -- Project Scale
Converts the raw `feasible_kwp` estimate into a penalised opportunity-size score using
a saturating function. Penalises sites where usable roof share is very low (roof
structurally constrained), where data confidence is low, or where payback exceeds the
sector-specific threshold. Produces the `project_scale_score`.

**Outputs:** `project_scale_score`, `feasible_kwp` (final), `size_band`.

---

### Agent 6 -- Business Propensity
Scores the likelihood that the building's occupier or owner is receptive to a solar
conversation, based on:
- SIC sector ESG orientation (from `commercial_archetypes.yml`)
- Energy intensity of the sector
- Company size and type (Companies House, where available)
- SECR/ESG reporting duty (large companies are legally required to report Scope 2 emissions)

**Outputs:** `business_propensity_score`, `sector_esg_tier`, `company_name`,
`company_number`, `companies_house_url`.

---

### Agent 7 -- Ownership and Decision Feasibility
Infers whether the building is likely owner-occupied or tenanted, using sector archetype
priors, Companies House registered office co-location, EPC tenure field, and building
category. Owner-occupiers have shorter decision chains; tenants require landlord engagement.

**Outputs:** `ownership_inference` (owner/tenant/unknown), `decision_feasibility_score`.

---

### Agent 8 -- Policy Risk
Assesses regulatory and planning risk:
- EPC/MEES duty (non-domestic buildings below EPC E face enforcement after 2023; solar
  can be a compliance pathway, which is a positive signal)
- Heritage and conservation area constraints (from planning.data.gov.uk)
- Listed building status (strong negative modifier for planning consent probability)

**Outputs:** `policy_risk_score`, `epc_rating`, `conservation_area_flag`,
`listed_building_flag`.

---

### Agent 9 -- Market Momentum
A deliberately low-weight signal (weight 0.05 in default scoring). Scores local market
activity:
- Density of MCS-registered installers in the postcode area
- Peer PV adoption rate among similar building categories in the LA

Commercial peer effects are weaker and less evidenced than the residential "solar
contagion" effect, so this agent's weight is kept small and is the first candidate for
reduction if calibration shows it adds noise.

**Outputs:** `momentum_score`, `local_installer_count`, `peer_adoption_rate`.

---

### Agent 10 -- Scoring and Ranking
Assembles the weighted final score from all sub-scores. Applies the selected ranking
mode (`score`, `large-project-first`, or `fastest-payback`). Assigns a data completeness
score and a confidence band per lead. Produces the final ranked lead list.

**Outputs:** `final_score`, `confidence_band`, `completeness_score`, `rank`,
`score_explainability.json` (per-lead component breakdown).
**Gate:** Uniform confidence bands are a QA failure -- triggers a warning.

---

### Agent 11 -- Enrichment
For the top-ranked leads, appends:
- A verified address from Companies House registered-office records (labelled with
  `address_source` and a caveat that registered office is not proof of occupancy)
- Google Maps pin, Street View, and OpenStreetMap links from the building centroid
- Companies House profile URL for each company registered at the postcode

**Outputs:** `address`, `address_source`, `address_caveat`, `maps_url`, `streetview_url`,
`osm_url`, `primary_company_name`, `primary_company_url`.

---

### Agent 12 -- Calibration, Report, and QA
Three functions in one final stage:

**Calibration:** Correlates the pipeline's final scores against any known PV adoption
ground truth available for the territory (MCS installs, EPC confirmed PV). Reports
Spearman correlation and confidence interval. Reports "inconclusive" when no ground truth
is available -- never fabricates a validation metric.

**Report:** Generates the executive HTML report (embedded charts, per-lead company and
map links, score breakdowns) and the LaTeX PDF where `pdflatex` is present.

**QA:** Runs deterministic blocking checks -- score variance, coverage thresholds,
mandatory field completeness, outlier detection -- and produces a PASS/WARN/FAIL report.
A FAIL blocks report delivery until a human reviews and approves.

**Outputs:** `calibration.json`, `qa_report.json`, `commercial_report_*.html`,
`commercial_report_*.pdf`, `commercial_leads_*.csv`, `commercial_leads_*.xlsx`.

---

## Agent 13 -- The Learning Agent (Roadmap)

The Learning Agent is the next planned addition to the pipeline. It runs after installer
feedback is collected and proposes evidence-based weight adjustments to the scoring
configuration.

See the "Learning Agent" section of the main README for the full specification.

---

## Operational Boundaries

OZ operates within hard constraints that cannot be overridden by a pipeline run:

- Property data and installer pricing are private. No external delivery without founder approval.
- No outbound communication of any kind from within a pipeline run.
- No individual-level political profiling. Socioeconomic signals are area-level aggregates only.
- No commercially indefensible fallback. If a mandatory data source is absent, the pipeline
  blocks and alerts founders with exact recovery steps.
- API keys and secrets are never written to any workspace file or included in any output.
- Every agent stage is logged. A heartbeat completing does not mean a task completed.

---

## Telegram Integration

OZ sends pipeline status updates to founders via Telegram using OpenClaw's native channel
routing. Notifications are sent on: pipeline start, each agent completion (with record
count and timing), any BLOCKED status, and final report delivery confirmation.

This is configured via `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`. It is
optional -- the pipeline runs without it, but founders lose real-time visibility into
long runs.
