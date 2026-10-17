/* VedaTrace demo UI. Everything shown comes from the API, which reads FalkorDB. No mock data. */
const $ = (id) => document.getElementById(id);
const COLORS = { Account: '#4aa3ff', Transaction: '#7d8590', Device: '#e3a008', Customer: '#56d4dd', Evidence: '#f0883e', Pattern: '#f85149', Policy: '#a371f7' };
const SHAPES = { Account: 'ellipse', Transaction: 'diamond', Device: 'round-rectangle', Customer: 'ellipse', Evidence: 'hexagon', Pattern: 'star', Policy: 'octagon' };
const SIZES = { Account: 30, Transaction: 14, Device: 24, Customer: 20, Evidence: 28, Pattern: 38, Policy: 30 };
const WRITERS = new Set(['write_evidence', 'write_pattern']);

let cy, es = null, currentInv = null, currentReport = null, graphTimer = null, running = false, stepCount = 0, readonly = false;

/* ---------- helpers ---------- */
async function api(path, opts) {
  let r;
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
function setStatus(msg, cls) { const s = $('case-status'); s.textContent = msg; s.className = 'status ' + (cls || ''); }
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
    readonly = !!h.readonly;
    if (readonly) { ['run', 'reset', 'make-report'].forEach((i) => { $(i).disabled = true; $(i).title = 'Read-only replay deployment'; }); }
    const problems = [];
    if (!h.falkordb.ok) problems.push('FalkorDB is not reachable. Start it with: docker start vedatrace-falkordb  (or docker run -p 6379:6379 -p 3000:3000 falkordb/falkordb)');
    else if (h.falkordb.nodes === 0) problems.push('The graph is empty. Press "Reset demo data" to seed it.');
    if (readonly) problems.push('Read-only replay of a recorded investigation. Every node, step and citation below is read from FalkorDB; run locally with an LLM key to investigate live.');
    else if (!h.llm.ok) problems.push('Live runs are disabled (' + (h.llm.detail || 'LLM not ready') + '). Recorded runs can still be replayed from the "Previous investigations" list.');
    banner(problems.join('\n'), !problems.some((p) => p.startsWith('FalkorDB')));
    return h;
  } catch (e) { banner(e.message); }
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
  const lg = $('legend');
  Object.entries(COLORS).forEach(([k, c]) => { const s = el('span'); const i = el('i'); i.style.background = c; s.append(i, k); lg.append(s); });
}

function shortLabel(n) { return n.id.replace(/^(ACC|TX|DEV|CUST)-/, '$1·'); }

async function refreshGraph(focusLatest) {
  if (!currentInv) return;
  let g;
  try { g = await api(`/api/investigations/${currentInv}/graph`); } catch (e) { banner(e.message); return; }
  const have = new Set(cy.nodes().map((n) => n.id()));
  const add = [];
  g.nodes.forEach((n) => {
    if (!have.has(n.id)) add.push({ group: 'nodes', data: { ...n, short: shortLabel(n), props: undefined }, classes: 'fresh' });
    else { const c = cy.getElementById(n.id); c.data('touched', n.touched); }
  });
  const haveE = new Set(cy.edges().map((e) => `${e.data('source')}|${e.data('target')}|${e.data('type')}`));
  g.edges.forEach((e) => { if (!haveE.has(`${e.source}|${e.target}|${e.type}`)) add.push({ group: 'edges', data: { ...e, id: `e:${e.source}|${e.target}|${e.type}` } }); });
  if (add.length) {
    const eles = cy.add(add);
    eles.nodes().style('opacity', 0).animate({ style: { opacity: 1 } }, { duration: 500 });
    cy.layout({ name: 'cose', animate: true, animationDuration: 600, randomize: false, fit: true, padding: 30, nodeRepulsion: 9000, idealEdgeLength: 55, numIter: 600 }).run();
    setTimeout(() => cy.nodes('.fresh').removeClass('fresh'), 2200);
  }
  $('graph-note').textContent = `${g.nodes.length} nodes · ${g.edges.length} edges${g.capped ? ' (capped)' : ''}`;
}
function scheduleGraph() { clearTimeout(graphTimer); graphTimer = setTimeout(() => refreshGraph(), 700); }

async function focusNode(id) {
  let n = cy.getElementById(id);
  if (!n || n.empty()) { await refreshGraph(); n = cy.getElementById(id); }
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

/* ---------- live stream ---------- */
function addStep(s) {
  stepCount = Math.max(stepCount, s.seq);
  $('step-count').textContent = `${stepCount} step${stepCount === 1 ? '' : 's'}`;
  const li = el('li', (s.ok ? '' : 'err ') + (WRITERS.has(s.tool) ? 'write' : ''));
  const args = Object.entries(s.args || {}).map(([k, v]) => `${k}=${Array.isArray(v) ? `[${v.length}]` : String(v).slice(0, 28)}`).join(', ');
  const t = el('div', 't'); const b = el('b', '', String(s.seq).padStart(2, '0')); t.append(b, `${s.tool}(${args})${s.ok ? '' : '  ⚠ rejected'}`);
  li.append(t, el('div', 'why', s.reason || ''), el('div', 'res', s.summary || ''));
  li.onclick = () => { const a = Object.values(s.args || {}).flat().find((v) => typeof v === 'string' && /^(ACC|TX|DEV)-/.test(v)); if (a) focusNode(a); };
  $('steps').append(li); $('steps').scrollTop = 1e9;
}
function addFinding(kind, f) {
  const d = el('div', 'finding ' + (kind === 'pattern' ? 'pattern' : f.kind === 'BENIGN_EXPLANATION' ? 'benign' : ''));
  const b = el('b', '', `${f.id} · ${kind === 'pattern' ? f.type : f.kind}`);
  d.append(b, el('div', '', f.summary)); d.onclick = () => focusNode(f.id);
  $('findings').append(d);
}

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
    $('run').disabled = readonly;
    setStatus(d.status === 'finished' ? `Finished. ${d.summary || ''}` : d.status === 'failed' ? `Investigation failed: ${d.summary || ''}` : `Stopped (${d.status}). ${d.summary || ''}`, d.status === 'finished' ? 'ok' : 'err');
    $('make-report').disabled = d.status === 'failed' || readonly;
    await refreshGraph();
    health();
  });
}

function resetView() {
  if (es) { es.close(); es = null; }
  $('steps').innerHTML = ''; $('findings').innerHTML = ''; stepCount = 0; $('step-count').textContent = '0 steps';
  cy.elements().remove(); $('graph-note').textContent = ''; $('inspector').classList.add('hidden');
  $('report').className = 'report muted'; $('report').textContent = 'The Reporter runs as a separate process. It reads only the graph, never the Investigator\'s memory.';
  ['dl-md', 'dl-json'].forEach((i) => $('' + i).classList.add('hidden'));
  $('make-report').disabled = true; currentReport = null; $('recalled').classList.add('hidden');
}

/* ---------- actions ---------- */
async function run() {
  if (running) return;
  resetView(); banner('');
  const tx = $('tx').value.trim();
  running = true; $('run').disabled = true; setStatus('Starting the Investigator process…');
  try {
    const r = await api('/api/investigations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tx_id: tx }) });
    currentInv = r.id; setStatus(`${r.id} running…`);
    await loadRecall(r.id);
    openStream(r.id); refreshGraph();
  } catch (e) { running = false; $('run').disabled = false; setStatus(e.message, 'err'); banner(e.message); }
}

async function loadRecall(inv) {
  try {
    const rc = await api(`/api/investigations/${inv}/memory`);
    const box = $('recalled');
    if (rc.recalled && rc.recalled.length) {
      box.innerHTML = ''; box.append(el('b', '', 'Recalled prior cases'));
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
    currentReport = rep; renderReport(rep); await refreshGraph();
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
    const sel = $('past'); sel.length = 1;
    list.forEach((i) => { const o = el('option', '', `${i.id} · ${i.tx_id} · ${i.status}${i.reports.length ? ' · report' : ''}${i.recorded ? ' · recorded run' : ''}`); o.value = i.id; o.dataset.report = i.reports[0] || ''; sel.append(o); });
  } catch (e) { /* ignore */ }
}

async function openPast() {
  const sel = $('past'); const id = sel.value; if (!id) return;
  const rpt = sel.selectedOptions[0].dataset.report;
  resetView(); currentInv = id; $('make-report').disabled = readonly;
  setStatus(`Replaying ${id} from the graph…`);
  openStream(id); await loadRecall(id); await refreshGraph();
  if (rpt) { try { const rep = await api('/api/reports/' + rpt); currentReport = rep; renderReport(rep); } catch (e) { /* none */ } }
}

async function resetData() {
  if (!confirm('Drop and reseed the demo graph? All investigations and reports will be erased.')) return;
  const b = $('reset'); b.disabled = true; b.textContent = 'Resetting…';
  try { const r = await api('/api/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ size: 'demo' }) }); resetView(); currentInv = null; setStatus(r.message, 'ok'); banner(''); }
  catch (e) { setStatus(e.message, 'err'); banner(e.message); }
  b.disabled = false; b.textContent = 'Reset demo data'; await health(); loadPast();
}

window.addEventListener('DOMContentLoaded', async () => {
  initGraph();
  $('run').onclick = run; $('make-report').onclick = makeReport; $('reset').onclick = resetData; $('past').onchange = openPast;
  $('tx').addEventListener('keydown', (e) => { if (e.key === 'Enter') run(); });
  await health(); await loadPast();
  if (readonly && $('past').options.length > 1) { $('past').selectedIndex = 1; openPast(); }  // land on the recorded run
  setInterval(health, 20000);
});
