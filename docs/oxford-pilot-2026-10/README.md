# Oxford pilot: progress, October 2026

Prepared by Zara Pashov, 9 October 2026.

Since the June hackathon we have replaced the ranking method, rebuilt the Oxford City lead list on open data, and checked the top sites on satellite imagery. Section 4 adds indicative sizing for 20 of the checked sites: system size, generation, savings, payback and CO2.

Nothing in the existing pipeline code has been changed. This folder only adds documents, the checked lead list and the scripts that produced it.

## 1. New ranking method (arena run, 2 October 2026)

We ran an "arena": 16 AI agents each proposed a UK-wide ranking method, attacked each other's proposals over 4 knockout rounds, and a judge scored each match against a written rubric. The brief required every ranking variable to be positively associated with observed UK rooftop solar adoption, the method to work for any UK local authority, and every data source to be usable in a paid product.

**What won (agent a006): a two-variable points scorecard.**

- **Roof footprint area** of each non-domestic building.
- **Site gap**: the edge-to-edge distance to the nearest other building of 200 m² or more. Stand-alone buildings adopt more often than units that share walls.
- The two are combined with a Weight-of-Evidence points scorecard, the method lenders use for credit scoring. Each band of each variable earns points according to how often buildings in that band adopted solar, and a site's score is the sum.
- **Data:** OpenStreetMap buildings (ODbL) and ONS boundaries (OGL). No OS MasterMap, so no educational-licence data.

**Evidence from the agent's pilot.** It covered 216,826 non-domestic buildings in 122 local authorities across all four nations, with 5,235 solar adopters observed in OpenStreetMap.

| Test (areas held out of fitting) | New scorecard | Hackathon method, rebuilt |
|---|---|---|
| AUC (0.5 = coin toss) | 0.693 | 0.644 |
| Top-50 hit rate per local authority (base rate 2.41%) | 7.49% | 4.49% |

In the blind final check, the winner scored 77.0 and the hackathon method 18.5.

**Limitations (stated by the method itself):**

- On large projects (150 kW or more, Renewable Energy Planning Database), its top-50 lists do worse than ranking by roof area alone.
- It fails two of its own release gates, so it is a **pilot release (v0.3)**, not production.
- Up to 59% of "new" OpenStreetMap adopters may be older arrays mapped late.
- The analysis was run by an AI agent. We have spot-checked it (section 2) but not yet reproduced it in full.

The full methodology document (about 14,000 words) is kept outside this repo and is available on request.

## 2. Oxford City lead list (7 October 2026)

Built from the arena's scored table for Oxford City (local authority E07000178). Scripts are in `scripts/`.

1. **1,790** non-domestic buildings scored with the new scorecard.
2. **95** removed because OpenStreetMap shows solar panels on them: 92 found by the arena, plus 3 found by a fresh recheck of the OpenStreetMap extract.
3. Buildings grouped into **1,008 sites**. A school, hospital, college or single-company site counts as one lead. Business parks and industrial estates stay as separate units, because each unit is a different occupier.
4. **11** sites removed because they already have their own rooftop solar record in the Renewable Energy Planning Database (REPD). Matching uses the organisation name, within 400 m.
5. Two lists: **Top 50** by score, and **Large roofs** (largest building 2,375 m² or more, about 150 kWp) ranked by roof area alone, as the method recommends for large projects.

**Checks:**

- Roof area and site gap were recomputed from the raw outlines for 10 buildings. All reproduce: area within a few m², gap exactly.
- **An encouraging sign, not proof:** before removal, the 11 sites with their own REPD record ranked 2, 14, 17, 32, 37, 61, 67, 78, 212, 295 and 408 out of 1,008, so 8 of the 11 were in the top 80.
- **Not done:** the official heritage check (the planning data service is blocked on our network, and OpenStreetMap heritage tags are sparse), and the Feed-in Tariff check for panels that exist but are not mapped (method step 7b).

## 3. Satellite check (Zara, 7 October 2026)

Zara checked 66 sites on satellite imagery: the Top 50 plus the 16 large roofs not already in it.

| Finding | Sites |
|---|---|
| Panels visible that OpenStreetMap had missed (about 1 in 11) | 6: Orion Academy, Pegasus Primary, The Swan School, Waitrose, St Ebbe's Primary, Science Area |
| Construction sites | 3 |
| Data error: a terrace of houses mapped as one large building | 1 |
| City-centre blocks too fragmented to treat as one site | 2 |
| Kept as leads | 56 |
| Chosen for sizing | 20 |

**Lessons for the pipeline:**

- A visual check for existing panels is needed before any list goes out (method step 7c).
- Large untagged buildings outside commercial zones need a rule against terraces.
- Identifying the occupier is still manual.

## 4. Indicative sizing for 20 sites (9–10 October 2026)

`oxford-solar-sizing-2026-10-09.xlsx` estimates system size, year-1 generation, annual saving, simple payback and CO2 avoided for the 20 sites chosen after the satellite check. Its **Method** sheet explains every step, and its **Inputs** sheet gives every number with its source and an exact quote. Every result is a live formula, so changing an input updates the whole workbook. All results were recomputed independently in Python and match.

**Method in brief**

- System size (kWp) = roof area × share of roof covered by panels × 230 W/m². The 230 W/m² is the median of three current module datasheets.
- Generation = size × 922.3 kWh/kWp/yr, from PVGIS for Oxford (10° tilt, facing south, 14% system losses).
- Saving = electricity used on site × 24.51 p/kWh (DESNZ all-business average, Q2 2026, including the Climate Change Levy) + electricity exported × the export price.
- Payback = capital cost ÷ (saving − operation and maintenance cost).
- CO2 = generation × 0.13096 kg CO2e/kWh (DESNZ 2026 grid-average conversion factor).
- Roof area is the main building only. Systems are sized to what the roof can hold, and anything over 1 MWp is flagged.

**Scenarios** (every input set to the same side)

| Input | Low | Central | High | Source |
|---|---|---|---|---|
| Share of roof covered by panels | 29.4% | 40% | 46.2% | Bódis et al. 2019; Kakoulaki et al. 2026; Gagnon et al. (NREL) 2016 |
| Share of solar used on site | 40% | 60% | 80% | Our assumption: no published source by sector |
| Export price (p/kWh) | 3.0 | 4.1 | 4.1 | Ofgem Smart Export Guarantee report, Year 5 |
| Capital cost (£/kWp) | 1,100 | 900 | 700 | Joju Solar published range (trade source) |
| O&M (£/kW/yr, 2014 prices) | 11.4 | 9.5 | 7.6 | DECC 2015 Small-Scale Generation Costs Update |

**Results: totals for the 19 sites that could be sized**

| | Low | Central | High |
|---|---|---|---|
| System size | 6.3 MWp | 8.6 MWp | 9.9 MWp |
| Generation | 5.8 GWh/yr | 7.9 GWh/yr | 9.2 GWh/yr |
| Net saving | £0.60m/yr | £1.21m/yr | £1.80m/yr |
| Simple payback | 11.5 years | 6.4 years | 3.9 years |
| CO2e avoided | 764 t/yr | 1,039 t/yr | 1,200 t/yr |

**Caveats**

- These are indicative desktop estimates for year 1, not quotes. There has been no site visit, structural check or grid check.
- Payback is the same at every site within a scenario, because roof area is the only input that differs between sites. Site-specific payback needs site data, above all each site's daytime demand.
- The share of solar used on site is an assumption and is the biggest driver of savings.
- Unipart Logistics Cowley (80,278 m²) is not sized: its roofs are angled and the roof planes have not been measured.
- The PVGIS yield was retrieved during the arena run and still has to be re-confirmed in the PVGIS web tool (Method sheet, section 7).

## 5. Next steps

1. Confirm the PVGIS yield used in the sizing workbook (Method sheet, section 7).
2. Get installer feedback on the list and the indicative figures.
3. Reproduce the arena's pilot analysis ourselves.
4. Add the heritage check and the Feed-in Tariff check for unmapped panels.
5. Update the main README so it matches what the code does (no LLM agents, Telegram gates or learning agent exist in the code yet).

## Files in this folder

| File | What it is |
|---|---|
| `oxford-leads-checked-2026-10-07.xlsx` | The lead list with Zara's satellite check: Top 50, Large roofs, reserves, sites already in the solar pipeline, buildings excluded for mapped panels |
| `oxford-solar-sizing-2026-10-09.xlsx` | Indicative sizing, generation, savings, payback and CO2 for 20 sites: Method, Inputs, Results, Calculations |
| `scripts/` | The Python scripts that built the list; see `scripts/README.md` |
| `DATA-LICENCE.md` | Licence and attribution for the data in this folder |
