#!/usr/bin/env python3
"""
OZ Solar pipeline entry point — supports both pipeline modes.

  python3 run_pipeline.py --mode commercial --area "Oxford" --leads 100 --scenario central
  python3 run_pipeline.py --mode commercial --area "Oxford" --ranking-mode large-project-first
  python3 run_pipeline.py --mode commercial --area "Oxford" --min-kwp 100
  python3 run_pipeline.py --mode commercial --area "Oxford" --leads 20 --dry-run
  python3 run_pipeline.py --mode domestic    # delegates to the preserved legacy pipeline

The original DOMESTIC pipeline is preserved unchanged under
data/pipeline/_archive/run_pipeline.py and is invoked for --mode domestic.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _commercial(args) -> int:
    from commercial.pipeline import run_commercial
    cats = [c.strip() for c in args.categories.split(",") if c.strip()] if args.categories else None
    result = run_commercial(
        args.area,
        categories=cats,
        leads=args.leads,
        scenario=args.scenario,
        ranking_mode=args.ranking_mode,
        min_kwp=args.min_kwp,
        min_usable_roof_area=args.min_usable_roof_area,
        clean_run=args.clean_run,
        dry_run=args.dry_run,
        limit_candidates=args.limit_candidates,
        use_lidar=args.lidar,
        run_id=args.run_id,
    )
    if result.get("status") == "error":
        print(f"\nERROR: {result.get('error')}", file=sys.stderr)
        return 1
    o = result["outputs"]
    print("\n" + "=" * 64)
    print(f"  Commercial run {result['run_id']} — {result['area']}")
    print(f"  Leads: {result['leads']} | candidates: {result['commercial_candidates']} | "
          f"total feasible {result['total_feasible_kwp']:,.0f} kWp")
    print(f"  QA: {result['qa_status']} | Calibration: {result['calibration_status']}")
    print(f"  Report:  {o['report_md']}")
    print(f"  HTML:    {o['report_html']}")
    print(f"  Leads:   {o['leads_csv']}")
    print(f"  Run dir: {result['run_path']}")
    print("=" * 64)
    return 0 if result["qa_status"] != "FAIL" else 2


def _domestic(args) -> int:
    legacy = ROOT / "data" / "pipeline" / "_archive" / "run_pipeline.py"
    if not legacy.exists():
        print("Legacy domestic pipeline not found at", legacy, file=sys.stderr)
        return 1
    print(f"[domestic] Delegating to preserved legacy pipeline: {legacy}")
    return subprocess.call([sys.executable, str(legacy)] + args.passthrough, cwd=str(legacy.parent))


def main() -> int:
    p = argparse.ArgumentParser(description="OZ Solar pipeline (commercial | domestic)")
    p.add_argument("--mode", choices=["commercial", "domestic"], default="commercial")
    p.add_argument("--area", default="Oxford", help='Area / town / postcode / outward code (e.g. "Oxford", "OX2")')
    p.add_argument("--leads", type=int, default=100)
    p.add_argument("--scenario", choices=["conservative", "central", "high"], default="central")
    p.add_argument("--ranking-mode", dest="ranking_mode",
                   choices=["balanced", "large-project-first", "fastest-payback", "highest-confidence"],
                   default="balanced")
    p.add_argument("--categories", default="", help="Comma list: warehouse,industrial,office,retail,school,university,leisure,public,mixed")
    p.add_argument("--min-kwp", dest="min_kwp", type=float, default=None)
    p.add_argument("--min-usable-roof-area", dest="min_usable_roof_area", type=float, default=None)
    p.add_argument("--clean-run", dest="clean_run", action="store_true")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="Skip live API calls (EPC/PVGIS/Companies House); use local data + fallbacks")
    p.add_argument("--lidar", action="store_true",
                   help="Measure large roofs from EA LiDAR DSM (HIGH confidence; slower). "
                        "Without this, roofs use the footprint+height model.")
    p.add_argument("--skip-heavy-lidar", dest="lidar", action="store_false",
                   help="Force-disable LiDAR refinement (default).")
    p.add_argument("--limit-candidates", dest="limit_candidates", type=int, default=None)
    p.add_argument("--run-id", dest="run_id", default=None)
    p.add_argument("passthrough", nargs="*", help="(domestic mode) args forwarded to legacy pipeline")
    args = p.parse_args()

    if args.mode == "commercial":
        return _commercial(args)
    return _domestic(args)


if __name__ == "__main__":
    sys.exit(main())
