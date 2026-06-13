#!/usr/bin/env python3
"""
Commercial pipeline readiness checker.

Prints OK / WARNING / MISSING for every declared data source and env key (key
PRESENCE only — never the value), with row counts / file existence and a verdict
on whether Oxford can run now and whether project-scale scoring can run now.

  python3 scripts/check_commercial_readiness.py [--area Oxford]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from commercial import settings, connectors  # noqa: E402

GREEN, YELLOW, RED, RESET = "\033[92m", "\033[93m", "\033[91m", "\033[0m"


def _tag(state: str) -> str:
    return {"OK": f"{GREEN}OK     {RESET}", "WARNING": f"{YELLOW}WARNING{RESET}",
            "MISSING": f"{RED}MISSING{RESET}"}.get(state, state)


def _rowcount_duckdb(path: Path, table: str) -> int | None:
    try:
        import duckdb
        c = duckdb.connect(str(path), read_only=True)
        n = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        c.close()
        return n
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="Oxford")
    ap.add_argument("--live", action="store_true", help="probe live APIs (EPC/PVGIS/Companies House)")
    args = ap.parse_args()

    srcs = settings.data_sources()["sources"]
    print(f"\n=== Commercial pipeline readiness — area: {args.area} ===\n")
    states = {}

    # ── Local files / DBs ────────────────────────────────────────────────────
    checks = []

    # OS MasterMap buildings (REQUIRED)
    db = settings.source_path("os_mastermap_buildings")
    if db and db.exists():
        n = _rowcount_duckdb(db, "buildings")
        checks.append(("OK", "OS MasterMap buildings", f"{db.name}: {n:,} buildings" if n else db.name))
    else:
        checks.append(("MISSING", "OS MasterMap buildings", "REQUIRED — set OS_MASTERMAP_BUILDINGS_DB"))

    # ONSPD (REQUIRED)
    onspd = settings.source_path("onspd")
    checks.append(("OK" if onspd and onspd.exists() else "MISSING", "ONSPD postcode directory",
                   str(onspd.name) if onspd and onspd.exists() else "REQUIRED for postcode/LA resolution"))

    # UPRN
    uprn = settings.source_path("os_open_uprn")
    checks.append(("OK" if uprn and uprn.exists() else "WARNING", "OS Open UPRN",
                   uprn.name if uprn and uprn.exists() else "optional — UPRN identity degraded"))

    # Heritage / planning
    her = settings.source_path("planning_constraints")
    checks.append(("OK" if her and her.exists() else "WARNING", "Planning/heritage constraints",
                   her.name if her and her.exists() else "optional — planning risk = UNKNOWN"))

    # MCS
    mcs = settings.DB_DIR / "mcs.duckdb"
    n = _rowcount_duckdb(mcs, "mcs_installers") if mcs.exists() else None
    checks.append(("OK" if n else "WARNING", "MCS installer register",
                   f"{n} installers" if n else "optional — market momentum reduced"))

    # Ground truth PV (calibration)
    gt = settings.source_path("commercial_pv_ground_truth")
    checks.append(("WARNING" if not (gt and gt.exists()) else "OK", "Commercial PV ground truth",
                   "present" if gt and gt.exists() else
                   "absent — calibration will be INCONCLUSIVE_NO_GROUNDTRUTH (honest)"))

    # ── Env-keyed APIs (presence only) ───────────────────────────────────────
    for key, label, req in [("EPC_BEARER_TOKEN", "Non-domestic EPC API", False),
                            ("COMPANIES_HOUSE_API_KEY", "Companies House API", False),
                            ("PVGIS_API", "PVGIS irradiance", False)]:
        present = settings.env_present(key)
        checks.append(("OK" if present else "WARNING", label,
                       "env key present" if present else f"{key} not set — degraded mode"))

    # ── EA LiDAR ─────────────────────────────────────────────────────────────
    lidar = settings.ROOT / "data" / "lidar" / "dsm"
    n_tiles = len(list(lidar.glob("*.tif"))) if lidar.exists() else 0
    checks.append(("OK" if n_tiles else "WARNING", "EA LiDAR DSM/DTM",
                   f"{n_tiles} DSM tiles — enables HIGH-confidence roof (use --lidar)" if n_tiles
                   else "absent — roof modelled from footprint+height (MEDIUM confidence)"))

    # ── VOA non-domestic ─────────────────────────────────────────────────────
    voa = settings.source_path("voa_nondomestic_rates")
    checks.append(("WARNING", "VOA non-domestic rating list",
                   "absent (local voa.duckdb is council-tax/domestic only) — tenure inferred"
                   if not (voa and voa.exists()) else "present"))

    for state, name, detail in checks:
        states.setdefault(state, 0)
        states[state] += 1
        print(f"  [{_tag(state)}] {name:<32} {detail}")

    # ── Live probes (optional) ───────────────────────────────────────────────
    if args.live:
        print("\n  Live API probes:")
        y, st = connectors.pvgis_specific_yield(51.75, -1.26)
        print(f"  [{_tag('OK' if st=='ok' else 'WARNING')}] PVGIS live           "
              f"{'yield '+str(y)+' kWh/kWp' if y else st}")
        ch, st = connectors.companies_house_search("Oxford City Council", items=1)
        print(f"  [{_tag('OK' if ch else 'WARNING')}] Companies House live  {'ok' if ch else st}")
        rows, st = connectors.epc_nondomestic(settings.TERRITORIES['oxford']['lad_code'], max_rows=5)
        print(f"  [{_tag('OK' if rows else 'WARNING')}] EPC non-domestic live {len(rows)} rows ({st})")

    # ── Verdicts ─────────────────────────────────────────────────────────────
    can_run = (db and db.exists()) and (onspd and onspd.exists())
    can_scale = can_run  # project-scale needs footprint area (MasterMap) — same requirement
    print("\n  ─ Verdict ─")
    print(f"  Oxford can run now:            {GREEN+'YES'+RESET if can_run else RED+'NO'+RESET}")
    print(f"  Project-scale scoring can run: {GREEN+'YES'+RESET if can_scale else RED+'NO'+RESET}")
    if not can_run:
        print(f"  {RED}Next action:{RESET} provide OS MasterMap buildings DB + ONSPD slice (see "
              f"docs/commercial_api_and_data_requirements.md).")
    else:
        print("  Calibration: requires data/commercial/ground_truth_pv.csv (else honest INCONCLUSIVE).")
        print("  Run: python3 run_pipeline.py --mode commercial --area \"Oxford\" --leads 100")
    print()
    return 0 if can_run else 1


if __name__ == "__main__":
    sys.exit(main())
