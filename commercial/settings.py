"""
Settings, paths, config + environment loading for the commercial pipeline.

All paths are anchored to the workspace root (this file's grandparent) so the
pipeline runs identically regardless of the caller's working directory.
"""
from __future__ import annotations

import os
import json
from pathlib import Path
from functools import lru_cache

import yaml

# Workspace root = .../.openclaw/workspace  (this file is workspace/commercial/settings.py)
ROOT = Path(__file__).resolve().parent.parent

# ── Standard directories (created on import so first run never fails) ─────────
CONFIG_DIR   = ROOT / "config"
DATA_DIR     = ROOT / "data"
DB_DIR       = DATA_DIR / "db"
CACHE_DIR    = DATA_DIR / "cache" / "commercial"
RUNS_DIR     = ROOT / "runs" / "commercial"
REPORTS_DIR  = ROOT / "reports" / "commercial"
LOGS_DIR     = ROOT / "logs" / "commercial"
COMM_DATA    = DATA_DIR / "commercial"

for _d in (CACHE_DIR, RUNS_DIR, REPORTS_DIR, LOGS_DIR, COMM_DATA):
    _d.mkdir(parents=True, exist_ok=True)


# ── .env loader (no external dependency) ─────────────────────────────────────
def load_dotenv(path: Path | None = None) -> dict:
    """Parse KEY=VALUE lines from .env into os.environ (without overwriting set vars)."""
    path = path or (ROOT / ".env")
    found = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            found[k] = v
            os.environ.setdefault(k, v)
    return found


load_dotenv()


def env(name: str, default: str | None = None) -> str | None:
    """Read an environment variable. NEVER logs the value."""
    return os.environ.get(name, default)


def env_present(name: str) -> bool:
    v = os.environ.get(name)
    return bool(v and v.strip())


# ── Config loaders (cached) ──────────────────────────────────────────────────
@lru_cache(maxsize=None)
def _load_yaml(name: str) -> dict:
    p = CONFIG_DIR / name
    if not p.exists():
        raise FileNotFoundError(f"Missing config file: {p}")
    return yaml.safe_load(p.read_text())


def data_sources() -> dict:
    return _load_yaml("commercial_data_sources.yml")


def tariffs() -> dict:
    return _load_yaml("commercial_tariffs.yml")


def pv_costs() -> dict:
    return _load_yaml("commercial_pv_costs.yml")


def archetypes() -> dict:
    return _load_yaml("commercial_archetypes.yml")


def scoring() -> dict:
    return _load_yaml("commercial_scoring.yml")


# ── Resolve a declared source's local path (env override → registry default) ──
def source_path(source_key: str) -> Path | None:
    """Return the resolved local path for a data source, honouring env overrides."""
    src = data_sources()["sources"].get(source_key)
    if not src:
        return None
    ev = src.get("env_var")
    if ev and env_present(ev) and not (env(ev, "").startswith("http")):
        return (ROOT / env(ev)) if not Path(env(ev)).is_absolute() else Path(env(ev))
    lp = src.get("local_path")
    return (ROOT / lp) if lp else None


# ── Known territories (extend as regions are added). Honest, not invented. ────
TERRITORIES = {
    "oxford": {
        "name": "Oxford",
        "local_authority": "Oxford",
        "lad_code": "E07000178",
        "postcode_prefixes": ["OX1", "OX2", "OX3", "OX4"],
        "bbox": {"south": 51.69, "north": 51.82, "west": -1.32, "east": -1.17},
    },
}


def run_dir(run_id: str) -> Path:
    d = RUNS_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str))
    return path
