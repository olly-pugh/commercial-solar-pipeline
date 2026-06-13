"""
Agent 12 — Report Generation.

Builds the commercial lead report in three forms:
  * a polished, self-contained **HTML** report (executive-grade, with per-lead
    company links + map links + score visualisations),
  * a **LaTeX → PDF** report (McKinsey/academic structure) compiled with pdflatex
    when available,
  * a **Markdown** narrative (also used for the QA over-claim scan),
plus the lead table (CSV + XLSX) and all charts.

Framing is explicit throughout: COMMERCIAL SOLAR LEAD PRIORITISATION, not a
prediction of guaranteed adoption. Company links are registered-office matches from
the Companies House public register, NOT confirmation of ownership/occupancy.
"""
from __future__ import annotations

import base64
import html as _html
import json
import re
import shutil
import subprocess
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import settings, MODEL_VERSION

# ── House style ──────────────────────────────────────────────────────────────
NAVY = "#0f172a"; INK = "#1e293b"; BLUE = "#2563eb"; GREEN = "#16a34a"
AMBER = "#f59e0b"; RED = "#dc2626"; TEAL = "#0d9488"; SLATE = "#64748b"
PALETTE = ["#2563eb", "#16a34a", "#f59e0b", "#7c3aed", "#0d9488", "#db2777", "#64748b"]
plt.rcParams.update({
    "figure.dpi": 130, "font.size": 9.5, "font.family": "DejaVu Sans",
    "axes.grid": True, "grid.alpha": 0.25, "grid.linestyle": "-",
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": "#94a3b8", "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": SLATE, "ytick.color": SLATE, "axes.titlecolor": NAVY,
    "axes.titleweight": "bold", "figure.facecolor": "white", "savefig.facecolor": "white",
})

_CAT_LABEL = {
    "warehouse_logistics": "Warehouse / Logistics", "cold_storage": "Cold storage",
    "manufacturing_industrial": "Manufacturing / Industrial", "retail_shed": "Retail warehouse",
    "supermarket_food": "Supermarket", "office": "Office", "retail_highstreet": "High-street retail",
    "school": "School", "university_higher_ed": "University", "leisure_gym": "Leisure",
    "public_sector": "Public sector", "mixed_commercial": "Mixed commercial",
    "generic_commercial": "Unclassified commercial",
}
_SUBS = [("technical_score", "Technical"), ("economics_score", "Economics"),
         ("project_scale_score", "Project scale"), ("adoption_propensity_score", "Propensity"),
         ("decision_feasibility_score", "Feasibility"), ("policy_compliance_score", "Policy"),
         ("market_momentum_score", "Momentum")]


# ── Narrative helpers ────────────────────────────────────────────────────────
def _sales_angle(r) -> str:
    cat = _CAT_LABEL.get(r["property_category"], r["property_category"])
    kwp = r["feasible_kwp"]; pay = r.get("payback_central_yr")
    sc = round(r.get("self_consumption_ratio", 0) * 100)
    base = f"{cat}: ~{kwp:,.0f} kWp feasible, ~{sc}% self-consumption"
    if pay and not pd.isna(pay):
        base += f", ~{pay:.0f}-yr indicative payback"
    if r["project_size_category"] in ("large", "very_large"):
        base += ". Flagship-scale — economies of scale + PPA/export potential."
    elif r.get("economics_score", 0) >= 7:
        base += ". Strong bill-savings case."
    return base


def _why_good(r) -> str:
    bits = []
    if r["technical_score"] >= 7: bits.append("technically suitable roof")
    if r["economics_score"] >= 7: bits.append("attractive payback")
    if r["project_scale_score"] >= 7: bits.append("large usable roof / high kWp")
    if r["adoption_propensity_score"] >= 7: bits.append("high-ESG / energy-intensive sector")
    if r["decision_feasibility_score"] >= 7: bits.append("likely single-decision owner-occupier")
    return "; ".join(bits) or "balanced across all criteria"


def _why_risk(r) -> str:
    bits = []
    if r.get("planning_risk") in ("high", "moderate"): bits.append(f"planning/heritage risk ({r['planning_risk']})")
    if r["decision_feasibility_score"] < 5.5: bits.append("possible tenant/split-incentive")
    if r["confidence_score"] < 0.4: bits.append("low data confidence")
    if r.get("category_source") != "epc_confirmed": bits.append("category geometry-inferred")
    if r.get("existing_solar_flag"): bits.append("existing PV — expansion only")
    return "; ".join(bits) or "standard pre-survey checks only"


def _companies(r) -> list:
    try:
        return json.loads(r.get("companies_json") or "[]")
    except Exception:
        return []


# ── Charts ───────────────────────────────────────────────────────────────────
def _charts(leads, funnel, calibration, econ_stats, cdir: Path) -> dict:
    cdir.mkdir(parents=True, exist_ok=True)
    out = {}

    def save(fig, name):
        fig.tight_layout(); fig.savefig(cdir / f"{name}.png"); plt.close(fig)
        out[name] = f"charts/{name}.png"

    # 1. Funnel
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    names = list(funnel.keys()); vals = list(funnel.values())
    ax.barh(range(len(names)), vals, color=BLUE)
    ax.set_yticks(range(len(names))); ax.set_yticklabels(names); ax.invert_yaxis()
    for i, v in enumerate(vals): ax.text(v, i, f" {v:,}", va="center", fontsize=8, color=INK)
    ax.set_title("Candidate funnel"); ax.set_xlabel("Buildings"); save(fig, "funnel")

    # 2. Score histogram
    fig, ax = plt.subplots(figsize=(6.4, 3))
    ax.hist(leads["final_score_10"], bins=22, color=GREEN, edgecolor="white")
    ax.set_xlabel("Final lead score (/10)"); ax.set_ylabel("Buildings")
    ax.set_title("Final-score distribution"); save(fig, "score_hist")

    # 3. Sub-score composition top 12
    t = leads.head(12)
    fig, ax = plt.subplots(figsize=(7.4, 3.6))
    bottom = np.zeros(len(t))
    for (s, lab), col in zip(_SUBS, PALETTE):
        ax.bar(range(len(t)), t[s], bottom=bottom, label=lab, color=col, width=0.78)
        bottom += t[s].values
    ax.set_xticks(range(len(t))); ax.set_xticklabels([f"#{i+1}" for i in range(len(t))])
    ax.set_title("Top 12 leads — weighted sub-score composition")
    ax.legend(fontsize=7, ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.08), frameon=False)
    ax.set_ylabel("Σ contribution"); save(fig, "subscores_top12")

    # 4. Usable area vs kWp
    fig, ax = plt.subplots(figsize=(5.6, 3.2))
    ax.scatter(leads["usable_roof_area_m2"], leads["feasible_kwp"], s=11, alpha=0.4, color=TEAL)
    ax.set_xlabel("Usable roof area (m²)"); ax.set_ylabel("Feasible kWp")
    ax.set_title("Usable roof area vs feasible capacity"); save(fig, "area_vs_kwp")

    # 5. Size breakdown
    fig, ax = plt.subplots(figsize=(5.6, 3))
    vc = leads["project_size_category"].value_counts().reindex(
        ["micro", "small", "medium", "large", "very_large"]).fillna(0)
    ax.bar([s.replace("_", " ") for s in vc.index], vc.values, color="#7c3aed")
    for i, v in enumerate(vc.values): ax.text(i, v, f"{int(v):,}", ha="center", va="bottom", fontsize=8)
    ax.set_title("Project-size categories"); ax.set_ylabel("Buildings"); save(fig, "size_breakdown")

    # 6. Final vs kWp
    fig, ax = plt.subplots(figsize=(5.6, 3.2))
    ax.scatter(leads["feasible_kwp"], leads["final_score_10"], s=11, alpha=0.4, color="#db2777")
    ax.set_xscale("log"); ax.set_xlabel("Feasible kWp (log)"); ax.set_ylabel("Final score")
    ax.set_title("Final score vs feasible capacity"); save(fig, "final_vs_kwp")

    # 7. Savings vs payback
    fig, ax = plt.subplots(figsize=(5.6, 3.2))
    sub = leads[leads["payback_central_yr"].notna()]
    sc = ax.scatter(sub["payback_central_yr"], sub["annual_savings_central_gbp"],
                    s=14, alpha=0.5, c=sub["feasible_kwp"], cmap="viridis")
    ax.set_xlabel("Payback (years)"); ax.set_ylabel("Annual savings £ (central)")
    ax.set_yscale("log"); ax.set_title("Annual savings vs payback (colour = kWp)")
    fig.colorbar(sc, ax=ax, label="kWp"); save(fig, "savings_vs_payback")

    # 8. Category mix (top leads)
    fig, ax = plt.subplots(figsize=(5.6, 3.4))
    top = leads.head(min(100, len(leads)))
    mix = top["property_category"].map(lambda c: _CAT_LABEL.get(c, c)).value_counts().head(7)
    ax.barh(range(len(mix)), mix.values, color=[PALETTE[i % len(PALETTE)] for i in range(len(mix))])
    ax.set_yticks(range(len(mix))); ax.set_yticklabels(mix.index); ax.invert_yaxis()
    for i, v in enumerate(mix.values): ax.text(v, i, f" {v}", va="center", fontsize=8)
    ax.set_title("Top-lead segment mix"); ax.set_xlabel("Leads"); save(fig, "segment_mix")

    # 9. Calibration
    fig, ax = plt.subplots(figsize=(5.6, 3.2))
    buckets = calibration.get("adoption_rate_by_score_bucket") or []
    if buckets:
        ax.bar([b["bucket"] for b in buckets], [b["adoption_rate"] for b in buckets], color=TEAL)
        ax.set_ylabel("Observed PV adoption rate"); ax.set_xlabel("Final-score bucket")
        ax.set_title(f"Calibration ({calibration.get('calibration_status')})")
    else:
        ax.text(0.5, 0.5, f"Calibration status:\n{calibration.get('calibration_status')}\n\n"
                          "No commercial-PV ground truth available —\ncorrelations honestly NOT computed.",
                ha="center", va="center", fontsize=9, color=INK)
        ax.set_axis_off(); ax.set_title("Calibration — honest (inconclusive)")
    save(fig, "calibration")
    return out


# ── Export columns ───────────────────────────────────────────────────────────
_EXPORT_COLS = [
    "rank", "commercial_property_id", "building_id", "uprn", "address", "address_source",
    "postcode", "os_grid_ref", "lat", "lon", "local_authority", "property_category",
    "category_source", "category_confidence",
    "primary_company_name", "primary_company_number", "primary_company_url",
    "primary_company_industry", "n_companies_at_postcode",
    "location_maps_url", "location_streetview_url", "location_osm_url", "ch_postcode_search_url",
    "final_score_10", "technical_score", "economics_score", "project_scale_score",
    "adoption_propensity_score", "decision_feasibility_score", "policy_compliance_score",
    "market_momentum_score", "footprint_area_m2", "total_roof_area_m2", "usable_roof_area_m2",
    "usable_roof_share", "project_size_category", "feasible_kwp", "annual_generation_kwh",
    "estimated_annual_demand_kwh", "self_consumption_ratio",
    "annual_savings_low_gbp", "annual_savings_central_gbp", "annual_savings_high_gbp",
    "payback_low_yr", "payback_central_yr", "payback_high_yr", "carbon_savings_kgco2e_yr",
    "epc_band", "mees_note", "planning_risk", "likely_occupancy", "decision_feasibility_caveat",
    "confidence_score", "data_completeness_score", "proxy_quality_score", "roof_method",
    "existing_solar_flag", "ranking_mode", "model_version", "caveat_tags",
]


def generate(leads, cfg, run_id, funnel, calibration, audit_stages, missing,
             scoring_stats, roof_stats, econ_stats, scale_stats, discovery_stats) -> dict:
    rdir = settings.REPORTS_DIR / run_id
    rdir.mkdir(parents=True, exist_ok=True)
    charts = _charts(leads, funnel, calibration, econ_stats, rdir / "charts")

    # Lead table exports
    export = leads.copy()
    for c in _EXPORT_COLS:
        if c not in export.columns:
            export[c] = None
    export["sales_angle"] = leads.apply(_sales_angle, axis=1)
    export["reason_good_lead"] = leads.apply(_why_good, axis=1)
    export["reason_may_not_convert"] = leads.apply(_why_risk, axis=1)
    cols = _EXPORT_COLS + ["sales_angle", "reason_good_lead", "reason_may_not_convert"]
    table = export[cols]
    csv_path = rdir / f"commercial_leads_{run_id}.csv"
    table.to_csv(csv_path, index=False)
    xlsx_path = rdir / f"commercial_leads_{run_id}.xlsx"
    try:
        table.to_excel(xlsx_path, index=False)
    except Exception:
        xlsx_path = None

    ctx = dict(leads=leads, cfg=cfg, run_id=run_id, funnel=funnel, calibration=calibration,
               charts=charts, audit_stages=audit_stages, missing=missing,
               scoring_stats=scoring_stats, roof_stats=roof_stats, econ_stats=econ_stats,
               scale_stats=scale_stats, discovery_stats=discovery_stats)

    md = _markdown(**ctx)
    md_path = rdir / f"commercial_report_{run_id}.md"; md_path.write_text(md)
    html_path = rdir / f"commercial_report_{run_id}.html"
    html_path.write_text(_html_pro(**ctx))

    pdf_path = None
    try:
        pdf_path = _build_pdf(rdir, **ctx)
    except Exception as e:  # noqa: BLE001 - PDF is best-effort
        (rdir / "latex_build_error.txt").write_text(str(e))

    return {"report_md": str(md_path), "report_html": str(html_path),
            "report_pdf": str(pdf_path) if pdf_path else None,
            "leads_csv": str(csv_path), "leads_xlsx": str(xlsx_path) if xlsx_path else None,
            "charts_dir": str(rdir / "charts"), "report_dir": str(rdir),
            "report_text": md}


def _fmt(x, money=False, n=0):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    if money:
        return f"£{x:,.0f}"
    return f"{x:,.{n}f}" if isinstance(x, (int, float)) else str(x)


# ── KPI helpers shared by HTML + LaTeX ───────────────────────────────────────
def _kpis(leads, funnel, roof_stats, econ_stats, scale_stats):
    mwp = roof_stats.get("total_feasible_kwp", 0) / 1000
    big = (scale_stats.get("largest_projects") or [{}])[0]
    return [
        ("Commercial candidates", f"{funnel.get('Commercial candidates', 0):,}", "buildings screened"),
        ("Feasible PV capacity", f"{mwp:,.1f} MWp", f"{roof_stats.get('total_feasible_kwp', 0):,.0f} kWp total"),
        ("Usable roof area", f"{roof_stats.get('total_usable_roof_area_m2', 0)/1e4:,.1f} ha",
         f"{roof_stats.get('total_usable_roof_area_m2', 0):,.0f} m²"),
        ("Annual bill savings", f"£{econ_stats.get('total_annual_savings_central_gbp', 0)/1e6:,.1f}M",
         "central scenario, indicative"),
        ("Carbon abatement", f"{econ_stats.get('total_carbon_savings_tco2e_yr', 0):,.0f} tCO₂e",
         "per year, indicative"),
        ("Largest single roof", f"{big.get('feasible_kwp', 0):,.0f} kWp",
         f"{big.get('project_size_category', '—')}"),
    ]


# ═════════════════════════════════════════════════════════════════════════════
# Professional HTML
# ═════════════════════════════════════════════════════════════════════════════
def _b64(path: Path) -> str:
    try:
        return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()
    except Exception:
        return ""


def _score_color(v):
    return RED if v < 4 else AMBER if v < 6 else BLUE if v < 7.5 else GREEN


def _bar(label, v):
    v = float(v or 0)
    return (f'<div class="bar"><span class="bl">{label}</span>'
            f'<span class="bt"><i style="width:{v*10:.0f}%;background:{_score_color(v)}"></i></span>'
            f'<span class="bv">{v:.1f}</span></div>')


def _esc(s):
    return _html.escape(str(s)) if s is not None else ""


def _lead_card(r, rdir):
    rank = int(r["rank"]); fin = float(r["final_score_10"])
    cat = _CAT_LABEL.get(r["property_category"], r["property_category"])
    addr = r.get("address") or f"{r.get('os_grid_ref','')} · {r.get('postcode','')}"
    asrc = r.get("address_source") or ""
    companies = _companies(r)
    primary = companies[0] if companies else None

    bars = "".join(_bar(lab, r.get(s, 0)) for s, lab in _SUBS)

    metrics = [
        ("Feasible PV", f"{r['feasible_kwp']:,.0f} kWp"),
        ("Usable roof", f"{r['usable_roof_area_m2']:,.0f} m²"),
        ("Self-consumption", f"{round(r.get('self_consumption_ratio',0)*100)}%"),
        ("Payback (central)", _fmt(r.get('payback_central_yr'), n=1) + " yr"),
        ("Savings/yr", _fmt(r.get('annual_savings_central_gbp'), money=True)),
        ("Carbon/yr", f"{r.get('carbon_savings_kgco2e_yr',0)/1000:,.1f} tCO₂e"),
    ]
    mhtml = "".join(f'<div class="m"><b>{v}</b><span>{k}</span></div>' for k, v in metrics)

    # Company block
    if primary:
        others = "".join(
            f'<a href="{c["url"]}" target="_blank" rel="noopener" class="cchip">{_esc(c["name"])}'
            f'{" · "+_esc(c["industry"]) if c.get("industry") else ""}</a>'
            for c in companies[1:5])
        comp = (f'<div class="company"><div class="clabel">Companies registered at this postcode '
                f'<span class="prov">Companies House · registered office, not confirmed owner</span></div>'
                f'<a class="cprimary" href="{primary["url"]}" target="_blank" rel="noopener">'
                f'🏢 {_esc(primary["name"])}</a>'
                f'<span class="cmeta">{_esc(primary.get("industry") or "—")} · '
                f'{_esc(primary.get("status") or "")} · inc. {_esc(primary.get("incorporated") or "?")}</span>'
                f'{("<div class=others>"+others+"</div>") if others else ""}</div>')
    else:
        sl = r.get("ch_postcode_search_url")
        comp = (f'<div class="company"><div class="clabel">No active company found at this postcode'
                f'{" · " if sl else ""}'
                f'{f"<a href=\"{sl}\" target=_blank rel=noopener>search Companies House</a>" if sl else ""}</div></div>')

    maps = r.get("location_maps_url"); sv = r.get("location_streetview_url"); osm = r.get("location_osm_url")
    loc = '<div class="loc">'
    if maps: loc += f'<a href="{maps}" target="_blank" rel="noopener">📍 Map</a>'
    if sv: loc += f'<a href="{sv}" target="_blank" rel="noopener">🛣 Street View</a>'
    if osm: loc += f'<a href="{osm}" target="_blank" rel="noopener">🗺 OSM</a>'
    loc += f'<span class="grid">{_esc(r.get("os_grid_ref",""))}</span></div>'

    badge_c = _score_color(fin)
    return f"""
    <div class="card">
      <div class="chead">
        <div class="rankbadge" style="background:{badge_c}">#{rank}</div>
        <div class="ctitle">
          <div class="addr">{_esc(addr)}</div>
          <div class="sub"><span class="catchip">{_esc(cat)}</span>
            <span class="src">{_esc(asrc.replace('_',' '))}</span></div>
        </div>
        <div class="finbox" style="border-color:{badge_c}">
          <b style="color:{badge_c}">{fin:.2f}</b><span>/10</span></div>
      </div>
      {comp}
      {loc}
      <div class="metrics">{mhtml}</div>
      <div class="bars">{bars}</div>
      <div class="notes">
        <div class="angle">🎯 {_esc(_sales_angle(r))}</div>
        <div class="good">✅ Why it ranks: {_esc(_why_good(r))}</div>
        <div class="risk">⚠ Watch-outs: {_esc(_why_risk(r))}</div>
      </div>
    </div>"""


def _html_pro(leads, cfg, run_id, funnel, calibration, charts, audit_stages, missing,
              scoring_stats, roof_stats, econ_stats, scale_stats, discovery_stats) -> str:
    rdir = settings.REPORTS_DIR / run_id
    top = leads[leads["included_in_top_leads"]] if "included_in_top_leads" in leads else leads
    n_show = min(int(cfg.get("leads", 100)), len(top))
    cards = "".join(_lead_card(r, rdir) for _, r in top.head(12).iterrows())

    kpis = "".join(
        f'<div class="kpi"><b>{v}</b><span class="kl">{k}</span><span class="kn">{n}</span></div>'
        for k, v, n in _kpis(leads, funnel, roof_stats, econ_stats, scale_stats))

    def chart_card(key, title, note=""):
        if key not in charts: return ""
        return (f'<figure class="chart"><img src="{_b64(rdir / charts[key])}" '
                f'alt="{title}"><figcaption><b>{title}</b>{(" — "+note) if note else ""}</figcaption></figure>')

    charts_html = "".join([
        chart_card("funnel", "Candidate funnel", "screening from raw buildings to ranked leads"),
        chart_card("segment_mix", "Top-lead segment mix"),
        chart_card("subscores_top12", "Sub-score composition (top 12)", "how each lead earns its score"),
        chart_card("size_breakdown", "Project-size distribution"),
        chart_card("area_vs_kwp", "Usable roof area → capacity"),
        chart_card("final_vs_kwp", "Score vs capacity", "scale rewarded, not dominant"),
        chart_card("savings_vs_payback", "Savings vs payback"),
        chart_card("score_hist", "Final-score distribution"),
        chart_card("calibration", "Calibration"),
    ])

    # Full table rows (rank 13..N)
    rows = []
    for _, r in top.head(n_show).iterrows():
        comp = r.get("primary_company_name")
        comp_cell = (f'<a href="{r.get("primary_company_url")}" target="_blank" rel="noopener">{_esc(comp)}</a>'
                     if comp and not pd.isna(comp) else '<span class="muted">—</span>')
        maps = r.get("location_maps_url")
        map_cell = f'<a href="{maps}" target="_blank" rel="noopener">📍</a>' if maps else ''
        rows.append(
            f'<tr><td class="r">{int(r["rank"])}</td><td>{_esc(r.get("address"))} {map_cell}</td>'
            f'<td>{_esc(_CAT_LABEL.get(r["property_category"], r["property_category"]))}</td>'
            f'<td>{comp_cell}</td>'
            f'<td class="n"><b style="color:{_score_color(r["final_score_10"])}">{r["final_score_10"]:.2f}</b></td>'
            f'<td class="n">{r["feasible_kwp"]:,.0f}</td>'
            f'<td class="n">{r["usable_roof_area_m2"]:,.0f}</td>'
            f'<td class="n">{_fmt(r.get("annual_savings_central_gbp"), money=True)}</td>'
            f'<td class="n">{_fmt(r.get("payback_central_yr"), n=1)}y</td>'
            f'<td class="n">{r["technical_score"]:.1f}</td><td class="n">{r["economics_score"]:.1f}</td>'
            f'<td class="n">{r["project_scale_score"]:.1f}</td>'
            f'<td class="n">{r["confidence_score"]:.2f}</td></tr>')
    table = "".join(rows)

    # Methodology weights
    wrows = "".join(f"<tr><td>{_esc(k)}</td><td class='n'>{v}</td></tr>"
                    for k, v in scoring_stats["effective_weights"].items())

    cal = calibration
    cal_badge_c = GREEN if str(cal.get("calibration_status", "")).startswith("PASS") else AMBER
    missing_rows = "".join(
        f"<tr><td>{_esc(m['source'])}</td><td>{_esc(m['required'])}</td>"
        f"<td>{_esc(m['impact'])}</td><td>{_esc(m['degraded_behaviour'])}</td></tr>"
        for m in missing) or "<tr><td colspan=4>All declared sources available.</td></tr>"

    audit_rows = "".join(
        f"<tr><td>{_esc(s['stage_name'])}</td><td>{_esc(s['status'])}</td>"
        f"<td>{_esc(s.get('fresh_or_cached',''))}</td><td class='n'>{_esc(s.get('runtime_s','—'))}</td>"
        f"<td class='n'>{len(s.get('warnings',[]))}</td></tr>" for s in audit_stages)

    qa_note = "deterministic QA: see red_team_report.md"
    nav = ("<a href='#summary'>Summary</a><a href='#leads'>Top leads</a>"
           "<a href='#charts'>Analytics</a><a href='#method'>Methodology</a>"
           "<a href='#calibration'>Calibration</a><a href='#data'>Data quality</a>")

    css = _PRO_CSS
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Commercial Solar Lead Prioritisation — {_esc(cfg['area'])} · {run_id}</title>
<style>{css}</style></head><body>
<header class="hero">
  <div class="wrap">
    <div class="eyebrow">OZ COMMERCIAL SOLAR · LEAD PRIORITISATION INTELLIGENCE</div>
    <h1>Commercial Rooftop Solar — {_esc(cfg['area'])}</h1>
    <p class="lede">A deterministic, evidence-graded prioritisation of the highest-value
    non-domestic rooftop solar opportunities across {_esc(cfg['area'])}, with verifiable
    locations and Companies House links for every top lead.</p>
    <div class="chips">
      <span class="chip">Run {run_id}</span>
      <span class="chip">Model {MODEL_VERSION}</span>
      <span class="chip">Mode: {_esc(cfg['ranking_mode'])}</span>
      <span class="chip">Scenario: {_esc(cfg['scenario'])}</span>
      <span class="chip badge" style="background:{cal_badge_c}">Calibration: {_esc(cal.get('calibration_status'))}</span>
    </div>
  </div>
</header>
<nav class="topnav"><div class="wrap">{nav}</div></nav>
<div class="wrap main">
  <div class="disclaimer">
    <b>Lead prioritisation, not a prediction of adoption.</b> Every figure is indicative and
    requires a site survey, bill validation, structural survey and planning/grid confirmation
    before commitment. Company links are <b>registered-office</b> matches from the Companies
    House public register — they indicate a company registered at the postcode, <b>not</b>
    confirmed ownership or occupancy. No personal contact data is collected.
  </div>

  <section id="summary">
    <h2>Executive summary</h2>
    <p class="section-lede">Across {funnel.get('Commercial candidates', 0):,} screened commercial
    buildings in {_esc(cfg['area'])}, the pipeline identifies
    <b>{roof_stats.get('total_feasible_kwp', 0)/1000:,.1f} MWp</b> of technically feasible rooftop PV
    on <b>{roof_stats.get('total_usable_roof_area_m2', 0)/1e4:,.1f} hectares</b> of usable roof —
    an indicative <b>£{econ_stats.get('total_annual_savings_central_gbp', 0)/1e6:,.1f}M/yr</b> in energy-bill
    savings and <b>{econ_stats.get('total_carbon_savings_tco2e_yr', 0):,.0f} tCO₂e/yr</b> of abatement.
    The {n_show} highest-priority sites below are dominated by
    {_esc(", ".join(f"{k} ({v})" for k,v in top['property_category'].map(lambda c:_CAT_LABEL.get(c,c)).value_counts().head(3).items()))}.</p>
    <div class="kpis">{kpis}</div>
  </section>

  <section id="leads">
    <h2>Top 12 priority leads</h2>
    <p class="section-lede">Each card shows the verified location, the company registered at that
    postcode (with a direct Companies House link), the full seven-factor score breakdown, and the
    commercial angle. The full ranked table of {n_show} leads follows.</p>
    <div class="cards">{cards}</div>

    <h3>Full ranked lead table (top {n_show})</h3>
    <div class="tablewrap"><table class="leadtable">
      <thead><tr><th>#</th><th>Address</th><th>Category</th><th>Company (reg. office)</th>
        <th>Score</th><th>kWp</th><th>Usable m²</th><th>Savings/yr</th><th>Payback</th>
        <th>Tech</th><th>Econ</th><th>Scale</th><th>Conf.</th></tr></thead>
      <tbody>{table}</tbody></table></div>
  </section>

  <section id="charts">
    <h2>Analytics</h2>
    <div class="charts">{charts_html}</div>
  </section>

  <section id="method">
    <h2>Methodology</h2>
    <p>The final score (0–10) is a transparent weighted blend of seven deterministic sub-scores.
    AI is used only for narrative — never to produce a number. The score formula:</p>
    <pre class="code">{_esc(scoring_stats['formula'])}</pre>
    <div class="cols">
      <div>
        <h3>Effective weights — mode “{_esc(cfg['ranking_mode'])}”</h3>
        <table class="mini"><thead><tr><th>Sub-score</th><th>Weight</th></tr></thead><tbody>{wrows}</tbody></table>
      </div>
      <div>
        <h3>Sub-scores &amp; evidence strength</h3>
        <table class="mini"><thead><tr><th>Sub-score</th><th>Measures</th><th>Ev.</th></tr></thead><tbody>
          <tr><td>Technical</td><td>Roof suitability: orientation, pitch, usable-area adequacy, shading, irradiance</td><td>B</td></tr>
          <tr><td>Economics</td><td>Bill-savings: demand, self-consumption, CAPEX, payback</td><td>B</td></tr>
          <tr><td>Project scale</td><td>Opportunity <i>size</i> — saturating in feasible kWp (not raw area)</td><td>A/B</td></tr>
          <tr><td>Propensity</td><td>Sector ESG + energy-cost salience</td><td>C</td></tr>
          <tr><td>Feasibility</td><td>Owner-occupier vs tenant / split-incentive</td><td>A/C</td></tr>
          <tr><td>Policy</td><td>EPC/MEES + ESG + heritage/planning risk</td><td>B/C</td></tr>
          <tr><td>Momentum</td><td>Local installer density / peer PV (small weight)</td><td>C/D</td></tr>
        </tbody></table>
        <p class="fine">Evidence grades: A = direct commercial; B = technical/economic; C = transferable
        residential; D = weak proxy. <b>Project scale is deliberately separate from technical
        suitability</b>: technical asks “is the roof suitable?” (usable-area component saturates ~150 m²);
        project scale asks “is the opportunity big enough to prioritise?” (saturates near ~800 kWp), with
        penalties for low usable-share, low confidence, shading, existing PV and weak economics.</p>
      </div>
    </div>
  </section>

  <section id="calibration">
    <h2>Calibration — reported honestly</h2>
    <p>Status <span class="pill" style="background:{cal_badge_c}">{_esc(cal.get('calibration_status'))}</span>
    · sample n = {_esc(cal.get('calibration_sample_size'))}. {_esc(cal.get('notes',''))}</p>
    <p class="fine">Where no commercial-PV ground truth exists, score-vs-adoption correlations are
    <b>not computed and never fabricated</b>. Supplying <code>data/commercial/ground_truth_pv.csv</code>
    activates Pearson/Spearman correlation of final score, project-scale, usable area and feasible kWp
    against observed adoption.</p>
  </section>

  <section id="data">
    <h2>Data quality, lineage &amp; missing-data register</h2>
    <h3>Stage audit</h3>
    <div class="tablewrap"><table class="mini"><thead><tr><th>Stage</th><th>Status</th><th>Fresh/cached</th>
      <th>Runtime s</th><th>Warnings</th></tr></thead><tbody>{audit_rows}</tbody></table></div>
    <h3>Missing-data register</h3>
    <div class="tablewrap"><table class="mini"><thead><tr><th>Source</th><th>Required</th><th>Impact</th>
      <th>Degraded behaviour</th></tr></thead><tbody>{missing_rows}</tbody></table></div>
    <p class="fine">{qa_note}. Full per-stage inputs/outputs in <code>agent_audit_log.json</code>.</p>
  </section>

  <footer class="foot">
    <div>Generated by the OZ commercial solar lead pipeline · model {MODEL_VERSION} · run {run_id}</div>
    <div class="fine">Indicative commercial lead prioritisation only. Lawful open/registry data;
    no personal contact scraping. Verify every lead before approach.</div>
  </footer>
</div>
</body></html>"""


_PRO_CSS = """
*{box-sizing:border-box} html{scroll-behavior:smooth}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Arial,sans-serif;
  color:#1e293b;background:#f1f5f9;line-height:1.55;font-size:15px}
.wrap{max-width:1180px;margin:0 auto;padding:0 22px}
.hero{background:linear-gradient(135deg,#0f172a 0%,#1e3a8a 60%,#1d4ed8 100%);color:#fff;padding:54px 0 40px}
.eyebrow{letter-spacing:.16em;font-size:12px;font-weight:700;color:#93c5fd;margin-bottom:12px}
.hero h1{font-size:40px;margin:0 0 12px;font-weight:800;letter-spacing:-.5px}
.lede{font-size:18px;max-width:760px;color:#dbeafe;margin:0 0 18px}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.18);padding:5px 12px;
  border-radius:999px;font-size:12.5px;font-weight:600}
.chip.badge{color:#0f172a}
.topnav{background:#0b1222;position:sticky;top:0;z-index:20;box-shadow:0 2px 10px rgba(0,0,0,.15)}
.topnav .wrap{display:flex;gap:6px;flex-wrap:wrap}
.topnav a{color:#cbd5e1;text-decoration:none;padding:13px 14px;font-size:13.5px;font-weight:600}
.topnav a:hover{color:#fff;background:rgba(255,255,255,.06)}
.main{padding:8px 22px 60px}
.disclaimer{background:#fef9c3;border:1px solid #fde047;border-left:5px solid #f59e0b;
  padding:14px 18px;border-radius:10px;margin:24px 0;font-size:13.5px;color:#713f12}
section{margin:40px 0;scroll-margin-top:60px}
h2{font-size:27px;font-weight:800;color:#0f172a;margin:0 0 6px;letter-spacing:-.3px;
  border-bottom:3px solid #e2e8f0;padding-bottom:8px}
h3{font-size:18px;color:#1e293b;margin:24px 0 10px}
.section-lede{font-size:16px;color:#475569;max-width:900px}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:20px}
.kpi{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:20px 22px;
  box-shadow:0 1px 3px rgba(15,23,42,.06)}
.kpi b{display:block;font-size:30px;font-weight:800;color:#1d4ed8;line-height:1.1}
.kl{display:block;font-weight:700;color:#334155;margin-top:6px;font-size:14px}
.kn{display:block;color:#94a3b8;font-size:12.5px;margin-top:2px}
.cards{display:grid;grid-template-columns:repeat(2,1fr);gap:18px;margin-top:18px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:18px;
  box-shadow:0 2px 8px rgba(15,23,42,.07);display:flex;flex-direction:column;gap:12px}
.chead{display:flex;align-items:flex-start;gap:12px}
.rankbadge{color:#fff;font-weight:800;font-size:15px;min-width:42px;height:42px;border-radius:10px;
  display:flex;align-items:center;justify-content:center}
.ctitle{flex:1;min-width:0}
.addr{font-weight:700;font-size:15.5px;color:#0f172a}
.sub{margin-top:4px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.catchip{background:#eff6ff;color:#1d4ed8;font-weight:700;font-size:11.5px;padding:2px 9px;border-radius:999px}
.src{color:#94a3b8;font-size:11px}
.finbox{border:2px solid;border-radius:12px;padding:6px 12px;text-align:center;min-width:64px}
.finbox b{font-size:24px;font-weight:800;display:block;line-height:1}
.finbox span{font-size:11px;color:#94a3b8}
.company{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:10px 12px}
.clabel{font-size:11.5px;color:#64748b;font-weight:600;margin-bottom:6px}
.prov{color:#94a3b8;font-weight:500;font-style:italic}
.cprimary{display:inline-block;background:#1d4ed8;color:#fff;text-decoration:none;font-weight:700;
  font-size:13.5px;padding:6px 12px;border-radius:8px}
.cprimary:hover{background:#1e40af}
.cmeta{display:block;color:#64748b;font-size:12px;margin-top:5px}
.others{margin-top:8px;display:flex;flex-wrap:wrap;gap:6px}
.cchip{background:#fff;border:1px solid #cbd5e1;color:#334155;text-decoration:none;font-size:11.5px;
  padding:3px 8px;border-radius:7px}
.cchip:hover{border-color:#1d4ed8;color:#1d4ed8}
.loc{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.loc a{background:#ecfdf5;color:#047857;text-decoration:none;font-weight:600;font-size:12.5px;
  padding:5px 11px;border-radius:8px;border:1px solid #a7f3d0}
.loc a:hover{background:#d1fae5}
.loc .grid{color:#94a3b8;font-size:11.5px;margin-left:auto}
.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.m{background:#f8fafc;border:1px solid #eef2f7;border-radius:9px;padding:8px 10px;text-align:center}
.m b{display:block;font-size:15px;color:#0f172a;font-weight:800}
.m span{font-size:11px;color:#64748b}
.bars{display:flex;flex-direction:column;gap:4px}
.bar{display:flex;align-items:center;gap:8px;font-size:11.5px}
.bl{width:78px;color:#475569;font-weight:600}
.bt{flex:1;height:8px;background:#eef2f7;border-radius:999px;overflow:hidden}
.bt i{display:block;height:100%;border-radius:999px}
.bv{width:26px;text-align:right;color:#334155;font-weight:700}
.notes{font-size:12.5px;display:flex;flex-direction:column;gap:3px;border-top:1px dashed #e2e8f0;padding-top:10px}
.angle{color:#1e3a8a;font-weight:600}.good{color:#15803d}.risk{color:#b45309}
.tablewrap{overflow-x:auto;border:1px solid #e2e8f0;border-radius:12px;margin-top:12px}
table{border-collapse:collapse;width:100%;font-size:13px;background:#fff}
.leadtable th,.leadtable td{padding:9px 11px;border-bottom:1px solid #eef2f7;text-align:left;white-space:nowrap}
.leadtable thead th{background:#0f172a;color:#fff;font-size:12px;position:sticky;top:0}
.leadtable tbody tr:hover{background:#f8fafc}
.leadtable .n,.mini .n{text-align:right;font-variant-numeric:tabular-nums}
.leadtable .r{color:#94a3b8;font-weight:700}
.leadtable a{color:#1d4ed8;text-decoration:none}.leadtable a:hover{text-decoration:underline}
.muted{color:#cbd5e1}
.charts{display:grid;grid-template-columns:repeat(2,1fr);gap:18px;margin-top:16px}
.chart{margin:0;background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:14px;
  box-shadow:0 1px 3px rgba(15,23,42,.05)}
.chart img{width:100%;height:auto;border-radius:6px}
.chart figcaption{font-size:12.5px;color:#475569;margin-top:8px;text-align:center}
.cols{display:grid;grid-template-columns:1fr 1.3fr;gap:24px;margin-top:14px}
.mini{font-size:12.5px}.mini th{background:#f1f5f9;text-align:left;padding:8px 10px;border-bottom:2px solid #e2e8f0}
.mini td{padding:7px 10px;border-bottom:1px solid #eef2f7;vertical-align:top}
.code,pre.code{background:#0f172a;color:#e2e8f0;padding:14px 16px;border-radius:10px;font-size:12.5px;
  overflow-x:auto;white-space:pre-wrap;font-family:ui-monospace,Menlo,monospace}
.fine{font-size:12.5px;color:#64748b}
.pill{color:#0f172a;font-weight:700;padding:2px 10px;border-radius:999px;font-size:12.5px}
code{background:#f1f5f9;padding:1px 6px;border-radius:5px;font-size:12.5px}
.foot{margin-top:50px;padding-top:20px;border-top:1px solid #e2e8f0;color:#64748b;font-size:13px}
@media(max-width:820px){.kpis,.cards,.charts,.cols{grid-template-columns:1fr}.hero h1{font-size:30px}}
"""


# ═════════════════════════════════════════════════════════════════════════════
# Markdown (also feeds QA over-claim scan)
# ═════════════════════════════════════════════════════════════════════════════
def _markdown(leads, cfg, run_id, funnel, calibration, charts, audit_stages, missing,
              scoring_stats, roof_stats, econ_stats, scale_stats, discovery_stats) -> str:
    top = leads[leads["included_in_top_leads"]] if "included_in_top_leads" in leads else leads
    L = [f"# Commercial Solar Lead Prioritisation — {cfg['area']}",
         f"_Run `{run_id}` · model `{MODEL_VERSION}` · ranking mode **{cfg['ranking_mode']}** · "
         f"price scenario **{cfg['scenario']}**_\n",
         "> **This is commercial solar LEAD PRIORITISATION, not a prediction of adoption.** "
         "All figures are indicative and require a site survey, bill validation, structural survey "
         "and planning/grid confirmation. Company links are registered-office matches from the "
         "Companies House public register — not confirmation of ownership or occupancy.\n",
         "## 1. Executive summary\n",
         f"- **Area searched:** {cfg['area']} (LA: {cfg.get('local_authority') or '—'})",
         f"- **Commercial candidates:** {funnel.get('Commercial candidates', 0):,}",
         f"- **Total usable commercial roof:** {roof_stats.get('total_usable_roof_area_m2', 0):,.0f} m²",
         f"- **Total feasible PV capacity:** {roof_stats.get('total_feasible_kwp', 0):,.0f} kWp "
         f"(~{roof_stats.get('total_feasible_kwp', 0)/1000:,.1f} MWp)",
         f"- **Indicative annual savings (central):** "
         f"£{econ_stats.get('total_annual_savings_central_gbp', 0):,.0f}",
         f"- **Indicative carbon abatement:** {econ_stats.get('total_carbon_savings_tco2e_yr', 0):,.0f} tCO₂e/yr",
         f"- **Calibration:** {calibration.get('calibration_status')} — {calibration.get('notes','')[:140]}\n",
         f"![funnel]({charts['funnel']})\n",
         f"## 2. Top {min(cfg['leads'], len(top))} ranked commercial leads\n",
         "| # | Address | Company (registered office) | Category | Final | kWp | Usable m² | "
         "Savings/yr | Payback | Conf |",
         "|--:|---|---|---|--:|--:|--:|--:|--:|--:|"]
    for _, r in top.head(cfg["leads"]).iterrows():
        addr = str(r.get("address") or r.get("os_grid_ref") or "")[:46]
        comp = str(r.get("primary_company_name") or "—")[:34]
        L.append(f"| {int(r['rank'])} | {addr} | {comp} | "
                 f"{_CAT_LABEL.get(r['property_category'], r['property_category'])} | "
                 f"{r['final_score_10']:.2f} | {_fmt(r['feasible_kwp'])} | {_fmt(r['usable_roof_area_m2'])} | "
                 f"{_fmt(r.get('annual_savings_central_gbp'), money=True)} | "
                 f"{_fmt(r.get('payback_central_yr'), n=1)}y | {r['confidence_score']:.2f} |")
    L += ["", f"![subscores]({charts['subscores_top12']})\n",
          "Per-lead company links, exact map/Street-View links, sales angle and "
          "'why it might not convert' notes are in the HTML report and the CSV/XLSX export.\n",
          "## 3. Methodology\n",
          f"**Final score formula** (out of 10):\n\n```\n{scoring_stats['formula']}\n```\n",
          f"Effective weights for mode **{cfg['ranking_mode']}**: "
          + ", ".join(f"{k}={v}" for k, v in scoring_stats["effective_weights"].items()) + "\n",
          f"![final vs kwp]({charts['final_vs_kwp']})\n![area vs kwp]({charts['area_vs_kwp']})\n",
          "## 4. Calibration\n",
          f"**Status: `{calibration.get('calibration_status')}`** · "
          f"sample n = {calibration.get('calibration_sample_size')}\n",
          f"{calibration.get('notes','')}\n"]
    if not calibration.get("score_vs_adoption"):
        L.append("_No correlations computed — calibration ground truth not available. This is reported "
                 "honestly; correlations are never fabricated._\n")
    L += [f"![calibration]({charts['calibration']})\n",
          "## 5. Missing-data register & lineage\n"]
    if missing:
        L += ["| Source | Required | Impact | Degraded behaviour |", "|---|---|---|---|"]
        for m in missing:
            L.append(f"| {m['source']} | {m['required']} | {m['impact'][:80]} | {m['degraded_behaviour'][:80]} |")
    else:
        L.append("All declared sources were available.")
    L.append("\n_Generated by the OZ commercial solar lead pipeline. Indicative lead prioritisation only._")
    return "\n".join(L)


# ═════════════════════════════════════════════════════════════════════════════
# LaTeX → PDF
# ═════════════════════════════════════════════════════════════════════════════
def _tex_escape(s) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return "--"
    s = str(s)
    repl = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
            "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
            "^": r"\textasciicircum{}", "£": r"\pounds{}", "²": r"\textsuperscript{2}",
            "₂": r"\textsubscript{2}", "–": "--", "—": "---", "≈": r"$\approx$",
            "·": r"$\cdot$", "→": r"$\rightarrow$", "≥": r"$\geq$", "“": "``", "”": "''",
            "’": "'", "‘": "'", "✅": "", "⚠": "", "🎯": "", "🏢": "", "📍": "", "₂e": "2e"}
    for k, v in repl.items():
        s = s.replace(k, v)
    return s


def _latex(leads, cfg, run_id, funnel, calibration, charts, audit_stages, missing,
           scoring_stats, roof_stats, econ_stats, scale_stats, discovery_stats) -> str:
    top = leads[leads["included_in_top_leads"]] if "included_in_top_leads" in leads else leads
    e = _tex_escape
    kpis = _kpis(leads, funnel, roof_stats, econ_stats, scale_stats)

    kpi_rows = " \\\\\n".join(
        f"\\textbf{{{e(v)}}} & {e(k)}: {e(n)}" for k, v, n in kpis)

    # Top 15 leads table (longtable)
    lead_lines = []
    for _, r in top.head(15).iterrows():
        comp = e(str(r.get("primary_company_name") or "--")[:26])
        addr = e(str(r.get("address") or r.get("os_grid_ref") or "")[:30])
        lead_lines.append(
            f"{int(r['rank'])} & {addr} & {comp} & "
            f"{r['final_score_10']:.2f} & {r['feasible_kwp']:,.0f} & "
            f"{e(_fmt(r.get('annual_savings_central_gbp'), money=True))} & "
            f"{e(_fmt(r.get('payback_central_yr'), n=1))} \\\\")
    lead_table = "\n".join(lead_lines)

    wrows = " \\\\\n".join(f"{e(k)} & {v}" for k, v in scoring_stats["effective_weights"].items())

    # Company highlight callouts for top 6 with a company
    callouts = []
    for _, r in top.head(8).iterrows():
        if r.get("primary_company_name") and not pd.isna(r.get("primary_company_name")):
            callouts.append(
                f"\\item \\textbf{{\\#{int(r['rank'])} {e(r.get('primary_company_name'))}}} "
                f"({e(r.get('primary_company_industry') or 'sector n/a')}) --- "
                f"{e(r.get('address'))}; \\emph{{{r['feasible_kwp']:,.0f} kWp}}, "
                f"payback {e(_fmt(r.get('payback_central_yr'), n=1))} yr. "
                f"\\href{{{r.get('primary_company_url')}}}{{Companies House}}.")
        if len(callouts) >= 6:
            break
    callout_block = "\n".join(callouts) or "\\item (No company links available in this run.)"

    cal = calibration
    miss_rows = " \\\\\n".join(
        f"{e(m['source'])} & {e(str(m['required']))} & {e(m['impact'][:60])}" for m in missing) \
        or "All declared sources & -- & available"

    fig = lambda key: (settings.REPORTS_DIR / run_id / charts[key]).as_posix() if key in charts else ""

    seg = ", ".join(f"{e(_CAT_LABEL.get(k,k))} ({v})"
                    for k, v in top['property_category'].value_counts().head(3).items())

    return r"""\documentclass[11pt]{article}
\usepackage[a4paper,margin=2.2cm]{geometry}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage{textcomp}
\usepackage{lmodern}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{longtable}
\usepackage{array}
\usepackage{xcolor}
\usepackage{titlesec}
\usepackage{enumitem}
\usepackage[colorlinks=true,linkcolor=myblue,urlcolor=myblue]{hyperref}
\usepackage{fancyhdr}
\definecolor{myblue}{HTML}{1D4ED8}
\definecolor{navy}{HTML}{0F172A}
\definecolor{slate}{HTML}{475569}
\definecolor{lightgray}{HTML}{F1F5F9}
\titleformat{\section}{\Large\bfseries\color{navy}}{\thesection}{0.6em}{}
\titleformat{\subsection}{\large\bfseries\color{myblue}}{\thesubsection}{0.5em}{}
\pagestyle{fancy}\fancyhf{}
\lhead{\small\color{slate}Commercial Solar Lead Prioritisation --- """ + e(cfg['area']) + r"""}
\rhead{\small\color{slate}""" + e(run_id) + r"""}
\cfoot{\small\color{slate}\thepage}
\renewcommand{\headrulewidth}{0.4pt}
\setlength{\parskip}{0.6em}\setlength{\parindent}{0pt}

\begin{document}
\begin{titlepage}
\centering
\vspace*{1.5cm}
{\footnotesize\color{myblue}\textbf{OZ COMMERCIAL SOLAR · LEAD PRIORITISATION INTELLIGENCE}}\\[1.2cm]
{\Huge\bfseries\color{navy} Commercial Rooftop Solar}\\[0.3cm]
{\Huge\bfseries\color{navy} """ + e(cfg['area']) + r"""}\\[1cm]
{\large\color{slate} A deterministic, evidence-graded prioritisation of the highest-value\\
non-domestic rooftop solar opportunities, with verifiable locations\\
and Companies House links for every priority lead.}\\[1.5cm]
\begin{tabular}{rl}
\toprule
Run identifier & \texttt{""" + e(run_id) + r"""} \\
Model version & \texttt{""" + e(MODEL_VERSION) + r"""} \\
Ranking mode & """ + e(cfg['ranking_mode']) + r""" \\
Price scenario & """ + e(cfg['scenario']) + r""" \\
Calibration & """ + e(cal.get('calibration_status')) + r""" \\
\bottomrule
\end{tabular}\\[1.5cm]
\colorbox{lightgray}{\parbox{0.86\textwidth}{\footnotesize\color{slate}
\textbf{Lead prioritisation, not a prediction of adoption.} Every figure is indicative and
requires a site survey, bill validation, structural survey and planning/grid confirmation.
Company links are \textbf{registered-office} matches from the Companies House public register
--- not confirmation of ownership or occupancy. Lawful open/registry data only; no personal
contact data collected.}}
\vfill
\end{titlepage}

\section{Executive summary}
Across \textbf{""" + f"{funnel.get('Commercial candidates', 0):,}" + r"""} screened commercial buildings in
""" + e(cfg['area']) + r""", the pipeline identifies \textbf{""" + f"{roof_stats.get('total_feasible_kwp',0)/1000:,.1f}" + r""" MWp}
of technically feasible rooftop PV on \textbf{""" + f"{roof_stats.get('total_usable_roof_area_m2',0)/1e4:,.1f}" + r""" hectares}
of usable roof --- an indicative \textbf{\pounds""" + f"{econ_stats.get('total_annual_savings_central_gbp',0)/1e6:,.1f}" + r"""M/yr}
in energy-bill savings and \textbf{""" + f"{econ_stats.get('total_carbon_savings_tco2e_yr',0):,.0f}" + r""" tCO\textsubscript{2}e/yr}
of abatement. The priority leads are dominated by """ + seg + r""".

\vspace{0.4em}
\begin{center}\renewcommand{\arraystretch}{1.5}
\begin{tabular}{>{\raggedleft}p{3.2cm} p{9cm}}
\toprule
""" + kpi_rows + r""" \\
\bottomrule
\end{tabular}\end{center}

\begin{figure}[h]\centering\includegraphics[width=0.78\textwidth]{""" + fig("funnel") + r"""}
\caption{Candidate funnel: from raw buildings to ranked commercial leads.}\end{figure}

\section{Priority leads}
\subsection{Named-company highlights}
\begin{itemize}[leftmargin=1.4em]
""" + callout_block + r"""
\end{itemize}

\subsection{Top 15 ranked leads}
\begin{center}\footnotesize
\begin{longtable}{r p{3.1cm} p{3.0cm} r r r r}
\toprule
\textbf{\#} & \textbf{Address} & \textbf{Company (reg. office)} & \textbf{Score} &
\textbf{kWp} & \textbf{Savings/yr} & \textbf{Payback} \\
\midrule
\endhead
""" + lead_table + r"""
\bottomrule
\end{longtable}\end{center}

\begin{figure}[h]\centering\includegraphics[width=0.95\textwidth]{""" + fig("subscores_top12") + r"""}
\caption{How the top leads earn their score --- weighted sub-score composition.}\end{figure}

\section{Methodology}
The final score (0--10) is a transparent weighted blend of seven deterministic sub-scores.
AI is used only for narrative --- never to produce a number.

\begin{center}\small
\begin{tabular}{l r}
\toprule
\textbf{Sub-score} & \textbf{Weight (mode: """ + e(cfg['ranking_mode']) + r""")} \\
\midrule
""" + wrows + r""" \\
\bottomrule
\end{tabular}\end{center}

\textbf{Project scale is deliberately separate from technical suitability.} Technical asks
``is the roof suitable?'' (usable-area component saturates near 150\,m\textsuperscript{2});
project scale asks ``is the opportunity big enough to prioritise?'' (saturates near 800\,kWp),
with penalties for low usable-share, low confidence, shading, existing PV and weak economics.

\begin{figure}[h]\centering
\includegraphics[width=0.49\textwidth]{""" + fig("final_vs_kwp") + r"""}\hfill
\includegraphics[width=0.49\textwidth]{""" + fig("savings_vs_payback") + r"""}
\caption{Left: scale is rewarded but never dominant. Right: savings versus payback.}\end{figure}

\section{Calibration --- reported honestly}
Status: \textbf{""" + e(cal.get('calibration_status')) + r"""}; sample n = """ + e(str(cal.get('calibration_sample_size'))) + r""".
""" + e(cal.get('notes', '')) + r"""
Where no commercial-PV ground truth exists, score-versus-adoption correlations are
\textbf{not computed and never fabricated}. Supplying a ground-truth dataset activates
Pearson/Spearman correlation of final score, project-scale, usable area and feasible kWp
against observed adoption.

\section{Data quality \& missing-data register}
\begin{center}\small
\begin{tabular}{l l p{7cm}}
\toprule
\textbf{Source} & \textbf{Required} & \textbf{Impact / degraded behaviour} \\
\midrule
""" + miss_rows + r""" \\
\bottomrule
\end{tabular}\end{center}

\vspace{1em}
\noindent\rule{\textwidth}{0.4pt}\\
{\footnotesize\color{slate} Generated by the OZ commercial solar lead pipeline · model
\texttt{""" + e(MODEL_VERSION) + r"""} · run \texttt{""" + e(run_id) + r"""}. Indicative commercial
lead prioritisation only. Verify every lead before approach.}

\end{document}
"""


def _build_pdf(rdir: Path, **ctx) -> Path | None:
    if not shutil.which("pdflatex"):
        return None
    tex = _latex(**ctx)
    run_id = ctx["run_id"]
    tex_path = rdir / f"commercial_report_{run_id}.tex"
    tex_path.write_text(tex)
    for _ in range(2):  # twice for longtable/refs
        proc = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
             f"-output-directory={rdir}", tex_path.name],
            cwd=rdir, capture_output=True, text=True, timeout=120)
    pdf_path = rdir / f"commercial_report_{run_id}.pdf"
    if not pdf_path.exists():
        (rdir / "latex_log.txt").write_text(proc.stdout[-4000:] + "\n" + proc.stderr[-2000:])
        return None
    # tidy aux files
    for ext in (".aux", ".log", ".out", ".toc"):
        (rdir / f"commercial_report_{run_id}{ext}").unlink(missing_ok=True)
    return pdf_path
