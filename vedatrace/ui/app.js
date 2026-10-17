/* VedaTrace demo UI. Everything shown comes from the API, which reads FalkorDB. No mock data. */
const $ = (id) => document.getElementById(id);
const COLORS = { Account: '#4aa3ff', Transaction: '#7d8590', Device: '#e3a008', Customer: '#56d4dd', Evidence: '#f0883e', Pattern: '#f85149', Policy: '#a371f7' };
const SHAPES = { Account: 'ellipse', Transaction: 'diamond', Device: 'round-rectangle', Customer: 'ellipse', Evidence: 'hexagon', Pattern: 'star', Policy: 'octagon' };
const SIZES = { Account: 30, Transaction: 14, Device: 24, Customer: 20, Evidence: 28, Pattern: 38, Policy: 30 };
const WRITERS = new Set(['write_evidence', 'write_pattern']);
/* human-readable names and a colour family for each tool the Investigator can call */
const TOOLS = {
  get_transaction: { label: 'Read transaction', kind: 'read' },
  get_account_profile: { label: 'Profile account', kind: 'read' },
  get_neighbors: { label: 'Inspect neighbours', kind: 'read' },
  find_shared_devices: { label: 'Find shared devices', kind: 'analyze' },
  analyze_fanout: { label: 'Analyze fan-out', kind: 'analyze' },
  trace_flow: { label: 'Trace money flow', kind: 'analyze' },
  find_cycles: { label: 'Search circular flows', kind: 'analyze' },
  shortest_path: { label: 'Find shortest path', kind: 'analyze' },
  list_policies: { label: 'Read policies', kind: 'policy' },
  write_evidence: { label: 'Record evidence', kind: 'write' },
  write_pattern: { label: 'Record pattern', kind: 'write' },
  finish: { label: 'Finish', kind: 'finish' },
};
const PACE = { step: 520, write: 1100 }; /* ms between steps during a replay */

let cy, es = null, currentInv = null, currentReport = null, graphTimer = null, running = false, stepCount = 0, readonly = false, adminEnabled = false, adminToken = null;
try { adminToken = sessionStorage.getItem('vt_admin'); } catch (e) { /* storage may be blocked */ }
const locked = () => readonly && !adminToken; /* true on a read-only deployment until the owner unlocks it */
let player = null, fullGraph = null, pastMeta = {}, noteText = '', currentTx = '', runFinished = false;

/* ---------- helpers ---------- */
async function api(path, opts) {
  let r;
  if (adminToken && opts && opts.method && opts.method !== 'GET') opts = { ...opts, headers: { ...(opts.headers || {}), 'X-Admin-Token': adminToken } };
  try { r = await fetch(path, opts); } catch (e) { throw new Error('Cannot reach the VedaTrace API: ' + e.message); }
  let body = null;
  try { body = await r.json(); } catch (e) { /* non-json */ }
  if (!r.ok) {
    const d = body && (body.detail || body.error);
    const msg = typeof d === 'string' ? d : JSON.stringify(d);
    const err = new Error((body && body.error ? body.error + ': ' : '') + (msg || r.statusText) + (body && body.hint ? '\n' + body.hint : ''));
    err.status = r.status;
    throw err;
  }
  return body;
}
function banner(msg, info) {
  const b = $('banner');
  if (!msg) { b.classList.add('hidden'); return; }
  b.textContent = msg; b.classList.toggle('info', !!info); b.classList.remove('hidden');
}
function setStatus(msg, cls) { const s = $('case-status'); s.textContent = msg; s.title = msg; s.className = 'status ' + (cls || ''); }
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }

/* ---------- health ---------- */
async function health() {
  try {
    const h = await api('/api/health');
    const db = $('b-db'), llm = $('b-llm'), mem = $('b-mem');
    db.textContent = h.falkordb.ok ? `FalkorDB · ${h.falkordb.nodes.toLocaleString()} nodes` : 'FalkorDB down';
    db.className = 'badge ' + (h.falkordb.ok ? 'ok' : 'bad');
    llm.textContent = `${h.llm.provider} · ${h.llm.model}`;
    llm.className = 'badge ' + (h.llm.ok ? 'ok' : 'bad');
    if (h.readonly) { llm.textContent = 'replay · no LLM calls'; llm.className = 'badge off'; llm.title = 'Read-only replay of a recorded run'; }
    mem.textContent = h.memory.enabled ? `memory on · ${h.memory.backend}` : 'memory off';
    mem.className = 'badge ' + (h.memory.enabled ? 'ok' : 'off');
    mem.title = h.memory.detail || '';
    if (h.readonly && !h.memory.enabled) { mem.textContent = 'memory · shown in recording'; mem.title = 'Recalled cases are stored in the recorded run (Graphiti + case store); no live memory service here'; }
    readonly = !!h.readonly; adminEnabled = !!h.admin_enabled;
    applyLock();
    const problems = [];
    if (!h.falkordb.ok) problems.push('FalkorDB is not reachable. Start it with: docker start vedatrace-falkordb  (or docker run -p 6379:6379 -p 3000:3000 falkordb/falkordb)');
    else if (h.falkordb.nodes === 0) problems.push('The graph is empty. Press "Reset data" to seed it.');
    if (readonly && adminToken) problems.push('Admin mode: live runs are enabled and use the server\'s LLM quota. Reset stays locked so the recorded run is safe.');
    else if (readonly) problems.push('Public demo: you can replay the recorded investigation of TX-FLAGSHIP. Alerts without a recording need a live run, which is off here.' + (adminEnabled ? ' The owner can unlock live runs with the Admin button.' : ''));
    else if (!h.llm.ok) problems.push('Live runs are disabled (' + (h.llm.detail || 'LLM not ready') + '). Recorded runs can still be replayed.');
    banner(problems.join('\n'), !problems.some((p) => p.startsWith('FalkorDB')));
    return h;
  } catch (e) { banner(e.message); }
}

/* ---------- read-only lock and admin unlock ---------- */
function applyLock() {
  const lock = locked();
  document.querySelectorAll('.run-btn').forEach((b) => { b.disabled = lock || running; b.title = lock ? 'Live runs are off in the public demo' : ''; });
  $('reset').disabled = readonly; $('reset').classList.toggle('hidden', readonly);
  if (lock) $('make-report').disabled = true;
  const u = $('unlock'); u.classList.toggle('hidden', !(readonly && adminEnabled)); u.textContent = adminToken ? '🔓 Admin mode' : '🔒 Admin';
  u.title = adminToken ? 'Click to lock again' : 'Owner only: unlock live runs';
}
async function toggleAdmin() {
  if (adminToken) { adminToken = null; try { sessionStorage.removeItem('vt_admin'); } catch (e) { /* ignore */ } await health(); return; }
  const t = window.prompt('Admin token (owner only)'); if (!t) return;
  try {
    const r = await fetch('/api/admin/check', { headers: { 'X-Admin-Token': t } }), b = await r.json();
    if (b.ok) { adminToken = t; try { sessionStorage.setItem('vt_admin', t); } catch (e) { /* ignore */ } setStatus('Admin mode on: live runs are enabled.', 'ok'); }
    else window.alert('Wrong admin token.');
  } catch (e) { window.alert(e.message); }
  await health();
}

/* ---------- graph ---------- */
function initGraph() {
  cy = cytoscape({
    container: $('cy'), wheelSensitivity: 0.25, minZoom: 0.1, maxZoom: 3,
    style: [
      { selector: 'node', style: { 'background-color': (n) => COLORS[n.data('type')] || '#888', shape: (n) => SHAPES[n.data('type')] || 'ellipse',
          width: (n) => SIZES[n.data('type')] || 20, height: (n) => SIZES[n.data('type')] || 20, label: 'data(short)', color: '#c9d1d9', 'font-size': 9,
          'text-valign': 'bottom', 'text-margin-y': 3, 'text-outline-color': '#0c1015', 'text-outline-width': 2, opacity: 0.45, 'border-width': 0 } },
      { selector: 'node[?touched]', style: { opacity: 1, 'border-width': 2, 'border-color': '#fff' } },
      { selector: 'node[type="Transaction"]', style: { label: '' } },
      { selector: 'node[type="Transaction"][?touched]', style: { label: 'data(short)' } },
      { selector: 'node.fresh', style: { 'border-width': 4, 'border-color': '#3fb950' } },
      { selector: 'node.focus', style: { 'border-width': 5, 'border-color': '#ffd33d', 'z-index': 99, opacity: 1 } },
      { selector: 'edge', style: { width: 1.2, 'line-color': '#3b4552', 'target-arrow-color': '#3b4552', 'target-arrow-shape': 'triangle', 'arrow-scale': 0.7, 'curve-style': 'bezier', opacity: 0.7 } },
      { selector: 'edge[type="SUPPORTED_BY"]', style: { 'line-color': '#f0883e', 'target-arrow-color': '#f0883e', 'line-style': 'dashed', width: 1.6, opacity: 0.95 } },
      { selector: 'edge[type="BACKED_BY"]', style: { 'line-color': '#f85149', 'target-arrow-color': '#f85149', width: 2.2, opacity: 1 } },
      { selector: 'edge[type="VIOLATES"]', style: { 'line-color': '#a371f7', 'target-arrow-color': '#a371f7', width: 2.2, opacity: 1 } },
      { selector: 'edge[type="USED_DEVICE"]', style: { 'line-color': '#e3a008', 'target-arrow-color': '#e3a008', opacity: 0.8 } },
    ],
  });
  cy.on('tap', 'node', (ev) => inspect(ev.target.id()));
  cy.on('tap', (ev) => { if (ev.target === cy) $('inspector').classList.add('hidden'); });
  cy.on('mouseover', 'node', (ev) => { $('graph-note').textContent = `${ev.target.id()} · ${ev.target.data('type')}`; });
  cy.on('mouseout', 'node', () => { $('graph-note').textContent = noteText; });
  const lg = $('legend');
  Object.entries(COLORS).forEach(([k, c]) => { const s = el('span'); const i = el('i'); i.style.background = c; s.append(i, k); lg.append(s); });
}

function shortLabel(n) { return n.id.replace(/^(ACC|TX|DEV|CUST)-/, '$1·'); }

/* Which nodes of the full graph are visible after step `upto` of a replay (Infinity = everything).
   Every node carries the number of the first step that touched or produced it; 999 = context, 998 = policies. */
function visibleSet(g, upto) {
  const live = upto === Infinity, vis = new Set(), byId = new Map(g.nodes.map((n) => [n.id, n]));
  g.nodes.forEach((n) => { if (live || (n.step <= upto && n.step < 998)) vis.add(n.id); });
  if (!live) {
    g.edges.forEach((e) => {
      const a = byId.get(e.source), b = byId.get(e.target);
      if (!a || !b) return;
      if (a.step === 999 && vis.has(b.id) && b.step < 998) vis.add(a.id);
      if (b.step === 999 && vis.has(a.id) && a.step < 998) vis.add(b.id);
    });
  }
  return vis;
}

function applyGraph(g, upto) {
  const vis = visibleSet(g, upto), have = new Set(cy.nodes().map((n) => n.id())), add = [];
  g.nodes.forEach((n) => {
    if (!vis.has(n.id)) return;
    const touched = !!n.touched && (upto === Infinity || n.step <= upto);
    if (!have.has(n.id)) add.push({ group: 'nodes', data: { ...n, touched, short: shortLabel(n), props: undefined }, classes: 'fresh' });
    else cy.getElementById(n.id).data('touched', touched);
  });
  const haveE = new Set(cy.edges().map((e) => `${e.data('source')}|${e.data('target')}|${e.data('type')}`));
  g.edges.forEach((e) => {
    if (vis.has(e.source) && vis.has(e.target) && !haveE.has(`${e.source}|${e.target}|${e.type}`)) add.push({ group: 'edges', data: { ...e, id: `e:${e.source}|${e.target}|${e.type}` } });
  });
  if (add.length) {
    const eles = cy.add(add);
    eles.nodes().style('opacity', 0).animate({ style: { opacity: 1 } }, { duration: 500 });
    cy.layout({ name: 'cose', animate: true, animationDuration: 600, randomize: false, fit: true, padding: 30, nodeRepulsion: 9000, idealEdgeLength: 55, numIter: 600 }).run();
    setTimeout(() => cy.nodes('.fresh').removeClass('fresh'), 2200);
  }
  noteText = `${cy.nodes().length} nodes · ${cy.edges().length} edges${g.capped ? ' (capped)' : ''}`;
  $('graph-note').textContent = noteText;
}

async function refreshGraph() {
  if (!currentInv) return;
  let g;
  try { g = await api(`/api/investigations/${currentInv}/graph`); } catch (e) { banner(e.message); return; }
  fullGraph = g; applyGraph(g, Infinity);
}
function scheduleGraph() { clearTimeout(graphTimer); graphTimer = setTimeout(() => refreshGraph(), 700); }

async function focusNode(id) {
  let n = cy.getElementById(id);
  if ((!n || n.empty()) && !player) { await refreshGraph(); n = cy.getElementById(id); }
  if (n && !n.empty()) {
    cy.nodes().removeClass('focus'); n.addClass('focus');
    cy.animate({ center: { eles: n }, zoom: Math.max(cy.zoom(), 1.1) }, { duration: 450 });
    setTimeout(() => n.removeClass('focus'), 4500);
  }
  inspect(id);
}

async function inspect(id) {
  const box = $('inspector');
  let d;
  try { d = await api('/api/node/' + encodeURIComponent(id)); } catch (e) { box.innerHTML = ''; box.append(el('div', 'muted', e.message)); box.classList.remove('hidden'); return; }
  box.innerHTML = '';
  const x = el('span', 'x', '✕'); x.onclick = () => box.classList.add('hidden');
  box.append(x, el('h3', '', `${d.type} · ${d.id}`));
  const t = el('table');
  Object.entries(d.props).forEach(([k, v]) => {
    if (['ts', 'opened_ts', 'started_ts', 'finished_ts', 'created_ts'].includes(k) && typeof v === 'number') v = new Date((v > 1e11 ? v : v * 1000)).toISOString().replace('T', ' ').slice(0, 16);
    const tr = el('tr'); tr.append(el('td', '', k), el('td', '', typeof v === 'object' ? JSON.stringify(v) : String(v))); t.append(tr);
  });
  box.append(t);
  if (d.relationships.length) {
    box.append(el('div', 'muted', 'Provenance / relationships'));
    const ul = el('div');
    d.relationships.slice(0, 30).forEach((r) => {
      const a = el('a', '', r.id); a.onclick = () => focusNode(r.id);
      const row = el('div'); row.append(el('span', 'muted', `${r.direction === 'out' ? '→' : '←'} ${r.edge} `), a); ul.append(row);
    });
    box.append(ul);
  }
  box.classList.remove('hidden');
}

/* ---------- steps and findings ---------- */
function addStep(s) {
  stepCount = Math.max(stepCount, s.seq);
  $('step-count').textContent = `${stepCount} step${stepCount === 1 ? '' : 's'}`;
  const meta = TOOLS[s.tool] || { label: s.tool, kind: 'read' };
  const li = el('li', `k-${meta.kind}${s.ok ? '' : ' err'}`);
  const head = el('div', 'head');
  head.append(el('span', 'idx', String(s.seq).padStart(2, '0')), el('span', 'label', meta.label));
  if (!s.ok) head.append(el('span', 'rej', 'rejected'));
  const args = Object.entries(s.args || {}).map(([k, v]) => `${k}=${Array.isArray(v) ? `[${v.length}]` : String(v).slice(0, 28)}`).join(', ');
  li.append(head, el('div', 'call', `${s.tool}(${args})`), el('div', 'why', s.reason || ''), el('div', 'res', s.summary || ''));
  li.onclick = () => { const a = Object.values(s.args || {}).flat().find((v) => typeof v === 'string' && /^(ACC|TX|DEV)-/.test(v)); if (a) focusNode(a); };
  $('steps-empty').classList.add('hidden'); $('steps').append(li); $('pane-steps').scrollTop = 1e9;
}
function addFinding(kind, f) {
  const d = el('div', 'finding ' + (kind === 'pattern' ? 'pattern' : f.kind === 'BENIGN_EXPLANATION' ? 'benign' : ''));
  const b = el('b', '', `${f.id} · ${kind === 'pattern' ? f.type : f.kind}`);
  d.append(b, el('div', '', f.summary)); d.onclick = () => focusNode(f.id);
  $('findings').append(d);
}

/* ---------- live stream (a run that is in progress) ---------- */
function openStream(inv) {
  if (es) es.close();
  es = new EventSource(`/api/investigations/${inv}/stream`);
  es.addEventListener('step', (e) => { addStep(JSON.parse(e.data)); scheduleGraph(); });
  es.addEventListener('evidence', (e) => { addFinding('evidence', JSON.parse(e.data)); scheduleGraph(); });
  es.addEventListener('pattern', (e) => { addFinding('pattern', JSON.parse(e.data)); scheduleGraph(); });
  es.addEventListener('status', (e) => { const s = JSON.parse(e.data); if (s.status === 'running') setStatus(`Investigating… ${s.steps} steps`, ''); });
  es.addEventListener('error', (e) => {
    if (e.data) { const m = JSON.parse(e.data).message; setStatus(m, 'err'); banner(m); }
  });
  es.addEventListener('done', async (e) => {
    const d = JSON.parse(e.data); es.close(); es = null; running = false;
    applyLock();
    setStatus(d.status === 'finished' ? `Finished. ${d.summary || ''}` : d.status === 'failed' ? `Investigation failed: ${d.summary || ''}` : `Stopped (${d.status}). ${d.summary || ''}`, d.status === 'finished' ? 'ok' : 'err');
    markFinished(d.status);
    $('replay').disabled = false;
    await refreshGraph();
    await loadPast(); loadAlerts();
    await showSummary(currentInv, null);
    health();
  });
}

function resetView() {
  stopPlayer();
  if (es) { es.close(); es = null; }
  $('steps').innerHTML = ''; $('steps-empty').classList.remove('hidden'); $('findings').innerHTML = ''; stepCount = 0; $('step-count').textContent = '0 steps';
  if (cy) cy.elements().remove(); noteText = ''; $('graph-note').textContent = ''; $('inspector').classList.add('hidden'); fullGraph = null;
  $('report').className = 'report muted'; $('report').textContent = 'The Reporter runs as a separate process. It reads only the graph, never the Investigator\'s memory.';
  ['dl-md', 'dl-json'].forEach((i) => $('' + i).classList.add('hidden'));
  $('make-report').disabled = true; $('make-report').textContent = 'Generate report'; currentReport = null; $('recalled').classList.add('hidden');
  runFinished = false; $('to-report').classList.add('disabled'); updateNav();
  $('summary').classList.add('hidden'); $('summary').innerHTML = '';
  $('replay').disabled = true; $('skip').disabled = true;
}

/* ---------- summary bar ---------- */
async function showSummary(inv, rep) {
  const meta = pastMeta[inv] || {}, tx = meta.tx_id || currentTx;
  let amount = null, score = null;
  try { const t = await api('/api/node/' + encodeURIComponent(tx)); amount = t.props.amount; score = t.props.monitor_score; } catch (e) { /* optional */ }
  const nEv = $('findings').querySelectorAll('.finding:not(.pattern)').length, nPa = $('findings').querySelectorAll('.finding.pattern').length;
  const box = $('summary'); box.innerHTML = '';
  const chip = (k, v) => { const c = el('span', 'chip'); c.append(el('em', '', k), el('b', '', String(v))); return c; };
  box.append(chip('Alert', tx));
  if (amount != null) box.append(chip('Amount', Number(amount).toLocaleString()));
  if (score != null) box.append(chip('Alert score', score));
  box.append(chip('Steps', stepCount), chip('Evidence', nEv), chip('Patterns', nPa));
  if (rep) { const r = el('span', 'chip rec ' + rep.recommendation); r.append(el('em', '', 'Recommendation'), el('b', '', `${rep.recommendation} · ${Number(rep.confidence).toFixed(2)}`)); box.append(r); }
  if (meta.recorded) box.append(el('span', 'chip src', `recorded run${meta.model ? ' · ' + meta.model : ''}`));
  else if (meta.model) box.append(el('span', 'chip src', meta.model));
  box.classList.remove('hidden');
}

/* ---------- paced replay of a finished investigation ---------- */
function stopPlayer() { if (player) { clearTimeout(player.timer); player = null; } if ($('skip')) $('skip').disabled = true; }

async function completeReplay(p) {
  if (player !== p) return;
  clearTimeout(p.timer);
  const { tr, byStep, inv } = p;
  for (let j = p.i; j < tr.steps.length; j++) { const s = tr.steps[j]; addStep(s); (byStep[s.id] || []).forEach(([k, f]) => addFinding(k, f)); }
  p.i = tr.steps.length;
  applyGraph(fullGraph, Infinity);
  player = null; $('skip').disabled = true; $('replay').disabled = false;
  setStatus(tr.status === 'finished' ? `Finished. ${tr.summary || ''}` : tr.status === 'failed' ? `Investigation failed: ${tr.summary || ''}` : `Stopped (${tr.status}). ${tr.summary || ''}`, tr.status === 'finished' ? 'ok' : 'err');
  const rid = ((pastMeta[inv] || {}).reports || [])[0];
  let rep = null;
  if (rid) { try { rep = await api('/api/reports/' + rid); currentReport = rep; renderReport(rep); } catch (e) { /* none */ } }
  markFinished(tr.status);
  if (rep) $('make-report').textContent = 'Regenerate report';
  await showSummary(inv, rep);
}

/* a run is finished: unlock the "Continue to report" step */
function markFinished(status) {
  runFinished = status !== 'failed' && status !== 'running';
  $('to-report').classList.toggle('disabled', !runFinished);
  $('make-report').disabled = !runFinished || locked();
  updateNav();
}

async function playRun(inv, instant) {
  resetView(); currentInv = inv; banner('');
  currentTx = (pastMeta[inv] || {}).tx_id || currentTx;
  $('case-title').textContent = currentTx ? `${currentTx} · ${inv}` : inv;
  $('to-report').href = `#/report/${inv}`; updateNav();
  setStatus(`Loading ${inv} from the graph…`);
  let tr, g;
  try { [tr, g] = await Promise.all([api(`/api/investigations/${inv}/trace`), api(`/api/investigations/${inv}/graph`)]); }
  catch (e) { setStatus(e.message, 'err'); banner(e.message); return; }
  await loadRecall(inv);
  if (tr.status === 'running') { setStatus(`${inv} is still running…`); openStream(inv); refreshGraph(); return; }
  fullGraph = g;
  const byStep = {};
  tr.evidence.forEach((f) => (byStep[f.step] = byStep[f.step] || []).push(['evidence', f]));
  tr.patterns.forEach((f) => (byStep[f.step] = byStep[f.step] || []).push(['pattern', f]));
  const p = player = { timer: null, i: 0, tr, byStep, inv };
  $('replay').disabled = false; $('skip').disabled = false;
  const tick = () => {
    if (player !== p) return;
    if (p.i >= tr.steps.length) { completeReplay(p); return; }
    const s = tr.steps[p.i++], fs = byStep[s.id] || [];
    addStep(s); fs.forEach(([k, f]) => addFinding(k, f));
    applyGraph(fullGraph, s.seq);
    setStatus(`Replaying ${inv} · step ${s.seq} of ${tr.steps.length}`);
    p.timer = setTimeout(tick, fs.length || WRITERS.has(s.tool) ? PACE.write : PACE.step);
  };
  if (instant) { completeReplay(p); return; }
  setStatus(`Replaying ${inv} · ${tr.steps.length} steps`);
  tick();
}

/* ---------- actions ---------- */
/* start a live investigation from an alert card; on success the router opens the investigation page and streams it */
async function run(tx, btn) {
  if (running) return;
  banner('');
  running = true; applyLock();
  const label = btn.textContent; btn.textContent = 'Starting the Investigator…';
  try {
    const r = await api('/api/investigations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tx_id: tx }) });
    pastMeta[r.id] = { id: r.id, tx_id: tx }; currentTx = tx;
    running = false; applyLock();
    location.hash = `#/case/${r.id}`;
  } catch (e) {
    running = false; applyLock(); btn.textContent = label;
    banner('Could not start the investigation.\n' + e.message);
  }
}

async function loadRecall(inv) {
  try {
    const rc = await api(`/api/investigations/${inv}/memory`);
    const box = $('recalled');
    if (rc.recalled && rc.recalled.length) {
      box.innerHTML = ''; box.append(el('b', '', 'Recalled prior cases (memory)'));
      rc.recalled.forEach((c) => box.append(el('div', '', '• ' + c)));
      box.classList.remove('hidden');
    }
  } catch (e) { /* memory is optional */ }
}

async function makeReport() {
  const btn = $('make-report'); btn.disabled = true; btn.textContent = 'Reporter running (separate process)…';
  $('report').className = 'report muted'; $('report').textContent = 'Reading the evidence trail from the graph and drafting the report…';
  try {
    const rep = await api(`/api/investigations/${currentInv}/report`, { method: 'POST' });
    currentReport = rep; renderReport(rep); await refreshGraph(); await loadPast(); await showSummary(currentInv, rep);
  } catch (e) { $('report').className = 'report'; $('report').textContent = e.message; banner(e.message); }
  btn.textContent = 'Regenerate report'; btn.disabled = false;
}

function renderReport(rep) {
  const md = rep.markdown.replace(/\[((?:EV|PAT|POL|ACC|TX|DEV|CUST|INV)-[A-Za-z0-9][A-Za-z0-9\-]*)\]/g, '[$1](#cite-$1)');
  const html = DOMPurify.sanitize(marked.parse(md), { ADD_ATTR: ['href'] });
  const box = $('report'); box.className = 'report'; box.innerHTML = html;
  box.querySelectorAll('a[href^="#cite-"]').forEach((a) => {
    const id = a.getAttribute('href').slice(6); a.className = 'cite'; a.textContent = id; a.removeAttribute('href');
    a.onclick = (ev) => { ev.preventDefault(); focusNode(id); };
  });
  const rec = el('span', 'rec ' + rep.recommendation, rep.recommendation); box.prepend(rec);
  $('dl-md').href = `/api/reports/${rep.id}/download?format=md`; $('dl-json').href = `/api/reports/${rep.id}/download?format=json`;
  ['dl-md', 'dl-json'].forEach((i) => $(i).classList.remove('hidden'));
}

async function loadPast() {
  try {
    const list = await api('/api/investigations');
    pastMeta = {};
    list.forEach((i) => { pastMeta[i.id] = i; });
  } catch (e) { /* ignore */ }
}

/* ---------- left rail: alerts ---------- */
const fmtDate = (ts) => { try { return new Date(ts > 1e11 ? ts : ts * 1000).toISOString().slice(0, 10); } catch (e) { return ''; } };

async function loadAlerts() {
  const box = $('alerts');
  let list;
  try { list = await api('/api/alerts'); } catch (e) { box.innerHTML = ''; box.append(el('div', 'muted', e.message)); return; }
  box.innerHTML = '';
  if (!list.length) { box.append(el('div', 'muted', 'No alerted transactions. The graph may be empty' + (readonly ? '.' : ': use "Reset data" in the footer to seed it.'))); return; }
  list.forEach((a) => {
    const inv = a.investigation, canRun = !locked();
    const card = el('div', 'alert' + (a.tx_id === 'TX-FLAGSHIP' ? ' featured' : '') + (a.tx_id === currentTx && currentInv ? ' active' : '') + (!inv && !canRun ? ' locked' : ''));
    const top = el('div', 'top'); top.append(el('span', 'id', a.tx_id));
    const amt = el('span', 'amt', Number(a.amount).toLocaleString(undefined, { maximumFractionDigits: 0 }) + ' '); amt.append(el('small', '', a.currency || 'USD')); top.append(amt);
    const meta = el('div', 'meta'); meta.append(el('span', '', `score ${a.score}`), el('span', '', a.channel || ''), el('span', '', fmtDate(a.ts)));
    const foot = el('div', 'foot');
    const tag = !inv ? (canRun ? el('span', 'tag-s', 'Not investigated') : el('span', 'tag-s', 'Needs a live run'))
      : inv.status === 'finished' ? el('span', 'tag-s done', inv.recorded ? 'Recorded run' : inv.has_report ? 'Report ready' : 'Investigated')
      : el('span', 'tag-s run', inv.status === 'running' ? 'Running…' : inv.status);
    if (a.tx_id === 'TX-FLAGSHIP') { const w = el('span'); w.style.display = 'flex'; w.style.gap = '6px'; w.append(el('span', 'tag-s feat', 'Featured'), tag); foot.append(w); } else foot.append(tag);
    if (inv) { const b = el('button', 'primary', inv.recorded ? '▶ Watch' : 'Open'); b.onclick = (e) => { e.stopPropagation(); location.hash = `#/case/${inv.id}`; }; foot.append(b); }
    else if (canRun) { const b = el('button', 'primary run-btn', 'Investigate'); b.onclick = (e) => { e.stopPropagation(); run(a.tx_id, b); }; foot.append(b); }
    card.onclick = () => { if (inv) location.hash = `#/case/${inv.id}`; else if (!canRun) banner('This alert has no recording. Live runs are off in the public demo.', true); };
    card.append(top, meta, foot);
    box.append(card);
  });
  applyLock();
}

/* ---------- one page, three states (the hash keeps links and the back button working) ---------- */
let currentView = 'alerts';
function updateNav() {
  const v = currentView, has = !!currentInv;
  const set = (id, active, done) => { $(id).className = 'st' + (active ? ' active' : '') + (done ? ' done' : ''); };
  set('st-alerts', v === 'alerts', has && v !== 'alerts');
  set('st-case', v === 'case', has && v === 'report');
  set('st-report', v === 'report', false);
  document.querySelectorAll('.alert').forEach((c) => c.classList.toggle('active', has && c.querySelector('.id').textContent === currentTx));
}
function setTab(t) {
  $('tab-steps').classList.toggle('active', t === 'steps'); $('tab-report').classList.toggle('active', t === 'report');
  $('pane-steps').classList.toggle('hidden', t !== 'steps'); $('pane-report').classList.toggle('hidden', t !== 'report');
}
function showView(v) {
  currentView = v;
  $('empty').classList.toggle('hidden', v !== 'alerts');
  if (v === 'case') setTab('steps'); else if (v === 'report') setTab('report');
  updateNav();
  if (cy) { cy.resize(); if (cy.nodes().length) cy.fit(undefined, 30); }
}
async function route() {
  const [, view, id] = (location.hash || '#/alerts').split('/');
  if (view === 'case' && id) {
    showView('case');
    if (currentInv !== id) await playRun(id);
    loadAlerts();
  } else if (view === 'report' && id) {
    showView('report');
    if (currentInv !== id) await playRun(id, true);
    loadAlerts();
    if (!currentReport && runFinished) {
      $('report').className = 'report muted';
      $('report').textContent = locked() ? 'No report was recorded for this investigation.' : 'No report yet. Press "Generate report": the Reporter, a separate process, will read the evidence from the graph and write it.';
    }
  } else {
    showView('alerts'); loadAlerts();
  }
}

async function resetData() {
  if (!confirm('Drop and reseed the demo graph? All investigations and reports will be erased.')) return;
  const b = $('reset'); b.disabled = true; b.textContent = 'Resetting…';
  try { const r = await api('/api/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ size: 'demo' }) }); resetView(); currentInv = null; setStatus(r.message, 'ok'); banner(''); location.hash = '#/alerts'; }
  catch (e) { setStatus(e.message, 'err'); banner(e.message); }
  b.disabled = false; b.textContent = 'Reset data'; await health(); loadPast();
}

window.addEventListener('DOMContentLoaded', async () => {
  initGraph();
  $('tab-steps').onclick = () => { if (currentInv) location.hash = `#/case/${currentInv}`; else setTab('steps'); };
  $('tab-report').onclick = () => { if (currentInv) location.hash = `#/report/${currentInv}`; else setTab('report'); };
  $('make-report').onclick = makeReport; $('reset').onclick = resetData;
  $('replay').onclick = () => { if (currentInv) playRun(currentInv); };
  $('unlock').onclick = async () => { await toggleAdmin(); if (currentView === 'alerts') loadAlerts(); };
  $('skip').onclick = () => { if (player) completeReplay(player); };
  window.addEventListener('hashchange', route);
  await health();
  if (adminToken) { try { const r = await fetch('/api/admin/check', { headers: { 'X-Admin-Token': adminToken } }); if (!(await r.json()).ok) { adminToken = null; sessionStorage.removeItem('vt_admin'); await health(); } } catch (e) { /* offline */ } }
  await loadPast();
  await route();
  setInterval(health, 20000);
});
