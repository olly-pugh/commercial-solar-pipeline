"""
Commercial Solar Lead dashboard (self-contained Flask app).

A modern single-page UI to start a commercial run, watch per-stage progress, view
the full interactive report (embedded), browse ranked leads with company + map
links, and download every output (HTML / PDF / CSV / XLSX). Kept separate from the
legacy web/devdashboard apps so nothing existing breaks.

  python3 -m commercial.dashboard --port 5055
  python3 -m commercial.dashboard --port 5055 --host 0.0.0.0
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, abort

from . import settings, MODEL_VERSION
from .territory import VALID_CATEGORIES, VALID_RANKING_MODES

ROOT = settings.ROOT
app = Flask(__name__)
_running: dict[str, subprocess.Popen] = {}


# ── API ──────────────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return jsonify({"ok": True, "model": MODEL_VERSION, "service": "commercial-dashboard"})


@app.get("/api/readiness")
def readiness():
    """Lightweight readiness summary (presence only — no secret values)."""
    def present(key):
        return settings.env_present(key)
    db = settings.source_path("os_mastermap_buildings")
    onspd = settings.source_path("onspd")
    lidar = ROOT / "data" / "lidar" / "dsm"
    gt = settings.source_path("commercial_pv_ground_truth")
    items = [
        {"name": "OS MasterMap buildings", "state": "OK" if db and db.exists() else "MISSING", "required": True},
        {"name": "ONSPD postcodes", "state": "OK" if onspd and onspd.exists() else "MISSING", "required": True},
        {"name": "EA LiDAR DSM", "state": "OK" if lidar.exists() and any(lidar.glob('*.tif')) else "WARNING", "required": False},
        {"name": "Non-domestic EPC key", "state": "OK" if present("EPC_BEARER_TOKEN") else "WARNING", "required": False},
        {"name": "Companies House key", "state": "OK" if present("COMPANIES_HOUSE_API_KEY") else "WARNING", "required": False},
        {"name": "PVGIS", "state": "OK" if present("PVGIS_API") else "WARNING", "required": False},
        {"name": "Calibration ground truth", "state": "OK" if gt and gt.exists() else "WARNING", "required": False},
    ]
    can_run = bool(db and db.exists() and onspd and onspd.exists())
    return jsonify({"items": items, "can_run": can_run, "model": MODEL_VERSION})


@app.get("/api/runs")
def runs():
    out = []
    if settings.RUNS_DIR.exists():
        for d in sorted(settings.RUNS_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)[:40]:
            res = d / "result.json"
            cfg = d / "territory_config.json"
            info = {"run_id": d.name}
            if res.exists():
                try:
                    r = json.loads(res.read_text())
                    info.update({k: r.get(k) for k in ("area", "status", "qa_status",
                                 "calibration_status", "leads", "ranking_mode", "total_feasible_kwp")})
                except Exception:
                    pass
            elif cfg.exists():
                info["status"] = "running" if d.name in _running else "incomplete"
            out.append(info)
    return jsonify({"runs": out})


@app.post("/api/run")
def start_run():
    body = request.get_json(force=True, silent=True) or {}
    area = (body.get("area") or "Oxford").strip()
    cmd = [sys.executable, str(ROOT / "run_pipeline.py"), "--mode", "commercial",
           "--area", area, "--leads", str(int(body.get("leads", 100))),
           "--scenario", body.get("scenario", "central"),
           "--ranking-mode", body.get("ranking_mode", "balanced")]
    if body.get("categories"):
        cmd += ["--categories", ",".join(body["categories"])]
    if body.get("min_kwp"):
        cmd += ["--min-kwp", str(body["min_kwp"])]
    if body.get("dry_run"):
        cmd += ["--dry-run"]
    if body.get("lidar"):
        cmd += ["--lidar"]
    if body.get("limit_candidates"):
        cmd += ["--limit-candidates", str(int(body["limit_candidates"]))]
    run_id = body.get("run_id") or _next_run_id(area)
    cmd += ["--run-id", run_id]
    # Pre-seed progress so the UI can poll immediately.
    settings.run_dir(run_id)
    log = (settings.LOGS_DIR / f"{run_id}.dash.log").open("w")
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)
    _running[run_id] = proc
    threading.Thread(target=lambda: (proc.wait(), _running.pop(run_id, None)), daemon=True).start()
    return jsonify({"run_id": run_id, "started": True, "cmd": " ".join(cmd[3:])})


def _next_run_id(area: str) -> str:
    from .pipeline import _gen_run_id
    return _gen_run_id(area)


@app.get("/api/run/<run_id>/progress")
def progress(run_id):
    p = settings.RUNS_DIR / run_id / "progress.json"
    if not p.exists():
        return jsonify({"stages": {}, "running": run_id in _running})
    data = json.loads(p.read_text())
    data["running"] = run_id in _running
    return jsonify(data)


@app.get("/api/run/<run_id>/result")
def result(run_id):
    p = settings.RUNS_DIR / run_id / "result.json"
    if not p.exists():
        return jsonify({"status": "pending"})
    return jsonify(json.loads(p.read_text()))


@app.get("/api/run/<run_id>/leads")
def leads(run_id):
    import csv
    rdir = settings.REPORTS_DIR / run_id
    csvs = list(rdir.glob("commercial_leads_*.csv"))
    if not csvs:
        return jsonify({"leads": []})
    rows = []
    with csvs[0].open() as f:
        for i, row in enumerate(csv.DictReader(f)):
            if i >= 200:
                break
            rows.append(row)
    return jsonify({"leads": rows})


@app.get("/api/run/<run_id>/qa")
def qa(run_id):
    p = settings.RUNS_DIR / run_id / "qa_report.json"
    return jsonify(json.loads(p.read_text()) if p.exists() else {"qa_status": "pending"})


@app.get("/report/<run_id>/<path:fname>")
def report_file(run_id, fname):
    rdir = settings.REPORTS_DIR / run_id
    if not (rdir / fname).exists() and not (rdir / "charts" / Path(fname).name).exists():
        abort(404)
    if fname.startswith("charts/"):
        return send_from_directory(rdir / "charts", Path(fname).name)
    return send_from_directory(rdir, fname)


@app.get("/api/latest")
def latest():
    """Most recent completed run (for auto-loading the dashboard)."""
    if settings.RUNS_DIR.exists():
        for d in sorted(settings.RUNS_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
            res = d / "result.json"
            if res.exists():
                try:
                    if json.loads(res.read_text()).get("status") == "complete":
                        return jsonify({"run_id": d.name})
                except Exception:
                    pass
    return jsonify({"run_id": None})


# ─────────────────────────────────────────────────────────────────────────────
# DEMO MODE — presentation-only, fully isolated from production execution.
#
# Safety contract (enforced by structure, not just convention):
#   * Demo routes are GET-only and READ-ONLY. They NEVER import or invoke
#     `pipeline`, `run_pipeline.py`, any agent module, or any connector/API.
#   * The demo page makes NO POST requests and never touches `/api/run`.
#   * It only (a) reads coordinates from an already-generated static CSV and
#     (b) opens the already-generated report HTML. No new artefacts are created.
# A test asserts the served demo page contains none of run_pipeline/api/run.
# ─────────────────────────────────────────────────────────────────────────────
DEMO_PREFERRED_RUN = "OXF-COM-SHOWCASE"

# 12 agents — canonical display names pulled from the codebase (Agents 1–12;
# Agent 13 QA/Red-Team is an internal blocking check, not a discovery agent).
DEMO_AGENTS = [
    {"name": "Territory Intake", "weight": 2.5},
    {"name": "Commercial Candidate Discovery", "weight": 7.0},
    {"name": "Roof Geometry & Solar Potential", "weight": 8.5},
    {"name": "Project Scale / Usable Roof Area", "weight": 4.5},
    {"name": "Energy Demand & Economics", "weight": 5.5},
    {"name": "Business / Occupier Intelligence", "weight": 4.5},
    {"name": "Ownership & Decision Feasibility", "weight": 3.5},
    {"name": "Policy, Planning & Compliance", "weight": 4.0},
    {"name": "Local Market Momentum", "weight": 3.5},
    {"name": "Scoring & Ranking", "weight": 5.0},
    {"name": "Calibration", "weight": 3.5},
    {"name": "Report Generation", "weight": 8.5},
]


def _demo_run_id() -> str | None:
    """Resolve which EXISTING report the demo will reuse (read-only).

    Prefers OXF-COM-SHOWCASE; else the most recent completed run. Never creates
    anything."""
    rdir = settings.REPORTS_DIR / DEMO_PREFERRED_RUN
    if (rdir / f"commercial_report_{DEMO_PREFERRED_RUN}.html").exists():
        return DEMO_PREFERRED_RUN
    if settings.REPORTS_DIR.exists():
        for d in sorted(settings.REPORTS_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
            if list(d.glob("commercial_report_*.html")):
                return d.name
    return None


@app.get("/api/demo/leads")
def demo_leads():
    """Read-only: lead coordinates from an EXISTING static CSV, for the map
    animation only. Returns [] (never errors) if no report exists yet — the page
    then falls back to placeholder Oxford coordinates. Triggers nothing."""
    import csv
    rid = _demo_run_id()
    if not rid:
        return jsonify({"run_id": None, "leads": []})
    csvs = list((settings.REPORTS_DIR / rid).glob("commercial_leads_*.csv"))
    if not csvs:
        return jsonify({"run_id": rid, "leads": []})
    out = []
    with csvs[0].open() as f:
        for i, row in enumerate(csv.DictReader(f)):
            if i >= 80:
                break
            try:
                out.append({"lat": float(row["lat"]), "lon": float(row["lon"]),
                            "score": float(row.get("final_score_10") or 0),
                            "kwp": float(row.get("feasible_kwp") or 0),
                            "cat": (row.get("property_category") or "").replace("_", " ")})
            except (ValueError, KeyError, TypeError):
                continue
    return jsonify({"run_id": rid, "leads": out})


@app.get("/demo")
def demo():
    """Presentation-only demo. Isolated: no pipeline, no agents, no external APIs."""
    rid = _demo_run_id()
    report_url = (f"/report/{rid}/commercial_report_{rid}.html") if rid else ""
    page = _DEMO_PAGE.replace("__REPORT_URL__", report_url)
    page = page.replace("__AGENTS_JSON__", json.dumps(DEMO_AGENTS))
    return page


@app.get("/")
def index():
    return _PAGE


# ── Single-page UI (Tailwind via CDN) ────────────────────────────────────────
_PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>OZ Commercial Solar Leads</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body{background:#f1f5f9}
  .stage{transition:all .3s}
  iframe{width:100%;border:0;border-radius:12px;background:#fff}
  .scorepill{display:inline-block;min-width:38px;text-align:center;font-weight:700;border-radius:6px;padding:1px 6px;color:#fff}
  .tab{cursor:pointer;padding:9px 16px;font-weight:600;font-size:14px;border-radius:9px 9px 0 0}
  .tab.active{background:#fff;color:#1d4ed8;box-shadow:0 -2px 6px rgba(0,0,0,.04)}
  .tab:not(.active){color:#475569}
</style></head>
<body class="text-slate-800">
<header class="bg-gradient-to-r from-slate-900 via-blue-900 to-blue-700 text-white">
  <div class="max-w-7xl mx-auto px-5 py-5 flex items-center justify-between flex-wrap gap-3">
    <div>
      <div class="text-[11px] tracking-[0.16em] font-bold text-blue-200">OZ COMMERCIAL SOLAR · LEAD PRIORITISATION INTELLIGENCE</div>
      <h1 class="text-2xl font-extrabold">☀️ Commercial Rooftop Solar — Lead Engine</h1>
    </div>
    <div class="text-right text-xs text-blue-100">
      <a href="/demo" class="inline-block mb-1 px-3 py-1.5 bg-white/15 hover:bg-white/25 border border-white/25 rounded-lg font-semibold text-sm">🎬 Demo mode</a>
      <div>model <span id=model class="font-mono"></span></div>
      <div id=readiness class="flex flex-wrap gap-1 mt-1 justify-end"></div>
    </div>
  </div>
</header>

<div class="max-w-7xl mx-auto p-5 grid lg:grid-cols-4 gap-5">
  <!-- Controls -->
  <div class="lg:col-span-1 space-y-4">
    <div class="bg-white rounded-xl shadow p-4">
      <h2 class="font-bold mb-2 text-slate-900">Run a report</h2>
      <label class="block text-sm">Area / postcode
        <input id=area value="Oxford" class="w-full border rounded p-2 mt-1"></label>
      <label class="block text-sm mt-2">Leads
        <input id=leads type=number value=100 class="w-full border rounded p-2 mt-1"></label>
      <label class="block text-sm mt-2">Price scenario
        <select id=scenario class="w-full border rounded p-2 mt-1">
          <option>conservative</option><option selected>central</option><option>high</option></select></label>
      <label class="block text-sm mt-2">Ranking mode
        <select id=ranking class="w-full border rounded p-2 mt-1">RANKING_OPTS</select></label>
      <label class="block text-sm mt-2">Minimum project size
        <select id=minkwp class="w-full border rounded p-2 mt-1">
          <option value="">no minimum</option><option value=30>&gt; 30 kWp</option>
          <option value=100>&gt; 100 kWp</option><option value=250>&gt; 250 kWp</option></select></label>
      <label class="block text-sm mt-2">Categories (blank = all)
        <select id=cats multiple size=4 class="w-full border rounded p-2 mt-1 text-xs">CAT_OPTS</select></label>
      <div class="flex gap-3 mt-2 text-sm">
        <label><input type=checkbox id=dry> dry-run</label>
        <label><input type=checkbox id=lidar> LiDAR roofs</label>
      </div>
      <button onclick=startRun() class="w-full bg-blue-600 text-white rounded-lg p-2.5 mt-3 font-semibold hover:bg-blue-700">
        ▶ Generate report</button>
      <div id=startmsg class="text-xs text-slate-500 mt-2"></div>
    </div>

    <div class="bg-white rounded-xl shadow p-4">
      <div class="flex justify-between items-center">
        <h2 class="font-bold text-slate-900 text-sm">View a run</h2>
        <button onclick=loadRuns() class="text-xs text-blue-600">↻</button>
      </div>
      <select id=runpick onchange="openRun(this.value)" class="w-full border rounded p-2 mt-2 text-sm"></select>
      <div id=stages class="grid grid-cols-2 gap-1.5 mt-3 text-[11px]"></div>
    </div>
  </div>

  <!-- Main -->
  <div class="lg:col-span-3 space-y-4">
    <!-- KPI summary -->
    <div id=summarywrap class="hidden">
      <div class="flex items-center justify-between flex-wrap gap-2">
        <h2 class="font-bold text-lg text-slate-900">Run <span id=runid class="font-mono text-blue-700"></span>
          <span id=qabadge></span></h2>
        <div class="flex gap-2 text-sm" id=downloads></div>
      </div>
      <div id=kpis class="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3"></div>
    </div>

    <!-- Tabs -->
    <div>
      <div class="flex gap-1 px-1 bg-slate-200/60 rounded-t-xl pt-1">
        <div class="tab active" id=tab-report onclick="showTab('report')">📊 Full report</div>
        <div class="tab" id=tab-leads onclick="showTab('leads')">📋 Lead table</div>
      </div>
      <div class="bg-white rounded-b-xl rounded-tr-xl shadow p-3">
        <div id=pane-report>
          <div id=noreport class="text-slate-400 text-sm p-8 text-center">
            Select a run or generate a new report to view the full interactive report here.</div>
          <iframe id=reportframe class="hidden" style="height:80vh"></iframe>
        </div>
        <div id=pane-leads class="hidden">
          <div class="flex items-center gap-3 mb-2 text-sm">
            <span class="text-slate-500">Min kWp</span>
            <input id=fkwp type=number value=0 class="border rounded p-1 w-24" oninput=renderLeads()>
            <span id=leadcount class="text-slate-400"></span>
          </div>
          <div class="overflow-auto" style="max-height:78vh">
            <table class="text-xs w-full" id=leadstbl></table></div>
        </div>
      </div>
    </div>
    <p class="text-xs text-slate-400">All figures indicative — require site survey, bill validation, structural &
      planning/grid confirmation. Company links are Companies House <b>registered-office</b> matches (not confirmed
      ownership). Calibration reported honestly (inconclusive when no ground truth).</p>
  </div>
</div>

<script>
let CUR=null, LEADS=[];
fetch('/api/health').then(r=>r.json()).then(d=>model.textContent=d.model);
document.getElementById('ranking').innerHTML='RANKING_OPTS';
loadReadiness(); loadRuns();
fetch('/api/latest').then(r=>r.json()).then(d=>{ if(d.run_id) openRun(d.run_id); });

function fmtMoney(n){n=+n||0; return n>=1e6?'£'+(n/1e6).toFixed(1)+'M':n>=1e3?'£'+(n/1e3).toFixed(0)+'k':'£'+n.toFixed(0);}
function scoreColor(v){v=+v; return v<4?'#dc2626':v<6?'#f59e0b':v<7.5?'#2563eb':'#16a34a';}

function loadReadiness(){fetch('/api/readiness').then(r=>r.json()).then(d=>{
  readiness.innerHTML=d.items.map(i=>{let c=i.state=='OK'?'bg-green-500/30':(i.state=='MISSING'?'bg-red-500/40':'bg-amber-500/30');
    return `<span class="px-2 py-0.5 rounded ${c}" title="${i.name}">${i.name.split(' ')[0]}: ${i.state}</span>`}).join('');})}

function startRun(){
  let cats=[...document.getElementById('cats').selectedOptions].map(o=>o.value);
  let body={area:area.value,leads:+leads.value,scenario:scenario.value,ranking_mode:ranking.value,
    categories:cats,min_kwp:minkwp.value?+minkwp.value:null,dry_run:dry.checked,lidar:lidar.checked};
  startmsg.textContent='starting…';
  fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
    .then(r=>r.json()).then(d=>{CUR=d.run_id;runid.textContent=d.run_id;summarywrap.classList.remove('hidden');
      startmsg.textContent='running: '+d.cmd;poll();});
}
function openRun(id){ if(!id)return; CUR=id; runid.textContent=id; summarywrap.classList.remove('hidden');
  document.getElementById('runpick').value=id; poll(); }

function poll(){ if(!CUR)return;
  fetch('/api/run/'+CUR+'/progress').then(r=>r.json()).then(d=>{
    let order=['territory','discovery','roof','economics','project_scale','business','ownership','policy','momentum','scoring','enrich','calibration','qa','report'];
    let st=d.stages||{};
    stages.innerHTML=order.filter(k=>st[k]).map(k=>{let v=st[k];
      let c={succeeded:'bg-green-100 text-green-800',running:'bg-blue-100 text-blue-800 animate-pulse',
             pending:'bg-slate-100 text-slate-400',warn:'bg-amber-100 text-amber-800',
             fail:'bg-red-100 text-red-800',warning:'bg-amber-100 text-amber-800'}[v.status]||'bg-slate-100';
      return `<div class="stage ${c} rounded p-1.5"><b>${k}</b><div class="text-[10px] opacity-70">${v.detail||v.status}</div></div>`}).join('');
    if(d.running){setTimeout(poll,1500)} else {finish()}
  });
}
function finish(){ fetch('/api/run/'+CUR+'/result').then(r=>r.json()).then(d=>{
   if(d.status=='complete'){
     let q=d.qa_status; let qc=q=='FAIL'?'bg-red-100 text-red-700':q=='WARN'?'bg-amber-100 text-amber-700':'bg-green-100 text-green-700';
     qabadge.innerHTML=`<span class="ml-2 px-2 py-0.5 rounded text-xs ${qc}">QA ${q}</span>`;
     let mwp=((d.total_feasible_kwp||0)/1000).toFixed(1), ha=((d.total_usable_roof_area_m2||0)/1e4).toFixed(1);
     kpis.innerHTML=[
       ['Commercial candidates',(d.commercial_candidates||d.leads||0).toLocaleString()],
       ['Feasible capacity',mwp+' MWp'],
       ['Usable roof',ha+' ha'],
       ['Ranking mode',d.ranking_mode||'—']
     ].map(([k,v])=>`<div class="bg-white rounded-xl shadow p-3"><div class="text-2xl font-extrabold text-blue-700">${v}</div><div class="text-xs text-slate-500 font-semibold">${k}</div></div>`).join('');
     downloads.innerHTML=`
       <a class="px-3 py-1.5 bg-blue-600 text-white rounded-lg" target=_blank href="/report/${CUR}/commercial_report_${CUR}.html">📊 HTML</a>
       <a class="px-3 py-1.5 bg-rose-600 text-white rounded-lg" target=_blank href="/report/${CUR}/commercial_report_${CUR}.pdf">📕 PDF</a>
       <a class="px-3 py-1.5 bg-emerald-600 text-white rounded-lg" href="/report/${CUR}/commercial_leads_${CUR}.csv">⬇ CSV</a>
       <a class="px-3 py-1.5 bg-slate-600 text-white rounded-lg" href="/report/${CUR}/commercial_leads_${CUR}.xlsx">⬇ XLSX</a>`;
     let f=document.getElementById('reportframe');
     f.src='/report/'+CUR+'/commercial_report_'+CUR+'.html'; f.classList.remove('hidden');
     document.getElementById('noreport').classList.add('hidden');
     loadLeads();
   } else { document.getElementById('noreport').textContent='Run incomplete — see logs.'; }
 }); loadRuns(); }

function loadLeads(){ fetch('/api/run/'+CUR+'/leads').then(r=>r.json()).then(d=>{LEADS=d.leads;renderLeads();}); }
function renderLeads(){ let min=+fkwp.value||0;
  let rows=LEADS.filter(l=>(+l.feasible_kwp||0)>=min);
  leadcount.textContent=rows.length+' leads';
  let head=['#','Address','Category','Company (reg. office)','Score','kWp','Usable m²','Savings/yr','Payback','Conf'];
  let html='<thead><tr class="bg-slate-900 text-white sticky top-0">'+head.map(h=>`<th class="text-left p-2">${h}</th>`).join('')+'</tr></thead><tbody>';
  html+=rows.slice(0,120).map(l=>{
    let comp=l.primary_company_name?`<a class="text-blue-600 hover:underline" target=_blank href="${l.primary_company_url}">${l.primary_company_name}</a>`:'<span class="text-slate-300">—</span>';
    let map=l.location_maps_url?` <a target=_blank href="${l.location_maps_url}" title="Open map">📍</a>`:'';
    let sc=(+l.final_score_10).toFixed(2);
    return `<tr class="border-b hover:bg-slate-50">
      <td class="p-2 text-slate-400 font-semibold">${l.rank}</td>
      <td class="p-2">${l.address||''}${map}</td>
      <td class="p-2">${(l.property_category||'').replace(/_/g,' ')}</td>
      <td class="p-2">${comp}</td>
      <td class="p-2"><span class="scorepill" style="background:${scoreColor(sc)}">${sc}</span></td>
      <td class="p-2 text-right">${(+l.feasible_kwp||0).toLocaleString(undefined,{maximumFractionDigits:0})}</td>
      <td class="p-2 text-right">${(+l.usable_roof_area_m2||0).toLocaleString(undefined,{maximumFractionDigits:0})}</td>
      <td class="p-2 text-right">${fmtMoney(l.annual_savings_central_gbp)}</td>
      <td class="p-2 text-right">${l.payback_central_yr?(+l.payback_central_yr).toFixed(1)+'y':'—'}</td>
      <td class="p-2 text-right">${(+l.confidence_score||0).toFixed(2)}</td></tr>`;
  }).join('')+'</tbody>';
  leadstbl.innerHTML=html;
}
function showTab(t){
  document.getElementById('tab-report').classList.toggle('active',t=='report');
  document.getElementById('tab-leads').classList.toggle('active',t=='leads');
  document.getElementById('pane-report').classList.toggle('hidden',t!='report');
  document.getElementById('pane-leads').classList.toggle('hidden',t!='leads');
}
function loadRuns(){ fetch('/api/runs').then(r=>r.json()).then(d=>{
  let pick=document.getElementById('runpick');
  pick.innerHTML='<option value="">— select a run —</option>'+(d.runs||[]).map(r=>
    `<option value="${r.run_id}">${r.run_id} · ${r.area||''} · ${r.status||''} ${r.qa_status?('· QA '+r.qa_status):''}</option>`).join('');
  if(CUR)pick.value=CUR;
}); }
</script></body></html>"""
_PAGE = _PAGE.replace("RANKING_OPTS", "".join(f"<option>{m}</option>" for m in VALID_RANKING_MODES))
_PAGE = _PAGE.replace("CAT_OPTS", "".join(f"<option value='{c}'>{c}</option>" for c in VALID_CATEGORIES))


# ── Demo mode page (presentation-only, no backend execution) ──────────────────
_DEMO_PAGE = r"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Commercial Solar Lead Engine — Demo</title>
<style>
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Arial,sans-serif}
  body{background:radial-gradient(1200px 600px at 50% -10%,#1e3a8a 0%,#0f172a 55%,#020617 100%);color:#e2e8f0;overflow:hidden}
  #app{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;padding:24px}
  .screen{width:100%;max-width:920px;display:none;animation:fade .5s ease both}
  .screen.show{display:block}
  @keyframes fade{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
  .eyebrow{letter-spacing:.18em;font-size:12px;font-weight:700;color:#93c5fd;text-align:center;margin-bottom:18px}
  /* Entry */
  .entry{max-width:760px;margin:0 auto;text-align:center}
  .entry h1{font-size:34px;font-weight:800;line-height:1.25;margin:0 0 26px;color:#fff}
  .inputwrap{display:flex;gap:10px;background:#fff;border-radius:16px;padding:8px;box-shadow:0 18px 50px rgba(0,0,0,.45)}
  .inputwrap input{flex:1;border:0;outline:0;font-size:18px;padding:14px 16px;color:#0f172a;background:transparent}
  .inputwrap button{border:0;background:#2563eb;color:#fff;font-weight:700;font-size:16px;padding:0 24px;border-radius:11px;cursor:pointer}
  .inputwrap button:hover{background:#1d4ed8}
  .hint{margin-top:14px;color:#94a3b8;font-size:13px}
  .suggest{margin-top:20px;display:flex;gap:8px;justify-content:center;flex-wrap:wrap}
  .suggest span{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.15);padding:6px 14px;border-radius:999px;font-size:13px;cursor:pointer;color:#cbd5e1}
  .suggest span:hover{background:rgba(255,255,255,.16);color:#fff}
  /* Transition */
  .bigmsg{text-align:center;font-size:64px;font-weight:800;color:#fff}
  .bigmsg.sub{font-size:40px;color:#bfdbfe}
  /* Progress */
  .pcard{background:rgba(15,23,42,.55);border:1px solid rgba(148,163,184,.2);border-radius:22px;padding:34px 30px;backdrop-filter:blur(6px)}
  .pcard h2{margin:0 0 4px;font-size:22px;color:#fff;font-weight:800}
  .pcard .area{color:#93c5fd;font-weight:700}
  .pct{font-size:72px;font-weight:800;color:#fff;text-align:center;line-height:1;margin:6px 0 2px;font-variant-numeric:tabular-nums}
  .nowagent{text-align:center;color:#cbd5e1;font-size:15px;height:22px;margin-bottom:22px}
  .nowagent b{color:#fff}
  .track{position:relative;height:8px;background:rgba(148,163,184,.22);border-radius:999px;margin:38px 8px 8px}
  .fill{position:absolute;left:0;top:0;bottom:0;width:0;background:linear-gradient(90deg,#22d3ee,#3b82f6,#22c55e);border-radius:999px;transition:width .12s linear}
  .dots{position:absolute;inset:-6px 0;}
  .dot{position:absolute;top:50%;width:18px;height:18px;border-radius:50%;
       background:#1e293b;border:2px solid #475569;transform:translate(-50%,-50%);transition:all .3s}
  .dot.done{background:#22c55e;border-color:#22c55e;box-shadow:0 0 0 4px rgba(34,197,94,.18)}
  .dot.active{background:#3b82f6;border-color:#93c5fd;box-shadow:0 0 0 5px rgba(59,130,246,.25);animation:pulse 1s infinite}
  @keyframes pulse{0%,100%{box-shadow:0 0 0 5px rgba(59,130,246,.25)}50%{box-shadow:0 0 0 9px rgba(59,130,246,.05)}}
  .agentlist{display:grid;grid-template-columns:repeat(2,1fr);gap:6px 22px;margin-top:42px;font-size:13px}
  .agentrow{display:flex;align-items:center;gap:9px;color:#94a3b8;padding:3px 0}
  .agentrow .ic{width:18px;height:18px;border-radius:50%;border:2px solid #475569;flex:none;display:flex;align-items:center;justify-content:center;font-size:11px}
  .agentrow.done{color:#e2e8f0}.agentrow.done .ic{background:#22c55e;border-color:#22c55e;color:#06281a}
  .agentrow.active{color:#fff}.agentrow.active .ic{border-color:#3b82f6}
  /* Map */
  .mapwrap{background:rgba(15,23,42,.55);border:1px solid rgba(148,163,184,.2);border-radius:22px;padding:22px}
  .mapwrap h2{margin:0 0 2px;font-size:20px;color:#fff;font-weight:800}
  .mapwrap .ml{color:#93c5fd;font-size:13px;margin-bottom:12px}
  svg{width:100%;height:auto;display:block;border-radius:14px}
  .leadcount{text-align:center;margin-top:12px;font-size:16px;color:#fff;font-weight:700}
  .leadcount span{color:#22d3ee}
  .skip{position:fixed;right:16px;bottom:14px;font-size:12px;color:#64748b;cursor:pointer;z-index:9}
  .skip:hover{color:#cbd5e1}
  /* Report */
  #reportscreen{position:fixed;inset:0;display:none;flex-direction:column}
  #reportbar{background:#0b1222;color:#fff;display:flex;align-items:center;gap:14px;padding:8px 16px;font-size:13px;flex:none}
  #reportbar .badge{background:#16a34a;color:#fff;padding:2px 9px;border-radius:999px;font-weight:700;font-size:11px}
  #reportbar a{color:#93c5fd;text-decoration:none;cursor:pointer}
  #reportframe{flex:1;border:0;width:100%;background:#fff}
</style></head>
<body>
<div id=app>
  <!-- 0: entry -->
  <section class="screen show" id=s-entry>
    <div class="entry">
      <div class="eyebrow">OZ COMMERCIAL SOLAR · LEAD ENGINE</div>
      <h1>Where do you want to search for commercial<br>solar leads today?</h1>
      <div class="inputwrap">
        <input id=q placeholder="e.g. Oxford, Reading, M25 corridor, OX4…" autocomplete=off autofocus>
        <button id=go>Search ➜</button>
      </div>
      <div class="hint">Press Enter to begin · this is a guided demo using a pre-generated Oxford report</div>
      <div class="suggest">
        <span data-q="Oxford">Oxford</span><span data-q="Reading">Reading</span>
        <span data-q="Milton Park, Didcot">Milton Park</span><span data-q="Slough Trading Estate">Slough Estate</span>
      </div>
    </div>
  </section>

  <!-- 1: transition -->
  <section class="screen" id=s-trans>
    <div class="bigmsg" id=transmsg>Great!</div>
  </section>

  <!-- 2: progress -->
  <section class="screen" id=s-prog>
    <div class="pcard">
      <h2>Scanning <span class="area" id=areaName>Oxford</span> for commercial solar leads…</h2>
      <div class="pct" id=pct>0%</div>
      <div class="nowagent" id=nowAgent>Initialising pipeline…</div>
      <div class="track"><div class="fill" id=fill></div><div class="dots" id=dots></div></div>
      <div class="agentlist" id=agentList></div>
    </div>
  </section>

  <!-- 3: map -->
  <section class="screen" id=s-map>
    <div class="mapwrap">
      <h2>Commercial solar leads — <span id=mapArea>Oxford</span></h2>
      <div class="ml">Plotting discovered rooftops · demo visualisation only</div>
      <svg id=map viewBox="0 0 860 460" preserveAspectRatio="xMidYMid meet"></svg>
      <div class="leadcount" id=leadCount></div>
    </div>
  </section>
</div>

<!-- 4: report (existing HTML, reused unmodified) -->
<div id=reportscreen>
  <div id=reportbar>
    <span class="badge">DEMO</span>
    <span>Pre-generated Oxford commercial solar report</span>
    <a onclick="location.reload()">↻ Restart demo</a>
    <a id=openNew target=_blank style="margin-left:auto">Open report in new tab ↗</a>
  </div>
  <iframe id=reportframe title="Oxford commercial solar report"></iframe>
</div>

<div class="skip" id=skip>skip ▸</div>

<script>
const AGENTS = __AGENTS_JSON__;
const REPORT_URL = "__REPORT_URL__";
const FAST = new URLSearchParams(location.search).has('fast'); // test-only time compression
const TOTAL_MS = FAST ? 2600 : 60000;
let AREA = "Oxford";

const $ = s => document.querySelector(s);
const show = id => { document.querySelectorAll('.screen').forEach(e=>e.classList.remove('show'));
  const el = document.getElementById(id); if(el) el.classList.add('show'); };

// ── 0: entry ────────────────────────────────────────────────────────────────
function start(){
  const v = $('#q').value.trim();
  if(v) AREA = v;                       // accept any input (personalises the scan header)…
  $('#areaName').textContent = AREA;    // …but the map + report are always the Oxford demo,
  // so #mapArea stays "Oxford" — matching the "Oxford report coming right up!" promise.
  transition();
}
$('#go').onclick = start;
$('#q').addEventListener('keydown', e => { if(e.key==='Enter') start(); });
document.querySelectorAll('.suggest span').forEach(s =>
  s.onclick = () => { $('#q').value = s.dataset.q; start(); });

// ── 1: transition: "Great!" → "Oxford report coming right up!" ───────────────
function transition(){
  show('s-trans');
  const m = $('#transmsg');
  m.textContent = 'Great!'; m.classList.remove('sub'); m.style.opacity = 1;
  const t = FAST ? 380 : 1100;
  setTimeout(()=>{ m.style.transition='opacity .45s'; m.style.opacity = 0; }, t);
  setTimeout(()=>{ m.textContent = 'Oxford report coming right up!'; m.classList.add('sub'); m.style.opacity = 1; }, t+480);
  setTimeout(()=>{ m.style.opacity = 0; }, t+ (FAST?900:2100));
  setTimeout(runProgress, t + (FAST?1150:2500));
}

// ── 2: fake weighted progress (12 agents, ~60s, proportional) ────────────────
let progressDone = false;
function runProgress(){
  show('s-prog');
  const totalW = AGENTS.reduce((a,b)=>a+b.weight,0);
  // cumulative fraction at which each agent COMPLETES
  let acc = 0; const cum = AGENTS.map(a => (acc += a.weight, acc/totalW));
  // dots + list
  $('#dots').innerHTML = cum.map((c,i)=>`<div class="dot" id="dot${i}" style="left:${(c*100).toFixed(2)}%"></div>`).join('');
  $('#agentList').innerHTML = AGENTS.map((a,i)=>
    `<div class="agentrow" id="row${i}"><span class="ic" id="ic${i}">${i+1}</span>${a.name}</div>`).join('');

  const t0 = performance.now();
  function frame(now){
    const f = Math.min(1, (now - t0) / TOTAL_MS);
    const pctV = Math.round(f * 100);
    $('#pct').textContent = pctV + '%';
    $('#fill').style.width = (f*100) + '%';
    let active = AGENTS.length - 1;
    for(let i=0;i<AGENTS.length;i++){
      const done = f >= cum[i];
      const prevCum = i===0 ? 0 : cum[i-1];
      const isActive = !done && f >= prevCum;
      $('#dot'+i).classList.toggle('done', done);
      $('#dot'+i).classList.toggle('active', isActive);
      $('#row'+i).classList.toggle('done', done);
      $('#row'+i).classList.toggle('active', isActive);
      $('#ic'+i).textContent = done ? '✓' : (i+1);
      if(isActive){ active = i; $('#nowAgent').innerHTML = 'Running · <b>'+AGENTS[i].name+'</b>'; }
    }
    if(f < 1){ requestAnimationFrame(frame); }
    else { $('#nowAgent').innerHTML = '<b>All 12 agents complete</b>'; progressDone = true;
           setTimeout(runMap, FAST?250:700); }
  }
  requestAnimationFrame(frame);
}

// ── 3: map with animated pins (offline SVG; no external tiles/APIs) ───────────
const PLACEHOLDER = [ // safe Oxford-area coords if no static leads available
  [51.7520,-1.2577],[51.7290,-1.1948],[51.7325,-1.1897],[51.7317,-1.1827],[51.7430,-1.2200],
  [51.7480,-1.2360],[51.7600,-1.2500],[51.7410,-1.2700],[51.7220,-1.2100],[51.7700,-1.2300],
  [51.7380,-1.2050],[51.7550,-1.2150],[51.7260,-1.2400],[51.7470,-1.1980],[51.7340,-1.2600]];
let mapDone = false;
function runMap(){
  show('s-map');
  fetch('/api/demo/leads').then(r=>r.json()).then(d=>{
    let pts = (d.leads&&d.leads.length) ? d.leads.map(l=>[l.lat,l.lon,l.score]) : PLACEHOLDER.map(p=>[p[0],p[1],8]);
    drawMap(pts);
  }).catch(()=> drawMap(PLACEHOLDER.map(p=>[p[0],p[1],8])));
}
function drawMap(pts){
  const W=860,H=460,pad=46;
  let lats=pts.map(p=>p[0]), lons=pts.map(p=>p[1]);
  let laMin=Math.min(...lats)-0.004, laMax=Math.max(...lats)+0.004;
  let loMin=Math.min(...lons)-0.004, loMax=Math.max(...lons)+0.004;
  const X=lon=>pad+(lon-loMin)/(loMax-loMin)*(W-2*pad);
  const Y=lat=>pad+(laMax-lat)/(laMax-laMin)*(H-2*pad);
  const svg=$('#map');
  let bg=`<defs>
    <radialGradient id="pin" cx="50%" cy="50%" r="50%"><stop offset="0%" stop-color="#fde68a"/><stop offset="60%" stop-color="#f59e0b"/><stop offset="100%" stop-color="#b45309"/></radialGradient>
    <linearGradient id="bgg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#0b1b3a"/><stop offset="100%" stop-color="#071226"/></linearGradient>
  </defs>
  <rect x="0" y="0" width="${W}" height="${H}" rx="14" fill="url(#bgg)"/>`;
  // subtle grid
  for(let gx=pad;gx<W-pad;gx+=58) bg+=`<line x1="${gx}" y1="${pad}" x2="${gx}" y2="${H-pad}" stroke="#1e3a5f" stroke-width="1" opacity=".5"/>`;
  for(let gy=pad;gy<H-pad;gy+=58) bg+=`<line x1="${pad}" y1="${gy}" x2="${W-pad}" y2="${gy}" stroke="#1e3a5f" stroke-width="1" opacity=".5"/>`;
  // faux river (Thames) + ring-road suggestion
  bg+=`<path d="M ${pad} ${H*0.30} C ${W*0.35} ${H*0.45}, ${W*0.45} ${H*0.20}, ${W*0.7} ${H*0.55} S ${W-pad} ${H*0.7}, ${W-pad} ${H*0.78}" stroke="#1d4ed8" stroke-width="6" fill="none" opacity=".35" stroke-linecap="round"/>`;
  bg+=`<ellipse cx="${W/2}" cy="${H/2}" rx="${(W-2*pad)/2.2}" ry="${(H-2*pad)/2.2}" stroke="#334e7a" stroke-width="2" fill="none" stroke-dasharray="5 7" opacity=".5"/>`;
  bg+=`<text x="${pad+6}" y="${H-pad+24}" fill="#64748b" font-size="13" font-weight="700" letter-spacing="2">OXFORD · demo map</text>`;
  svg.innerHTML=bg;
  let shown=0;
  pts.forEach((p,i)=>{
    setTimeout(()=>{
      const cx=X(p[1]), cy=Y(p[0]);
      const col = p[2]>=7.5?'#22c55e':p[2]>=6?'#f59e0b':'#60a5fa';
      const g=document.createElementNS('http://www.w3.org/2000/svg','g');
      g.innerHTML=`<circle cx="${cx}" cy="${cy}" r="3" fill="${col}" opacity=".25">
          <animate attributeName="r" from="3" to="20" dur="1.1s" repeatCount="indefinite"/>
          <animate attributeName="opacity" from=".5" to="0" dur="1.1s" repeatCount="indefinite"/></circle>
        <circle cx="${cx}" cy="${cy}" r="0" fill="${col}" stroke="#fff" stroke-width="1.2">
          <animate attributeName="r" from="0" to="5.5" dur=".4s" fill="freeze"/></circle>`;
      svg.appendChild(g);
      shown++; $('#leadCount').innerHTML=`<span>${shown}</span> commercial solar leads discovered`;
    }, i*(FAST?12:55));
  });
  const dur = pts.length*(FAST?12:55) + (FAST?500:1400);
  setTimeout(()=>{ mapDone=true; openReport(); }, dur);
}

// ── 4: open the EXISTING report (reused unmodified) ──────────────────────────
let reportOpened=false;
function openReport(){
  if(!REPORT_URL){ $('#leadCount').innerHTML='<span>Demo report not found</span> — generate a run first.'; return; }
  reportOpened=true;
  $('#openNew').href=REPORT_URL;
  $('#reportframe').src=REPORT_URL;
  document.getElementById('app').style.display='none';
  $('#skip').style.display='none';
  document.getElementById('reportscreen').style.display='flex';
}
// skip jumps straight to the existing report (still no backend call)
$('#skip').onclick = openReport;
// expose state for automated tests
window.__demo = () => ({progressDone, mapDone, reportOpened,
  pct: ($('#pct')||{}).textContent, reportSrc: $('#reportframe').getAttribute('src')||''});
</script></body></html>"""


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5055)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--daemon", action="store_true")
    args = ap.parse_args()
    if args.daemon:
        import os
        if os.fork():
            print(f"Commercial dashboard daemonised on http://{args.host}:{args.port}")
            return
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
