"""
Per-run audit trail + missing-data register for the commercial pipeline.

Produces agent_audit_log.json (one entry per stage) and missing_data.json,
satisfying the data-lineage and "no silent skips" requirements.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from . import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AuditTrail:
    def __init__(self, run_id: str, run_path: Path):
        self.run_id = run_id
        self.run_path = run_path
        self.stages: list[dict] = []
        self.missing: list[dict] = []
        self._t0: dict[str, float] = {}

    # ── stage lifecycle ──────────────────────────────────────────────────────
    def start(self, stage: str, inputs: dict | None = None) -> None:
        self._t0[stage] = time.time()
        self.stages.append({
            "run_id": self.run_id,
            "stage_name": stage,
            "started_at": _now(),
            "finished_at": None,
            "status": "running",
            "fresh_or_cached": "fresh",
            "inputs": inputs or {},
            "outputs": {},
            "warnings": [],
            "errors": [],
            "runtime_s": None,
            "checkpoint_path": None,
        })

    def _cur(self, stage: str) -> dict:
        for s in reversed(self.stages):
            if s["stage_name"] == stage:
                return s
        raise KeyError(stage)

    def finish(self, stage: str, status: str = "succeeded", outputs: dict | None = None,
               warnings: list | None = None, errors: list | None = None,
               cached: bool = False, checkpoint: str | None = None) -> None:
        s = self._cur(stage)
        s["finished_at"] = _now()
        s["status"] = status
        s["fresh_or_cached"] = "cached" if cached else "fresh"
        if outputs:
            s["outputs"].update(outputs)
        if warnings:
            s["warnings"].extend(warnings)
        if errors:
            s["errors"].extend(errors)
        s["runtime_s"] = round(time.time() - self._t0.get(stage, time.time()), 2)
        s["checkpoint_path"] = checkpoint

    def warn(self, stage: str, msg: str) -> None:
        try:
            self._cur(stage)["warnings"].append(msg)
        except KeyError:
            pass

    # ── missing-data register ────────────────────────────────────────────────
    def record_missing(self, source: str, required: bool, impact: str,
                       degraded_behaviour: str) -> None:
        self.missing.append({
            "source": source,
            "required": required,
            "impact": impact,
            "degraded_behaviour": degraded_behaviour,
            "recorded_at": _now(),
        })

    # ── persistence ──────────────────────────────────────────────────────────
    def save(self) -> dict[str, Path]:
        a = settings.write_json(self.run_path / "agent_audit_log.json", self.stages)
        m = settings.write_json(self.run_path / "missing_data.json", self.missing)
        return {"agent_audit_log": a, "missing_data": m}
