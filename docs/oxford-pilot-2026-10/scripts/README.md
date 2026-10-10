# Scripts that built the Oxford City lead list

Run in this order. Each script is short and commented.

| Step | Script | Reads | Writes |
|---|---|---|---|
| 1 | `step1_candidates.py` | the arena's scored table (`a006r4/scored_gap.parquet`) and building file (`a006/b_oxfordshire.csv`) | `candidates.csv`: Oxford City buildings, sorted by points, then area, then OSM id |
| 2 | `step2_osm_names.py` | the OpenStreetMap extract (`oxfordshire.osm.pbf`) and `candidates.csv` | `osm_names.csv`: names, addresses, containing site, fresh solar recheck |
| 3 | `step3_flags.py` | the OSM extract, `candidates.csv` and the Renewable Energy Planning Database (`repd.csv`) | `flags.csv`: heritage tags, REPD rooftop records within 400 m |
| 3b | `step3b_merge.py` | the three files above | `merged.csv` |
| 4 | `step4_sites.py` | `merged.csv` | `sites.csv` (one row per site) and `buildings_final.csv` |
| 5 | `step5_workbook.py` | `sites.csv` and `buildings_final.csv` | the lead-check workbook |

Example:

```bash
python3 step1_candidates.py <arena scratch folder> candidates.csv
python3 step2_osm_names.py <path>/oxfordshire.osm.pbf candidates.csv osm_names.csv
python3 step3_flags.py <path>/oxfordshire.osm.pbf candidates.csv <path>/repd.csv flags.csv
python3 step3b_merge.py
python3 step4_sites.py
python3 step5_workbook.py oxford-leads-check.xlsx
```

**Packages:** pandas, pyarrow, osmium (pyosmium), shapely, pyproj, openpyxl.

**Input data is not included in this repo.** The arena run's scored table, the OpenStreetMap extract (downloaded 2 October 2026) and the REPD file are too large to commit, and they are kept with the arena run.
