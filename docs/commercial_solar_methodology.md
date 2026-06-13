# Commercial Solar Lead Prioritisation — Methodology & Literature Review

**Product framing.** This system produces a **commercial solar lead priority** for
non-domestic buildings (warehouses, offices, retail, industrial, schools, leisure,
public and mixed-use). It is **lead prioritisation, not a prediction of guaranteed
adoption**. Every lead requires a site survey, bill validation, structural survey
and planning/grid confirmation before commitment. Numbers are **indicative**.

All scores are produced by **deterministic Python** (`commercial/`). AI is used only
for narrative summarisation and red-team critique — never as the source of a number.

---

## 1. Literature review

### A. Behavioural & organisational adoption
- **DESNZ/BEIS evidence on rooftop solar & SME energy behaviour** — non-domestic
  decisions are dominated by **payback expectations, capital constraints, management
  attention and split incentives** (landlord/tenant). Energy-cost *salience* (high
  bills) is a primary driver. → motivates the **Economics** and **Adoption Propensity**
  sub-scores and the **Decision Feasibility** sub-score.
- **Diffusion of Innovations (Rogers)** and **peer-effect** literature — visible
  neighbouring adoption raises uptake. For *commercial* PV we deliberately keep this a
  **small** signal (commercial peer effects are weaker and less evidenced than the
  residential "solar contagion" effect) → **Market Momentum** weight 0.05.
- **Theory of Planned Behaviour (Ajzen)** — perceived benefit + perceived behavioural
  control. Adapted *cautiously* from residential evidence (strength C) into the
  propensity score via sector ESG orientation and energy-cost salience.
- **Organisational adoption** — environmental orientation, ESG/SECR reporting duty,
  multi-site/portfolio operators, trust in installers, decision complexity. Mapped to
  sector archetypes (`config/commercial_archetypes.yml`).

### B. Commercial & industrial rooftop PV
- **UK Warehousing Association (UKWA) rooftop solar guidance** and commercial-rooftop
  investment cases — large shed roofs offer **strong self-consumption economics**,
  **economies of scale** (lower £/kWp at size), and **PPA/landlord-export** options.
- Commercial economics hinge on **self-consumption** (displacing import at ~18–34 p/kWh)
  far more than export (~4–10 p/kWh). → economics models self-consumption explicitly,
  capped by modelled demand.
- **Why larger viable projects can be more attractive**: larger installable capacity,
  lower soft cost per kWp, larger absolute savings, stronger installer revenue, better
  PPA/export proposition, strategic value for multi-site operators. → motivates a
  **dedicated Project-Scale sub-score** — but only for *viable, usable, confident* scale.

### C. Technical PV potential
- **LiDAR + building-footprint rooftop PV assessment** (EU JRC and building-level
  rooftop-PV-potential research) — roof-plane extraction (pitch, azimuth, shading,
  usable area) from a Digital Surface Model is the standard high-confidence method.
  Implemented in `commercial/lidar_roof.py` (EA National LiDAR DSM, 1 m) with a
  footprint+height fallback when LiDAR is off/absent.
- **PVGIS (JRC)** irradiance/specific-yield modelling → annual generation.
- **Existing-PV detection & calibration** — best evidence is observed installations
  (MCS/FiT/SEG or imagery). We expose a calibration hook; absent ground truth, we report
  calibration **inconclusive** rather than fabricate it.

### D. Policy & regulatory context
- **Non-domestic EPC + MEES** — minimum energy efficiency standards make poor-rated
  commercial stock a retrofit target (solar often part of a *bundle*, not a sole fix).
- **Display Energy Certificates (DEC)** for public buildings; **public-sector net-zero
  estate duty** raises propensity for schools/universities/civic buildings.
- **Planning constraints** — conservation areas & listed buildings (Oxford is heavily
  constrained) materially affect feasibility → heritage penalty + caveat.

### Evidence strengths
`A` = direct commercial/SME empirical · `B` = strong technical/economic ·
`C` = residential evidence plausibly transferable · `D` = weak proxy / exploratory.
**D-strength variables are never allowed to dominate** (Market Momentum capped at 0.05).

---

## 2. Evidence table

| Variable | Mechanism | Expected sign | Evidence | Data source | Implementation field | Caveats |
|---|---|---|---|---|---|---|
| Usable roof area | More installable capacity | + | B | OS MasterMap + LiDAR | `usable_roof_area_m2` | Only if not shaded/constrained |
| Roof pitch/azimuth | Yield & layout | +/− | B | EA LiDAR (else assumed) | `roof_pitch_deg`, aspect | Modelled if LiDAR off |
| Shading | Lower yield | − | B | EA LiDAR | `shading_factor` | Not measured without LiDAR |
| Irradiance | Generation | + | B | PVGIS | `specific_yield_kwh_per_kwp` | City-uniform |
| Feasible kWp | Project size/economies of scale | + | A/B | derived | `feasible_kwp` | Saturating, penalised if low-confidence |
| Electricity price/bill | Savings salience | + | A | DESNZ QEP / tariffs.yml | `import_price_gbp_kwh` | Scenario-based |
| Self-consumption | Avoided import value | + | A/B | archetypes.yml | `self_consumption_ratio` | Capped by modelled demand |
| Payback | Investment attractiveness | − | A | derived | `payback_central_yr` | Indicative, not a quote |
| Sector energy intensity | Cost salience | + | B | archetypes.yml | `energy_intensity_class` | Sector proxy |
| Sector ESG/SECR pressure | Reputational/regulatory driver | + | C | archetypes.yml | `esg_pressure_class` | Transferable, not firm-specific |
| Company status/SIC | Firm viability/sector | + | A (where matched) | Companies House | `company_status`,`sic_code` | Only if occupier resolved |
| Owner-occupier vs tenant | Decision feasibility / split incentive | + | A/C | type+size (VOA absent) | `tenure_inference` | Inferred, low confidence |
| EPC band / MEES | Compliance pressure | + | B | non-dom EPC | `epc_band`,`mees_note` | Unknown if EPC absent |
| Conservation/listed | Planning risk | − | B | planning.data / heritage | `planning_risk` | Approximate proximity |
| Local installer density | Market readiness | + (weak) | D | MCS register | `local_installer_count` | Installers, not installs |
| Peer commercial PV | Diffusion nudge | + | C/D | ground truth (if supplied) | momentum | Often unavailable |

---

## 3. Scoring model

Six+1 transparent sub-scores (each 0–10, separately visible in outputs):

| Sub-score | Default weight | Asks |
|---|--:|---|
| Technical roof suitability | 0.25 | Is the roof *suitable* (orientation/pitch/usable adequacy/shading/irradiance)? |
| Energy economics | 0.25 | Is it *financially attractive* (demand, self-consumption, CAPEX, payback)? |
| **Project scale / usable roof area** | **0.15** | Is the opportunity *big enough to prioritise commercially*? |
| Business adoption propensity | 0.15 | Does the occupier/sector *care* (ESG + energy-cost salience)? |
| Ownership / decision feasibility | 0.08 | Is a sale *actionable* (owner-occupier vs split incentive)? |
| Policy / compliance / ESG | 0.07 | Does regulation/reputation *push* (EPC/MEES, public-sector duty)? |
| Local market momentum | 0.05 | Local proof (installers / peer PV)? *(small by design)* |

**Final score (out of 10):**
```
final_10 = 0.25·technical + 0.25·economics + 0.15·project_scale
         + 0.15·adoption_propensity + 0.08·decision_feasibility
         + 0.07·policy_compliance + 0.05·market_momentum
         × heritage_penalty (0.8 if high planning risk, ~0.9 if moderate)
```
Weights live in `config/commercial_scoring.yml` and are echoed into every run's
`score_explainability.json`. Ranking modes re-weight and re-normalise:
- **balanced** — base weights.
- **large-project-first** — ×2.2 project_scale, ×1.1 economics.
- **fastest-payback** — ×2.0 economics, ×1.2 technical, ordered by payback.
- **highest-confidence** — base weights, ordered by `confidence_score`.

### Avoiding double-counting roof area
The **technical** usable-area component **saturates at ~150 m²** (a binary-ish
"is there enough usable roof?"), while **project-scale saturates near ~800 kWp**
(a "how big is the viable opportunity?"). A small excellent roof and a huge excellent
roof therefore score similarly on *technical* but differ on *project-scale* — by design.

### Project-scale variable (the dedicated agent)
`project_scale_score = 10 · ln(1 + kWp/30) / ln(1 + 800/30)`, capped at 10, then
multiplied by penalties for: low usable-share (<0.35), low data confidence,
heavy shading, existing-PV saturation, heritage. A **saturating (log)** curve stops
mega-roofs automatically dominating; penalties stop **large-but-unviable** roofs from
ranking highly. Micro sites (<30 kWp) with strong economics keep a mid score so they
are not unfairly buried. Optional `--min-kwp` / `--min-usable-roof-area` filters apply
only when the user requests them.

### Confidence, completeness, proxy quality
- `confidence_score` — weighted blend of classification/category/roof/economics/scale/
  policy confidences.
- `data_completeness_score` — weighted presence of geometry/height/postcode/UPRN/
  non-dom EPC/Companies House/planning/PVGIS for that lead.
- `proxy_quality_score` — evidence-strength-weighted (A=1…D=0.3) by the effective weights.

---

## 4. Material deviations from the original specification (justified)

1. **Stage order: Economics runs *before* Project-Scale.** The scale agent must be able
   to penalise "large but poor economics", which requires payback first. (Spec listed
   scale before economics.)
2. **No raw non-domestic-EPC-driven discovery.** The legacy EPC host retired 30 May 2026
   and the new host's endpoint/auth for the supplied token did not return rows; discovery
   therefore uses **OS MasterMap footprint+height geometry + domestic-EPC exclusion** as
   the primary commercial classifier, *refined* by non-dom EPC/VOA/SIC when available.
   This is recorded honestly (category_source, category_confidence) and in the missing-data register.
3. **LiDAR is opt-in (`--lidar`), applied to large buildings.** EA LiDAR is present and
   gives HIGH-confidence roofs, but is heavier; default runs use the documented
   footprint+height model (MEDIUM confidence) and label it as such.
4. **Calibration is honest-by-default.** With no commercial-PV ground truth, calibration
   returns `INCONCLUSIVE_NO_GROUNDTRUTH` + a QA warning rather than a fabricated correlation.

---

## 5. What the system explicitly does **not** claim
- It does **not** predict that any named business will buy.
- It does **not** assert planning or grid approval.
- It does **not** present geometry-inferred categories or inferred tenure as confirmed.
- It does **not** fabricate or force a positive calibration correlation.
