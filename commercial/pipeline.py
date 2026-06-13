"""
Commercial pipeline orchestrator.

Runs the deterministic agent sequence, writes per-run artifacts + audit trail +
dashboard progress, runs calibration + QA, and generates the report. Returns a
result dict. Never throws on a single stage's degraded data — it records missing
data and continues, then QA decides PASS/WARN/FAIL.

Stage order (note deliberate deviation from the spec's numbering): economics runs
BEFORE project-scale so the scale agent can penalise "large but poor economics".
"""
from __future__ import annotations

import json
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import (settings, territory, discovery, roof, economics, project_scale,
               business, ownership, policy, momentum, scoring, calibration, qa,
               enrich, report, MODEL_VERSION)
from .audit import AuditTrail

PROGRESS_STAGES = [
    "territory", "discovery", "roof", "economics", "project_scale", "business",
    "ownership", "policy", "momentum", "scoring", "enrich", "calibration", "qa", "report",
]


def _progress(run_path: Path, stage: str, status: str, detail: str = "", extra: dict | None = None):
    pf = run_path / "progress.json"
    data = {"stages": {}, "updated_at": datetime.now(timezone.utc).isoformat()}
    if pf.exists():
        try:
            data = json.loads(pf.read_text())
        except Exception:
            pass
    data.setdefault("stages", {})
    data["stages"][stage] = {"status": status, "detail": detail, **(extra or {})}
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    pf.write_text(json.dumps(data, indent=2, default=str))


def _gen_run_id(area: str) -> str:
    code = "".join(c for c in area[:3].upper() if c.isalpha()) or "RUN"
    year = datetime.now().year
    base = settings.RUNS_DIR
    n = 1
    existing = [p.name for p in base.glob(f"{code}-COM-{year}-*")] if base.exists() else []
    while f"{code}-COM-{year}-{n:03d}" in existing:
        n += 1
    return f"{code}-COM-{year}-{n:03d}"


def run_commercial(area: str, **opts) -> dict:
    t_start = time.time()
    cfg = territory.resolve_territory(area, **{k: v for k, v in opts.items() if k in {
        "categories", "leads", "scenario", "ranking_mode", "min_kwp",
        "min_usable_roof_area", "clean_run", "dry_run", "limit_candidates", "use_lidar"}})
    run_id = opts.get("run_id") or _gen_run_id(cfg["area"])
    run_path = settings.run_dir(run_id)
    audit = AuditTrail(run_id, run_path)
    settings.write_json(run_path / "territory_config.json", cfg)
    for s in PROGRESS_STAGES:
        _progress(run_path, s, "pending")

    log = (settings.LOGS_DIR / f"{run_id}.log").open("w")
    def say(msg):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(line); log.write(line + "\n"); log.flush()

    say(f"=== Commercial pipeline {run_id} | area={cfg['area']} | mode={cfg['ranking_mode']} "
        f"| scenario={cfg['scenario']} | dry_run={cfg['dry_run']} ===")

    funnel = {}
    pvgis_ok = False
    try:
        # 1. Territory
        audit.start("territory", {"area": area})
        _progress(run_path, "territory", "running")
        audit.finish("territory", outputs={"bbox": cfg["bbox"], "lad": cfg["lad_code"]})
        _progress(run_path, "territory", "succeeded", f"{cfg['area']} resolved")

        # 2. Discovery
        audit.start("discovery", {"min_footprint_m2": settings.scoring()["discovery"]["min_footprint_m2"]})
        _progress(run_path, "discovery", "running")
        cands, excluded, dstats = discovery.discover(cfg, audit=audit)
        funnel["Raw building candidates"] = dstats["raw_candidates"]
        funnel["Commercial candidates"] = dstats["commercial"]
        audit.finish("discovery", status="succeeded" if len(cands) else "warning",
                     outputs=dstats, warnings=[] if len(cands) else ["no commercial candidates"])
        _progress(run_path, "discovery", "succeeded", f"{len(cands)} commercial candidates",
                  {"commercial": int(len(cands)), "excluded_domestic": int(len(excluded))})
        if cands.empty:
            raise RuntimeError("No commercial candidates found — cannot proceed.")
        excluded.to_csv(run_path / "domestic_exclusions.csv", index=False)

        # 3. Roof
        gt_ids, gt_df = momentum.load_ground_truth_pv()
        audit.start("roof", {"candidates": len(cands)})
        _progress(run_path, "roof", "running")
        cands, rstats = roof.analyse(cands, cfg, ground_truth_pv=gt_ids or None,
                                     use_lidar=cfg.get("use_lidar", False), audit=audit)
        pvgis_ok = rstats["specific_yield_source"] == "pvgis"
        funnel["Valid roof model"] = int((cands["feasible_kwp"] > 0).sum())
        audit.finish("roof", outputs=rstats)
        _progress(run_path, "roof", "succeeded",
                  f"{rstats['total_feasible_kwp']:,.0f} kWp total ({rstats['specific_yield_source']})")

        # 4. Economics (before project scale — deliberate)
        audit.start("economics", {"scenario": cfg["scenario"]})
        _progress(run_path, "economics", "running")
        cands, estats = economics.model(cands, cfg, audit=audit)
        funnel["Valid economics"] = int(cands["payback_central_yr"].notna().sum())
        audit.finish("economics", outputs=estats)
        _progress(run_path, "economics", "succeeded",
                  f"median payback {estats['median_payback_central_yr']}y")

        # 5. Project scale
        audit.start("project_scale", {})
        _progress(run_path, "project_scale", "running")
        cands, sstats = project_scale.score(cands, cfg, audit=audit)
        audit.finish("project_scale", outputs={k: sstats[k] for k in ("size_category_breakdown", "min_scale_filter_removed")})
        _progress(run_path, "project_scale", "succeeded", str(sstats["size_category_breakdown"]))

        # 6-9. Context sub-scores
        for name, fn in [("business", lambda d: business.score(d, cfg, audit=audit)),
                         ("ownership", lambda d: ownership.score(d, cfg, audit=audit)),
                         ("policy", lambda d: policy.score(d, cfg, audit=audit)),
                         ("momentum", lambda d: momentum.score(d, cfg, ground_truth_ids=gt_ids, audit=audit))]:
            audit.start(name, {})
            _progress(run_path, name, "running")
            cands, st = fn(cands)
            audit.finish(name, outputs=st)
            _progress(run_path, name, "succeeded")

        # 10. Scoring & ranking
        audit.start("scoring", {"mode": cfg["ranking_mode"]})
        _progress(run_path, "scoring", "running")
        leads, scstats = scoring.rank(cands, cfg, run_id, pvgis_ok, audit=audit)
        funnel["Ranked leads"] = int(len(leads))
        settings.write_json(run_path / "score_explainability.json", scstats)
        audit.finish("scoring", outputs=scstats)
        _progress(run_path, "scoring", "succeeded",
                  f"{scstats['distinct_final_scores_top100']} distinct top-100 scores")

        # Lead enrichment — real addresses, location links and company links for the
        # top leads (lawful public register only; capped; cached on disk).
        audit.start("enrich", {})
        _progress(run_path, "enrich", "running")
        n_leads = int(cfg.get("leads") or 100)
        leads, enrich_stats = enrich.enrich_leads(
            leads, cfg, audit=audit, max_leads=0 if cfg["dry_run"] else max(n_leads, 100))
        audit.finish("enrich", outputs=enrich_stats)
        _progress(run_path, "enrich", "succeeded",
                  f"{enrich_stats['leads_with_company_link']} company links, "
                  f"{enrich_stats['leads_with_real_address']} street addresses")
        say(f"Enrichment: {enrich_stats['leads_with_company_link']} company links, "
            f"{enrich_stats['leads_with_real_address']} street addresses "
            f"(CH available={enrich_stats['companies_house_available']})")

        # 11. Calibration
        audit.start("calibration", {})
        _progress(run_path, "calibration", "running")
        cal = calibration.calibrate(leads, cfg, run_id, gt_df, audit=audit)
        audit.finish("calibration", outputs={"status": cal["calibration_status"],
                                             "n": cal["calibration_sample_size"]})
        _progress(run_path, "calibration", "succeeded", cal["calibration_status"])

        # 12. Report
        audit.start("report", {})
        _progress(run_path, "report", "running")
        # Persist full scored table + commercial_lead_scores schema slice.
        leads.to_csv(run_path / "all_scored.csv", index=False)
        out = report.generate(leads, cfg, run_id, funnel, cal, audit.stages, audit.missing,
                              scstats, rstats, estats, sstats, dstats)
        audit.finish("report", outputs={k: v for k, v in out.items() if k != "report_text"})
        _progress(run_path, "report", "succeeded", "report + charts + CSV/XLSX")

        # 13. QA (uses report text for claims scan)
        audit.start("qa", {})
        _progress(run_path, "qa", "running")
        qa_res = qa.run_qa(leads, cfg, scstats, cal, dstats, run_id, report_text=out["report_text"])
        audit.finish("qa", status="succeeded" if qa_res["qa_status"] != "FAIL" else "warning",
                     outputs={"qa_status": qa_res["qa_status"], "n_fail": qa_res["n_fail"]})
        _progress(run_path, "qa", qa_res["qa_status"].lower() if qa_res["qa_status"] != "PASS" else "succeeded",
                  f"{qa_res['qa_status']} ({qa_res['n_fail']} fail / {qa_res['n_warn']} warn)")

        audit.save()
        result = {
            "run_id": run_id, "area": cfg["area"], "status": "complete",
            "qa_status": qa_res["qa_status"], "calibration_status": cal["calibration_status"],
            "leads": int(len(leads)), "commercial_candidates": dstats["commercial"],
            "total_feasible_kwp": rstats["total_feasible_kwp"],
            "total_usable_roof_area_m2": rstats["total_usable_roof_area_m2"],
            "ranking_mode": cfg["ranking_mode"], "funnel": funnel,
            "outputs": out, "run_path": str(run_path),
            "runtime_s": round(time.time() - t_start, 1),
            "missing_data": audit.missing,
        }
        settings.write_json(run_path / "result.json", result)
        say(f"=== DONE {run_id} in {result['runtime_s']}s | QA={qa_res['qa_status']} "
            f"| calibration={cal['calibration_status']} ===")
        log.close()
        return result

    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        say(f"PIPELINE ERROR: {e}\n{tb}")
        try:
            audit.save()
        except Exception:
            pass
        log.close()
        return {"run_id": run_id, "status": "error", "error": str(e),
                "traceback": tb, "run_path": str(run_path)}
