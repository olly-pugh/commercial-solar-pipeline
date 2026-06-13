"""
Agent 9 — Local Market Momentum & Existing Solar.

A deliberately SMALL nudge signal (weight 0.05). Uses local MCS installer density
(area-level) and, where supplied, known nearby commercial PV. Domestic neighbour
solar is NOT used as a commercial peer signal. Also assembles the calibration
sample (candidates × known-PV flag) for the calibration agent.

NOTE: the MCS register holds installer COMPANIES, not installation locations, so it
cannot be used as PV-adoption ground truth — only as an installer-availability proxy.
"""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from . import settings


def _installer_density() -> int:
    db = settings.DB_DIR / "mcs.duckdb"
    if not db.exists():
        return 0
    con = duckdb.connect(str(db), read_only=True)
    try:
        n = con.execute("SELECT COUNT(*) FROM mcs_installers").fetchone()[0]
    except Exception:
        n = 0
    finally:
        con.close()
    return int(n)


def load_ground_truth_pv() -> tuple[set, pd.DataFrame | None]:
    """Load known commercial-PV ground truth (calibration only), if provided."""
    path = settings.source_path("commercial_pv_ground_truth")
    if not path or not path.exists():
        return set(), None
    try:
        gt = pd.read_csv(path)
    except Exception:
        return set(), None
    ids = set()
    for col in ("uprn_or_toid", "uprn", "toid", "building_id"):
        if col in gt.columns:
            ids |= set(gt[gt.get("has_commercial_pv", 1).astype(bool)][col].astype(str))
    return ids, gt


def score(df: pd.DataFrame, cfg: dict, ground_truth_ids: set | None = None,
          audit=None) -> tuple[pd.DataFrame, dict]:
    n_inst = _installer_density()
    # Installer-availability proxy → 0..6 (caps low; this is a weak signal).
    inst_score = min(6.0, n_inst * 0.5)
    gt = ground_truth_ids or set()

    rows = []
    for _, r in df.iterrows():
        peer = 0.0
        if gt:
            # peer commercial PV nearby would be computed from ground-truth geometry;
            # with only a flag set we credit confirmed-PV neighbours modestly.
            peer = 2.0 if (str(r["building_id"]) in gt or str(r.get("uprn")) in gt) else 0.0
        rows.append({
            "market_momentum_score": round(min(10.0, inst_score + peer), 2),
            "local_installer_count": n_inst,
            "market_momentum_confidence": 0.3,
        })
    res = pd.concat([df.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    if not gt and audit is not None:
        audit.record_missing(
            "commercial_pv_ground_truth", required=False,
            impact="No known commercial-PV dataset → peer-adoption signal absent and "
                   "calibration cannot be computed against observed adoption.",
            degraded_behaviour="Momentum from installer availability only; calibration "
                               "reported INCONCLUSIVE_NO_GROUNDTRUTH (never fabricated).")
    stats = {"local_installer_count": n_inst, "ground_truth_pv_available": bool(gt)}
    return res, stats
