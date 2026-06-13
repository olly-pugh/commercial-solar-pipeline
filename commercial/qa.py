"""
Agent 13 — QA / Red-Team (deterministic blocking checks).

Produces qa_status = PASS | WARN | FAIL with machine-readable findings and a
human-readable red-team report. FAIL blocks delivery. Implements the section-9
checks: domestic leakage, missing-source-but-high-confidence, score compression,
impossible PV physics, bad joins, existing-solar mislabel, project-scale misuse,
calibration dishonesty, missing caveats, claims risk, licence/privacy.
"""
from __future__ import annotations

import re
import pandas as pd

from . import settings

_DOMESTIC_TERMS = re.compile(r"\b(house|flat|bungalow|maisonette|cottage|terrace dwelling|apartment)\b", re.I)
# Affirmative over-claims only. Negation-aware scan below excludes disclaimers
# such as "NOT a prediction of guaranteed adoption".
_CLAIMS_BANNED = re.compile(
    r"\b(will adopt|guaranteed|formal quote|planning approved|grid approved|"
    r"certain to (?:adopt|buy|convert)|definitely will)\b", re.I)
_NEGATION = re.compile(r"\b(not|never|no|without|cannot|can't|isn't|aren't|rather than)\b", re.I)


def _has_overclaim(text: str) -> str | None:
    """Return the offending phrase, or None. Skips matches preceded by a negation
    within the prior ~8 words (disclaimers correctly negate over-claims)."""
    for m in _CLAIMS_BANNED.finditer(text):
        prefix = text[max(0, m.start() - 60):m.start()]
        if _NEGATION.search(prefix):
            continue
        return m.group(0)
    return None


def _add(findings, severity, code, detail):
    findings.append({"severity": severity, "code": code, "detail": detail})


def run_qa(leads: pd.DataFrame, cfg: dict, scoring_stats: dict, calibration: dict,
           discovery_stats: dict, run_id: str, report_text: str = "") -> dict:
    q = settings.scoring()["qa"]
    findings: list[dict] = []
    top = leads[leads["included_in_top_leads"]] if "included_in_top_leads" in leads else leads
    top100 = leads.head(100)

    # 1. Domestic leakage — measured on the BUILDING's classification, not on the
    #    address text. (Addresses are now sourced from companies' registered offices,
    #    whose text may contain words like "House"/"Cottages" without the lead
    #    building being domestic; the text heuristic is only a fallback for
    #    building-derived addresses.)
    _DOM_CATS = {"house", "flat", "bungalow", "maisonette", "dwelling", "residential", "domestic"}
    cat_dom = sum(1 for c in top.get("property_category", pd.Series([], dtype=str)).dropna()
                  if str(c).lower() in _DOM_CATS)
    # text heuristic only on addresses derived from the building itself (not reg. office)
    src = top.get("address_source", pd.Series(["unknown"] * len(top), index=top.index))
    text_dom = sum(1 for a, s in zip(top.get("address", pd.Series([], dtype=str)), src)
                   if isinstance(a, str) and s != "companies_house_registered_office"
                   and _DOMESTIC_TERMS.search(a))
    dom_hits = cat_dom + text_dom
    dom_frac = dom_hits / max(len(top), 1)
    if dom_frac > q["domestic_leakage_max_frac"]:
        _add(findings, "FAIL", "domestic_leakage",
             f"{dom_hits} top leads ({dom_frac:.0%}) classify as domestic "
             f"(category={cat_dom}, building-address text={text_dom}).")

    # 2. Missing source but high confidence
    epc_ok = discovery_stats.get("epc_status") == "ok"
    if not epc_ok and (top["confidence_score"].median() > 0.75):
        _add(findings, "WARN", "missing_source_high_confidence",
             "Non-domestic EPC unavailable yet median confidence >0.75 — verify confidence model.")

    # 3. Score compression
    if scoring_stats["distinct_final_scores_top100"] < q["min_distinct_final_scores_top100"]:
        _add(findings, "FAIL", "score_compression",
             f"Only {scoring_stats['distinct_final_scores_top100']} distinct final scores in top 100 "
             f"(min {q['min_distinct_final_scores_top100']}).")
    if scoring_stats["distinct_project_scale_top100"] < q["min_distinct_project_scale_top100"]:
        _add(findings, "FAIL", "project_scale_compression",
             f"Project-scale score has only {scoring_stats['distinct_project_scale_top100']} distinct "
             f"values in top 100 (min {q['min_distinct_project_scale_top100']}).")

    # 4. Impossible PV physics
    kwp_per_m2 = (leads["feasible_kwp"] / leads["usable_roof_area_m2"]).replace([float("inf")], 0)
    bad_density = leads[(kwp_per_m2 < q["kwp_per_m2_min"]) | (kwp_per_m2 > q["kwp_per_m2_max"])]
    if len(bad_density):
        _add(findings, "FAIL", "impossible_kwp_density",
             f"{len(bad_density)} buildings have kWp/m² outside "
             f"[{q['kwp_per_m2_min']}, {q['kwp_per_m2_max']}].")
    gen_per_kwp = (leads["annual_generation_kwh"] / leads["feasible_kwp"]).replace([float("inf")], 0)
    bad_gen = leads[(gen_per_kwp < q["generation_per_kwp_min"]) | (gen_per_kwp > q["generation_per_kwp_max"])]
    if len(bad_gen):
        _add(findings, "FAIL", "impossible_generation",
             f"{len(bad_gen)} buildings have generation/kWp outside "
             f"[{q['generation_per_kwp_min']}, {q['generation_per_kwp_max']}].")
    if (leads["usable_roof_area_m2"] > leads["total_roof_area_m2"] + 0.1).any():
        _add(findings, "FAIL", "usable_exceeds_total", "usable_roof_area > total_roof_area on some rows.")
    ratio = leads["total_roof_area_m2"] / leads["footprint_area_m2"]
    if (ratio > q["max_roof_to_footprint_ratio"]).any():
        n = int((ratio > q["max_roof_to_footprint_ratio"]).sum())
        _add(findings, "WARN", "roof_footprint_ratio",
             f"{n} buildings have roof/footprint ratio > {q['max_roof_to_footprint_ratio']}.")

    # 5. Bad joins — duplicate UPRN in top leads
    if "uprn" in top:
        dup = top[top["uprn"].notna()]["uprn"].duplicated().sum()
        if dup > 0:
            _add(findings, "WARN", "duplicate_uprn", f"{int(dup)} duplicate UPRNs in top leads.")

    # 6. Existing solar mislabelled as new install
    if "existing_solar_flag" in leads:
        mis = leads[(leads["existing_solar_flag"]) & (~leads["caveat_tags"].str.contains("expansion", na=False))]
        if len(mis):
            _add(findings, "FAIL", "existing_pv_mislabel",
                 f"{len(mis)} buildings flagged existing PV but not labelled expansion/repowering.")

    # 7. Project-scale misuse — large roof, high scale, but low confidence & poor economics
    misuse = leads[(leads["project_scale_score"] >= 8) & (leads["confidence_score"] < 0.35)
                   & (leads["economics_score"] < 4)]
    if len(misuse):
        _add(findings, "WARN", "project_scale_overweight",
             f"{len(misuse)} high-scale leads have low confidence AND weak economics — verify not area-driven.")

    # 8. Calibration honesty
    status = calibration.get("calibration_status", "")
    if status.startswith("INCONCLUSIVE") or status in ("WEAK_INCONCLUSIVE", "FAIL_NEGATIVE"):
        _add(findings, "WARN", "calibration_inconclusive",
             f"Calibration {status}: {calibration.get('notes','')[:160]}")
    if calibration.get("project_scale_vs_adoption") is None and calibration.get("calibration_sample_size", 0) >= 30:
        _add(findings, "FAIL", "calibration_missing_scale_test",
             "Calibration claims a sample but project-scale correlation not computed.")

    # 9. Missing caveats (every lead must carry feasibility caveats)
    if "caveat_tags" in top:
        no_caveat = top[~top["caveat_tags"].str.contains("requires_site_survey", na=False)]
        if len(no_caveat):
            _add(findings, "FAIL", "missing_caveats",
                 f"{len(no_caveat)} top leads lack the mandatory site-survey caveat.")

    # 10. Claims risk in report text (negation-aware: disclaimers are allowed)
    if report_text:
        bad = _has_overclaim(report_text)
        if bad:
            _add(findings, "FAIL", "claims_risk",
                 f"Report text contains an affirmative over-claim: '{bad}'.")

    # 11. Licence/privacy — occupier presented without provenance
    if "occupier_name" in top:
        bad_prov = top[(top["occupier_name"].notna())
                       & (top.get("occupier_source", "unknown") == "unknown")]
        if len(bad_prov):
            _add(findings, "FAIL", "occupier_without_provenance",
                 f"{len(bad_prov)} leads show an occupier name without a lawful source.")

    severities = {f["severity"] for f in findings}
    status_out = "FAIL" if "FAIL" in severities else ("WARN" if "WARN" in severities else "PASS")
    summary = {
        "qa_status": status_out,
        "n_findings": len(findings),
        "n_fail": sum(1 for f in findings if f["severity"] == "FAIL"),
        "n_warn": sum(1 for f in findings if f["severity"] == "WARN"),
        "findings": findings,
        "checks_run": [
            "domestic_leakage", "missing_source_high_confidence", "score_compression",
            "impossible_physics", "bad_joins", "existing_pv_mislabel", "project_scale_misuse",
            "calibration_honesty", "missing_caveats", "claims_risk", "licence_privacy"],
        "remediation": _remediation(findings),
    }
    settings.write_json(settings.run_dir(run_id) / "qa_report.json", summary)
    (settings.run_dir(run_id) / "red_team_report.md").write_text(_human_report(summary, cfg))
    return summary


def _remediation(findings) -> list:
    fixes = {
        "score_compression": "Increase score resolution / verify sub-scores vary; check upstream inputs.",
        "project_scale_compression": "Verify usable-area inputs differ; check saturating curve params.",
        "impossible_kwp_density": "Check m2_per_kwp config and usable-area derivation.",
        "impossible_generation": "Check specific-yield source/bounds.",
        "domestic_leakage": "Tighten discovery min_footprint / domestic-exclusion thresholds.",
        "existing_pv_mislabel": "Label existing-PV sites as expansion/repowering.",
        "missing_caveats": "Ensure scoring appends mandatory caveats to every lead.",
        "claims_risk": "Replace over-claims with 'indicative/estimated/requires survey' wording.",
        "calibration_missing_scale_test": "Compute project-scale correlation when sample present.",
    }
    return [{"code": f["code"], "action": fixes.get(f["code"], "Review finding detail.")}
            for f in findings]


def _human_report(summary, cfg) -> str:
    lines = [f"# Commercial Lead QA / Red-Team Report",
             f"**Status: {summary['qa_status']}**  —  {summary['n_fail']} FAIL, {summary['n_warn']} WARN\n",
             f"Area: {cfg.get('area')} | ranking mode: {cfg.get('ranking_mode')}\n",
             "## Findings"]
    if not summary["findings"]:
        lines.append("No issues found. All deterministic checks passed.")
    for f in summary["findings"]:
        lines.append(f"- **[{f['severity']}] {f['code']}** — {f['detail']}")
    lines.append("\n## Remediation")
    for rem in summary["remediation"]:
        lines.append(f"- `{rem['code']}` → {rem['action']}")
    lines.append("\n## Checks executed")
    lines += [f"- {c}" for c in summary["checks_run"]]
    return "\n".join(lines)
