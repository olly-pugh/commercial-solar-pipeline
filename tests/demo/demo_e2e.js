// End-to-end browser test for the commercial solar DEMO mode (Playwright).
// Verifies: entry page loads, submit triggers animation, fake progress reaches
// 100%, the map appears, the EXISTING Oxford report opens, and NO backend
// pipeline execution is triggered (no POST, no /api/run, no run_pipeline).
//
// Prerequisites (one-off):
//   npm i -D playwright && npx playwright install chromium
// Run (dashboard must be serving on :5055):
//   python3 -m commercial.dashboard --port 5055 &
//   BASE_URL=http://127.0.0.1:5055 node tests/demo/demo_e2e.js
const { chromium } = require('playwright');

const BASE = process.env.BASE_URL || 'http://127.0.0.1:5055';
const SHOT = process.env.SHOT_DIR || '/tmp/pw/shots';
const fs = require('fs');
fs.mkdirSync(SHOT, { recursive: true });

function assert(cond, msg) {
  if (!cond) { console.error('  ❌ FAIL:', msg); process.exitCode = 1; }
  else console.log('  ✓', msg);
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 820 } });

  // Record every network request to prove isolation.
  const reqs = [];
  page.on('request', r => reqs.push({ method: r.method(), url: r.url() }));

  // ---- 1. entry page loads ----
  await page.goto(BASE + '/demo?fast=1', { waitUntil: 'networkidle' });
  const caption = await page.textContent('.entry h1');
  assert(/Where do you want to search/i.test(caption), 'entry caption present: "' + caption.replace(/\s+/g,' ').trim() + '"');
  assert(await page.isVisible('#q'), 'text input box visible');
  await page.screenshot({ path: SHOT + '/1_entry.png' });

  // ---- 2. submit triggers animation flow (type anything, press Enter) ----
  await page.fill('#q', 'Reading');                 // arbitrary input
  await page.press('#q', 'Enter');
  await page.waitForSelector('#s-trans.show', { timeout: 4000 });
  const tmsg = await page.textContent('#transmsg');
  assert(/Great|Oxford report coming/i.test(tmsg), 'transition message shown ("' + tmsg + '")');
  await page.screenshot({ path: SHOT + '/2_transition.png' });

  // ---- 3. fake progress appears and reaches 100% ----
  await page.waitForSelector('#s-prog.show', { timeout: 6000 });
  // mid-progress screenshot
  await page.waitForFunction(() => {
    const p = parseInt((document.getElementById('pct')||{}).textContent) || 0;
    return p > 15 && p < 95;
  }, { timeout: 6000 });
  await page.screenshot({ path: SHOT + '/3_progress.png' });
  // 12 agent points rendered
  const dots = await page.$$eval('#dots .dot', els => els.length);
  const rows = await page.$$eval('#agentList .agentrow', els => els.length);
  assert(dots === 12, '12 progress points on the bar (got ' + dots + ')');
  assert(rows === 12, '12 agent names listed (got ' + rows + ')');
  await page.waitForFunction(() => window.__demo && window.__demo().progressDone === true, { timeout: 12000 });
  const pct = await page.textContent('#pct');
  assert(pct.trim() === '100%', 'progress reached 100% (got ' + pct + ')');

  // ---- 4. map appears with animated pins ----
  await page.waitForSelector('#s-map.show', { timeout: 6000 });
  await page.waitForFunction(() => document.querySelectorAll('#map g').length > 3, { timeout: 6000 });
  const pins = await page.$$eval('#map g', els => els.length);
  assert(pins > 3, 'map pins animated in (got ' + pins + ' pins)');
  const lc = await page.textContent('#leadCount');
  assert(/discovered/i.test(lc), 'lead counter shown ("' + lc.replace(/\s+/g,' ').trim() + '")');
  await page.screenshot({ path: SHOT + '/4_map.png' });

  // ---- 5. the EXISTING Oxford report opens (reused file) ----
  await page.waitForFunction(() => window.__demo && window.__demo().reportOpened === true, { timeout: 12000 });
  const src = await page.getAttribute('#reportframe', 'src');
  assert(/commercial_report_.*\.html$/.test(src || ''), 'report iframe points at existing report (' + src + ')');
  // confirm the iframe actually rendered the real report content (wait for load)
  const h1 = await page.frameLocator('#reportframe').locator('h1').first()
    .textContent({ timeout: 8000 }).catch(() => '');
  assert(/Commercial Rooftop Solar/i.test(h1 || ''), 'real report content rendered ("' + (h1||'').trim() + '")');
  await page.waitForTimeout(600);
  await page.screenshot({ path: SHOT + '/5_report.png' });

  // ---- 6. NO backend pipeline execution triggered ----
  const posts = reqs.filter(r => r.method !== 'GET');
  const danger = reqs.filter(r => /\/api\/run|run_pipeline|\/api\/demo\/run/.test(r.url));
  assert(posts.length === 0, 'no non-GET requests issued (' + posts.length + ')');
  assert(danger.length === 0, 'no calls to pipeline/run endpoints (' + danger.length + ')');
  // demo only ever hit demo/report/static GET routes
  const offdomain = reqs.filter(r => !r.url.startsWith(BASE) && !r.url.startsWith('data:'));
  assert(offdomain.length === 0, 'no external/off-domain requests (' + offdomain.length + ')');
  console.log('  · requests made:', reqs.map(r => r.method + ' ' + r.url.replace(BASE,'')).join('  |  '));

  await browser.close();
  console.log(process.exitCode ? '\nRESULT: FAIL' : '\nRESULT: PASS ✅  (all demo E2E assertions passed)');
})().catch(e => { console.error('E2E ERROR', e); process.exit(2); });
