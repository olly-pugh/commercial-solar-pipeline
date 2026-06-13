"""
Agent 11 — Calibration.

Validates the lead score (and SEPARATELY the project-scale score / usable roof area
/ feasible kWp) against KNOWN commercial PV adoption — when a ground-truth dataset
is supplied. Computes Pearson r and Spearman rho with p-values and sample size,
plus adoption-rate-by-bucket tables.

Honesty contract:
  * Correlations are NEVER fabricated or forced positive.
  * No ground truth  → status INCONCLUSIVE_NO_GROUNDTRUTH (+ QA warning).
  * Sample too small → status INCONCLUSIVE_SMALL_SAMPLE.
  * Weak/negative r on adequate sample → status WEAK / NEGATIVE, reported plainly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as sstats

from . import settings


def _corr(x, y):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = ~(np.isnan(x) | np.isnan(y))
    x, y = x[mask], y[mask]
    n = len(x)
    if n < 3 or np.std(x) == 0 or np.std(y) == 0:
        return {"n": int(n), "pearson_r": None, "pearson_p": None,
                "spearman_rho": None, "spearman_p": None}
    pr, pp = sstats.pearsonr(x, y)
    sr, sp = sstats.spearmanr(x, y)
    return {"n": int(n), "pearson_r": round(float(pr), 4), "pearson_p": round(float(pp), 5),
            "spearman_rho": round(float(sr), 4), "spearman_p": round(float(sp), 5)}


def _adoption_by_bucket(df, value_col, flag_col, bins, labels):
    out = []
    cats = pd.cut(df[value_col], bins=bins, labels=labels, include_lowest=True)
    for lab in labels:
        sub = df[cats == lab]
        if len(sub):
            out.append({"bucket": str(lab), "n": int(len(sub)),
                        "adoption_rate": round(float(sub[flag_col].mean()), 4)})
    return out


def calibrate(df: pd.DataFrame, cfg: dict, run_id: str,
              ground_truth: pd.DataFrame | None, audit=None) -> dict:
    min_n = settings.scoring()["qa"]["min_calibration_sample"]
    result = {
        "run_id": run_id, "model_version": df["model_version"].iloc[0] if len(df) else None,
        "calibration_sample_size": 0, "calibration_status": "INCONCLUSIVE_NO_GROUNDTRUTH",
        "notes": ("No known commercial-PV ground-truth dataset supplied "
                  "(data/commercial/ground_truth_pv.csv). Calibration cannot be computed "
                  "against observed adoption. Provide MCS installation export / verified "
                  "PV survey to enable calibration. Correlations are NOT fabricated."),
        "score_vs_adoption": None, "project_scale_vs_adoption": None,
        "usable_area_vs_adoption": None, "feasible_kwp_vs_adoption": None,
        "adoption_rate_by_score_bucket": [], "adoption_rate_by_project_size_bucket": [],
    }

    if ground_truth is None or ground_truth.empty:
        if audit is not None:
            audit.warn("calibration", result["notes"])
        return result

    # Join ground truth (has_commercial_pv) onto leads by uprn or building_id/toid.
    gt = ground_truth.copy()
    key = next((k for k in ("uprn_or_toid", "uprn", "toid", "building_id") if k in gt.columns), None)
    if key is None or "has_commercial_pv" not in gt.columns:
        result["calibration_status"] = "INCONCLUSIVE_BAD_GROUNDTRUTH"
        result["notes"] = "Ground-truth file lacks join key or has_commercial_pv column."
        return result
    gt["_k"] = gt[key].astype(str)
    df = df.copy()
    df["_k_uprn"] = df["uprn"].astype("Int64").astype(str)
    df["_k_toid"] = df["building_id"].astype(str)
    flagmap = dict(zip(gt["_k"], gt["has_commercial_pv"].astype(int)))
    df["has_pv"] = df.apply(
        lambda r: flagmap.get(r["_k_uprn"], flagmap.get(r["_k_toid"])), axis=1)
    sample = df[df["has_pv"].notna()].copy()
    n = len(sample)
    result["calibration_sample_size"] = int(n)

    if n < min_n:
        result["calibration_status"] = "INCONCLUSIVE_SMALL_SAMPLE"
        result["notes"] = f"Calibration sample n={n} < minimum {min_n}. Inconclusive."
        if audit is not None:
            audit.warn("calibration", result["notes"])
        return result

    result["score_vs_adoption"] = _corr(sample["final_score_10"], sample["has_pv"])
    result["project_scale_vs_adoption"] = _corr(sample["project_scale_score"], sample["has_pv"])
    result["usable_area_vs_adoption"] = _corr(sample["usable_roof_area_m2"], sample["has_pv"])
    result["feasible_kwp_vs_adoption"] = _corr(sample["feasible_kwp"], sample["has_pv"])

    result["adoption_rate_by_score_bucket"] = _adoption_by_bucket(
        sample, "final_score_10", "has_pv",
        bins=[0, 2, 4, 6, 8, 10], labels=["0-2", "2-4", "4-6", "6-8", "8-10"])
    result["adoption_rate_by_project_size_bucket"] = _adoption_by_bucket(
        sample, "feasible_kwp", "has_pv",
        bins=[0, 30, 100, 250, 1000, 1e9], labels=["micro", "small", "medium", "large", "very_large"])

    # Honest status from the headline (final-score) Pearson r.
    r = result["score_vs_adoption"]["pearson_r"]
    p = result["score_vs_adoption"]["pearson_p"]
    if r is None:
        result["calibration_status"] = "INCONCLUSIVE"
    elif r >= 0.2 and p is not None and p < 0.05:
        result["calibration_status"] = "PASS_POSITIVE"
    elif r < 0:
        result["calibration_status"] = "FAIL_NEGATIVE"
    else:
        result["calibration_status"] = "WEAK_INCONCLUSIVE"
    result["notes"] = (f"Calibrated on n={n}. Final-score Pearson r={r}, p={p}. "
                       f"Status={result['calibration_status']}. Reported as observed — not forced.")
    if audit is not None and result["calibration_status"] not in ("PASS_POSITIVE",):
        audit.warn("calibration", result["notes"])

    settings.write_json(settings.run_dir(run_id) / "calibration.json", result)
    return result
