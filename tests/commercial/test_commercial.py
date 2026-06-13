"""
Commercial pipeline test suite.

Mix of fast pure-function unit tests and one small integration run on the real
Oxford data (dry-run, limited candidates). Run:  pytest tests/commercial -q
"""
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from commercial import (territory, project_scale, economics, scoring, qa,
                        calibration, dashboard, settings, enrich)  # noqa: E402


# ── Territory intake ─────────────────────────────────────────────────────────
def test_territory_resolves_oxford():
    cfg = territory.resolve_territory("Oxford", leads=50)
    assert cfg["lad_code"] == "E07000178"
    assert cfg["bbox"]["north"] > cfg["bbox"]["south"]
    assert cfg["leads"] == 50


def test_territory_rejects_unknown_area():
    with pytest.raises(ValueError):
        territory.resolve_territory("Atlantis-on-Sea")


def test_territory_rejects_bad_ranking_mode():
    with pytest.raises(ValueError):
        territory.resolve_territory("Oxford", ranking_mode="nonsense")


# ── Project-scale scoring ────────────────────────────────────────────────────
def _scale_df(kwps):
    return pd.DataFrame([{
        "feasible_kwp": k, "usable_roof_area_m2": k * 8, "usable_roof_share": 0.6,
        "roof_confidence_score": 0.9, "category_confidence": 0.8, "shading_factor": 0.95,
        "existing_solar_flag": False, "roof_method": "lidar", "classification_confidence": 0.9,
        "payback_central_yr": 6.0,
    } for k in kwps])


def test_project_scale_monotonic_and_saturating():
    df, _ = project_scale.score(_scale_df([10, 50, 150, 400, 800, 3000]), {})
    s = df["project_scale_score"].tolist()
    assert all(s[i] <= s[i + 1] + 1e-6 for i in range(len(s) - 1)), "scale must be non-decreasing in kWp"
    assert s[-1] <= 10.0 and s[-2] >= 9.0, "must saturate near 800+ kWp"
    assert s[0] < s[3], "small must score below large"


def test_project_scale_penalises_low_confidence():
    base = _scale_df([400])
    low = base.copy(); low["roof_confidence_score"] = 0.4
    hs, _ = project_scale.score(base, {})
    ls, _ = project_scale.score(low, {})
    assert ls["project_scale_score"].iloc[0] < hs["project_scale_score"].iloc[0]


def test_project_size_categories():
    df, _ = project_scale.score(_scale_df([20, 60, 180, 600, 2000]), {})
    assert df["project_size_category"].tolist() == ["micro", "small", "medium", "large", "very_large"]


def test_min_kwp_filter():
    df, _ = project_scale.score(_scale_df([20, 120, 400]), {"min_kwp": 100})
    assert (df["feasible_kwp"] >= 100).all() and len(df) == 2


# ── Economics ────────────────────────────────────────────────────────────────
def test_payback_score_monotonic_decreasing():
    e = settings.scoring()["economics"]
    ys = [economics._payback_score(p, e) for p in [3, 6, 9, 14, 25]]
    assert all(ys[i] >= ys[i + 1] for i in range(len(ys) - 1))


def test_capex_economies_of_scale():
    bands = settings.pv_costs()["capex_gbp_per_kwp"]
    small = economics._capex_per_kwp(40, bands)["central"]
    big = economics._capex_per_kwp(900, bands)["central"]
    assert big < small, "£/kWp must fall with size"


# ── Roof physics plausibility (via integration df) ───────────────────────────
@pytest.fixture(scope="module")
def oxford_run():
    """One small dry-run on real Oxford data, reused across tests."""
    from commercial.pipeline import run_commercial
    res = run_commercial("Oxford", leads=40, dry_run=True, limit_candidates=250,
                         run_id="OXF-COM-PYTEST-1")
    assert res["status"] == "complete", res.get("error")
    leads = pd.read_csv(Path(res["run_path"]) / "all_scored.csv")
    return res, leads


def test_kwp_density_plausible(oxford_run):
    _, leads = oxford_run
    d = leads["feasible_kwp"] / leads["usable_roof_area_m2"]
    assert d.min() >= 0.083 and d.max() <= 0.20, "kWp/m² must be physically plausible"


def test_generation_per_kwp_plausible(oxford_run):
    _, leads = oxford_run
    g = leads["annual_generation_kwh"] / leads["feasible_kwp"]
    assert g.min() >= 700 and g.max() <= 1150


def test_usable_not_exceeding_total(oxford_run):
    _, leads = oxford_run
    assert (leads["usable_roof_area_m2"] <= leads["total_roof_area_m2"] + 0.1).all()


# ── Scoring / ranking ────────────────────────────────────────────────────────
def test_weights_sum_to_one():
    w = settings.scoring()["weights"]
    assert abs(sum(w.values()) - 1.0) < 1e-9


def test_final_score_in_range_and_differentiated(oxford_run):
    _, leads = oxford_run
    assert leads["final_score_10"].between(0, 10).all()
    assert leads["final_score_10"].round(2).nunique() >= 15, "no score compression"


def test_project_scale_differentiated(oxford_run):
    _, leads = oxford_run
    assert leads["project_scale_score"].round(2).nunique() >= 10
    # project-scale must differ from technical (not a copy)
    assert not leads["project_scale_score"].equals(leads["technical_score"])


def test_large_project_first_ranks_bigger_higher():
    from commercial.pipeline import run_commercial
    res = run_commercial("Oxford", leads=20, dry_run=True, limit_candidates=250,
                         ranking_mode="large-project-first", run_id="OXF-COM-PYTEST-LPF")
    leads = pd.read_csv(Path(res["run_path"]) / "all_scored.csv")
    top10_kwp = leads.head(10)["feasible_kwp"].median()
    bottom_kwp = leads.tail(20)["feasible_kwp"].median()
    assert top10_kwp > bottom_kwp, "large-project-first must surface bigger projects"


# ── QA blocking checks ───────────────────────────────────────────────────────
def test_qa_claims_negation_aware():
    assert qa._has_overclaim("savings are guaranteed for all") is not None
    assert qa._has_overclaim("this is NOT a prediction of guaranteed adoption") is None


def test_qa_detects_score_compression(oxford_run):
    res, leads = oxford_run
    flat = leads.copy()
    flat["final_score_10"] = 7.0
    flat["project_scale_score"] = 5.0
    out = qa.run_qa(flat, {"area": "Oxford", "ranking_mode": "balanced"},
                    {"distinct_final_scores_top100": 1, "distinct_project_scale_top100": 1},
                    {"calibration_status": "INCONCLUSIVE_NO_GROUNDTRUTH"},
                    {"epc_status": "unavailable"}, "QA-COMPRESS")
    codes = {f["code"] for f in out["findings"]}
    assert "score_compression" in codes and out["qa_status"] == "FAIL"


# ── Calibration honesty ──────────────────────────────────────────────────────
def test_calibration_inconclusive_without_groundtruth(oxford_run):
    res, leads = oxford_run
    cal = calibration.calibrate(leads, {"area": "Oxford"}, "CAL-NONE", None)
    assert cal["calibration_status"] == "INCONCLUSIVE_NO_GROUNDTRUTH"
    assert cal["score_vs_adoption"] is None  # never fabricated


def test_calibration_computes_when_groundtruth_present(oxford_run):
    res, leads = oxford_run
    # Synthetic ground truth: higher-scoring buildings more likely to "have PV".
    gt = leads[["building_id", "final_score_10"]].copy()
    gt["uprn_or_toid"] = gt["building_id"].astype(str)
    gt["has_commercial_pv"] = (gt["final_score_10"] > gt["final_score_10"].median()).astype(int)
    cal = calibration.calibrate(leads, {"area": "Oxford"}, "CAL-SYNTH",
                                gt[["uprn_or_toid", "has_commercial_pv"]])
    assert cal["calibration_sample_size"] >= 30
    assert cal["project_scale_vs_adoption"] is not None  # scale test computed
    assert cal["score_vs_adoption"]["pearson_r"] is not None


# ── Dashboard ────────────────────────────────────────────────────────────────
def test_dashboard_routes_load():
    c = dashboard.app.test_client()
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/readiness").get_json()["can_run"] is True
    page = c.get("/").get_data(as_text=True)
    assert "Generate report" in page and "Full report" in page
    # /api/latest must return valid JSON (used to auto-load the dashboard)
    assert "run_id" in c.get("/api/latest").get_json()


def test_demo_mode_present_and_isolated():
    """DEMO mode must load, contain the full guided flow, reference ONLY the
    existing report, expose all 12 agent names, and — critically — contain NO
    production execution paths (it can never run the pipeline)."""
    c = dashboard.app.test_client()
    page = c.get("/demo").get_data(as_text=True)
    assert "Where do you want to search" in page          # entry caption
    assert "Great!" in page and "Oxford report coming right up" in page  # transition
    # all 12 canonical agent names injected
    for a in dashboard.DEMO_AGENTS:
        assert a["name"] in page
    assert len(dashboard.DEMO_AGENTS) == 12
    # reuses an EXISTING report file, never regenerates
    assert "commercial_report_" in page and ".html" in page
    # ISOLATION: no production execution path may appear in the demo page
    for forbidden in ("/api/run", "run_pipeline", "method:'POST'", "method: 'POST'",
                      ".post(", "/api/demo/run"):
        assert forbidden not in page, f"demo page leaks execution path: {forbidden}"


def test_demo_leads_endpoint_is_readonly_coordinates():
    """The only demo data endpoint returns static coordinates and never errors."""
    c = dashboard.app.test_client()
    # GET-only: POST must not be routed
    assert c.post("/api/demo/leads").status_code in (404, 405)
    data = c.get("/api/demo/leads").get_json()
    assert "leads" in data
    for ld in data["leads"][:5]:
        assert -2.0 < ld["lon"] < -0.5 and 51.0 < ld["lat"] < 52.5  # Oxford-ish bbox


def test_enrich_adds_location_links_and_honest_address():
    # Two leads: one with a postcode that has companies, one with no postcode.
    df = pd.DataFrame([
        {"rank": 1, "postcode": "OX4 6LT", "os_grid_ref": "SP 557 036",
         "lat": 51.7288, "lon": -1.1948, "address": float("nan"), "caveat_tags": ""},
        {"rank": 2, "postcode": "", "os_grid_ref": "SP 500 050",
         "lat": 51.75, "lon": -1.25, "address": float("nan"), "caveat_tags": ""},
    ])
    out, stats = enrich.enrich_leads(df, {"leads": 2}, max_leads=2)
    # Every enriched lead gets a Google Maps location link from its coordinates.
    assert out.iloc[0]["location_maps_url"].startswith("https://www.google.com/maps")
    assert out.iloc[1]["location_maps_url"].startswith("https://www.google.com/maps")
    # No lead is ever left with a NaN/empty address (honest fallback to grid+postcode).
    assert all(isinstance(a, str) and a.strip() for a in out["address"])
    # Stats are coherent and never claim more than was enriched.
    assert stats["enriched_leads"] == 2
    assert 0 <= stats["leads_with_company_link"] <= 2


def test_enrich_company_link_carries_owner_caveat():
    df = pd.DataFrame([{
        "rank": 1, "postcode": "OX4 6LT", "os_grid_ref": "SP 557 036",
        "lat": 51.7288, "lon": -1.1948, "address": float("nan"), "caveat_tags": "",
        "primary_company_name": None}])
    out, _ = enrich.enrich_leads(df, {"leads": 1}, max_leads=1)
    # If a company was attached, it must carry the registered-office (not-owner) caveat.
    if out.iloc[0]["primary_company_name"]:
        assert "registered_office" in out.iloc[0]["caveat_tags"]
        assert out.iloc[0]["primary_company_url"].startswith(
            "https://find-and-update.company-information.service.gov.uk/company/")


def test_report_artifacts_exist(oxford_run):
    res, _ = oxford_run
    o = res["outputs"]
    for k in ("report_md", "report_html", "leads_csv"):
        assert Path(o[k]).exists(), f"{k} missing"
    assert (Path(o["charts_dir"]) / "funnel.png").exists()
