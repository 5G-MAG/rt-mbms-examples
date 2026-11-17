/* 5G Broadcast Positioning Emulator: the page of bps.dashboard. The server holds the state; this page sets up a scenario, starts and watches a run and reads the stored results. */
'use strict';
const $ = id => document.getElementById(id);
const fmt = (v, n = 1) => (v === null || v === undefined || Number.isNaN(v)) ? '-' : v.toFixed(n);
const COLORS = ['#00a0d2', '#d9534f', '#1e8a4c', '#b9770e', '#8250df', '#0a7e8c', '#e67e22', '#64748b', '#c2185b', '#2e7d32', '#5c6bc0', '#8d6e63'];
let PRESETS = [], LAB = null, PLATFORM = { mbs_demo_up: false, demo_up: false }, RUN = { state: 'idle' };

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

/* theme toggle, as in the other 5G-MAG tools */
(function () {
  const btn = $('theme-toggle'), KEY = '5g-broadcast-positioning-emulator-theme';
  function apply(t) {
    if (t === 'dark') document.documentElement.setAttribute('data-theme', 'dark'); else document.documentElement.removeAttribute('data-theme');
    btn.innerHTML = t === 'dark' ? '&#9728;&nbsp;Light mode' : '&#9790;&nbsp;Dark mode'; btn.setAttribute('aria-pressed', t === 'dark');
  }
  apply(document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light');
  btn.addEventListener('click', () => { const n = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark'; try { localStorage.setItem(KEY, n); } catch (e) {} apply(n); redrawAll(); });
})();
function toast(msg, kind) { const el = $('toast'); el.textContent = msg; el.className = kind || ''; el.hidden = false; clearTimeout(window._tt); window._tt = setTimeout(() => el.hidden = true, 5000); }

/* tabs */
function showTab(t) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === t));
  document.querySelectorAll('.tab-panel').forEach(p => p.hidden = p.id !== 'tab-' + t);
  if (t === 'results') loadResults();
  if (t === 'live') renderLive();
}
document.querySelectorAll('.tab-btn').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));

/* small SVG plotting helpers */
function niceStep(span) { const raw = span / 5, b = Math.pow(10, Math.floor(Math.log10(raw))); for (const m of [1, 2, 5, 10]) if (m * b >= raw) return m * b; return b; }
function cssv(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
function plot(svg, W, H, xr, yr, xlab, ylab, noXTicks) {
  const M = 46, R = 14, T = 12, B = 38, sx = v => M + (v - xr[0]) / (xr[1] - xr[0]) * (W - M - R), sy = v => H - B - (v - yr[0]) / (yr[1] - yr[0]) * (H - B - T);
  let s = `<rect x="${M}" y="${T}" width="${W - M - R}" height="${H - T - B}" fill="none" stroke="currentColor" opacity=".35"/>`;
  const xs = niceStep(xr[1] - xr[0]), ys = niceStep(yr[1] - yr[0]);
  for (let v = Math.ceil(xr[0] / xs) * xs; v <= xr[1] + 1e-9; v += xs) s += `<line x1="${sx(v)}" x2="${sx(v)}" y1="${T}" y2="${H - B}" stroke="currentColor" opacity=".12"/>${noXTicks ? '' : `<text x="${sx(v)}" y="${H - B + 15}" text-anchor="middle">${+v.toFixed(6)}</text>`}`;
  for (let v = Math.ceil(yr[0] / ys) * ys; v <= yr[1] + 1e-9; v += ys) s += `<line x1="${M}" x2="${W - R}" y1="${sy(v)}" y2="${sy(v)}" stroke="currentColor" opacity=".12"/><text x="${M - 6}" y="${sy(v) + 3}" text-anchor="end">${+v.toFixed(6)}</text>`;
  s += `<text x="${(M + W - R) / 2}" y="${H - 6}" text-anchor="middle">${xlab}</text><text transform="translate(12 ${H / 2}) rotate(-90)" text-anchor="middle">${ylab}</text>`;
  return { sx, sy, head: s };
}
const accent = () => cssv('--accent') || '#00a0d2';
function symRange(vals, floor) { const m = Math.max(floor, ...vals.map(Math.abs)) * 1.15, st = niceStep(2 * m); const l = Math.ceil(m / st) * st; return [-l, l]; }

function drawScatter(svg, fixes, rms) {
  const pts = fixes.map(f => [f.east, f.north]), r = symRange(pts.flat(), Math.max(rms || 0, 4)), p = plot(svg, 460, 360, r, r, 'east error (m)', 'north error (m)');
  let s = p.head + `<path d="M${p.sx(0)} ${p.sy(r[0])} V${p.sy(r[1])} M${p.sx(r[0])} ${p.sy(0)} H${p.sx(r[1])}" stroke="currentColor" opacity=".3"/>`;
  pts.forEach((q, i) => s += `<circle cx="${p.sx(q[0])}" cy="${p.sy(q[1])}" r="${i === pts.length - 1 ? 4.5 : 2.6}" fill="${accent()}" opacity="${i === pts.length - 1 ? 1 : .5}"/>`);
  if (rms) s += `<ellipse cx="${p.sx(0)}" cy="${p.sy(0)}" rx="${p.sx(rms) - p.sx(0)}" ry="${p.sy(0) - p.sy(rms)}" fill="none" stroke="#d9534f" stroke-dasharray="4 3"/><text x="${p.sx(0) + 6}" y="${p.sy(rms) - 4}" style="fill:#d9534f">rms error</text>`;
  svg.innerHTML = s;
}
function drawTime(svg, fixes, total) {
  const top = Math.max(8, ...fixes.map(f => Math.max(f.err, f.claimed))) * 1.1, st = niceStep(top), yr = [0, Math.ceil(top / st) * st], p = plot(svg, 460, 360, [0, total], yr, 'time (s)', 'horizontal error (m)');
  let s = p.head;
  if (fixes.length > 1) {
    s += `<polyline fill="none" stroke="currentColor" opacity=".45" stroke-dasharray="4 3" points="${fixes.map(f => p.sx(f.t) + ',' + p.sy(f.claimed)).join(' ')}"/>`;
    s += `<polyline fill="none" stroke="${accent()}" stroke-width="1.8" points="${fixes.map(f => p.sx(f.t) + ',' + p.sy(f.err)).join(' ')}"/>`;
  }
  s += `<text x="${p.sx(0) + 8}" y="26" style="fill:${accent()}">error</text><text x="${p.sx(0) + 58}" y="26">claimed (dashed)</text>`;
  svg.innerHTML = s;
}
function drawRstd(svg, fixes) {
  const cells = ['2', '3', '4'], all = fixes.flatMap(f => cells.map(c => f.rstd[c])), r = symRange(all, 20), p = plot(svg, 460, 360, [0, 4], r, 'cell', 'time-difference error (ns)', true);
  let s = p.head + `<line x1="${p.sx(0)}" x2="${p.sx(4)}" y1="${p.sy(0)}" y2="${p.sy(0)}" stroke="currentColor" opacity=".4"/>`;
  cells.forEach((c, k) => {
    const x = k + 1; s += `<text x="${p.sx(x)}" y="${360 - 22}" text-anchor="middle">cell ${c}</text>`;
    fixes.forEach((f, i) => s += `<circle cx="${p.sx(x) + ((i * 37) % 21 - 10)}" cy="${p.sy(f.rstd[c])}" r="2.4" fill="${COLORS[k]}" opacity=".5"/>`);
    if (fixes.length) { const m = fixes.reduce((a, f) => a + f.rstd[c], 0) / fixes.length; s += `<line x1="${p.sx(x) - 20}" x2="${p.sx(x) + 20}" y1="${p.sy(m)}" y2="${p.sy(m)}" stroke="${COLORS[k]}" stroke-width="2.5"/>`; }
  });
  svg.innerHTML = s;
}
function drawCdf(svg, runs) {
  const top = Math.max(10, ...runs.flatMap(r => r.fixes.map(f => f.err))), st = niceStep(top), xr = [0, Math.ceil(top / st) * st], p = plot(svg, 640, 360, xr, [0, 1], 'horizontal error (m)', 'share of fixes');
  let s = p.head;
  runs.forEach(r => { const e = r.fixes.map(f => f.err).sort((a, b) => a - b); if (!e.length) return;
    s += `<polyline fill="none" stroke="${r.color}" stroke-width="1.9" points="${p.sx(0)},${p.sy(0)} ${e.map((v, i) => p.sx(v) + ',' + p.sy((i + 1) / e.length)).join(' ')}"/>`; });
  svg.innerHTML = s;
}

/* scenario form */
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
let problems = [];
function buildCellTable() {
  const tb = $('cellTable').querySelector('tbody'); tb.innerHTML = '';
  for (let i = 0; i < 4; i++) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td><strong>${i + 1}</strong> ${i === 0 ? '<span class="badge badge-neutral" title="Transmitted by the real transmitter; the reference cell of the positioning method">reference</span>' : '<span class="badge badge-warning">emulated</span>'}</td>` +
      [0, 1, 2].map(k => `<td class="num"><input type="number" step="any" data-c="${i}" data-k="${k}" style="max-width:110px"></td>`).join('') +
      (i === 0 ? '<td></td><td></td><td></td><td></td>' : ['rb', 'rd', 'pw', 'cf'].map(k => `<td class="num"><input type="number" step="any" data-e="${i + 1}" data-k="${k}" style="max-width:110px"></td>`).join(''));
    tb.appendChild(tr);
  }
  tb.querySelectorAll('input').forEach(el => el.addEventListener('input', updatePower));
}
function loadPreset(name) {
  const p = PRESETS.find(x => x.name === name), sc = p.scenario, r = p.resolved;
  $('f-name').value = p.name; $('f-seconds').value = p.seconds;
  r.ue.forEach((v, k) => $('ue-' + k).value = v);
  r.cells.forEach((c, i) => c.forEach((v, k) => document.querySelector(`[data-c="${i}"][data-k="${k}"]`).value = v));
  for (const c of [2, 3, 4]) {
    document.querySelector(`[data-e="${c}"][data-k="rb"]`).value = r.rtd[c][0]; document.querySelector(`[data-e="${c}"][data-k="rd"]`).value = r.rtd[c][1];
    document.querySelector(`[data-e="${c}"][data-k="pw"]`).value = (sc.power_db && sc.power_db[c] !== undefined) ? sc.power_db[c] : '';
    document.querySelector(`[data-e="${c}"][data-k="cf"]`).value = (sc.cfo_hz && sc.cfo_hz[c]) || '';
  }
  $('f-ple').value = sc.path_loss_exponent ?? ''; $('f-snr').value = sc.snr_db ?? ''; $('f-mon').value = sc.monitor_noise_ns ?? 20; $('f-cp').value = sc.cp || 'extended';
  $('presetNote').textContent = p.note; view = null; updatePower();
}
function formScenario() {
  const ue = [0, 1, 2].map(k => Number($('ue-' + k).value));
  const cs = [0, 1, 2, 3].map(i => [0, 1, 2].map(k => Number(document.querySelector(`[data-c="${i}"][data-k="${k}"]`).value)));
  const sc = { name: $('f-name').value }, e = (c, k) => document.querySelector(`[data-e="${c}"][data-k="${k}"]`).value;
  sc.ue_enu = ue; sc.cells_enu = cs; sc.rtd = {}; sc.power_db = {}; sc.cfo_hz = {};
  for (const c of [2, 3, 4]) { sc.rtd[c] = [Number(e(c, 'rb')), Number(e(c, 'rd'))]; if (e(c, 'pw') !== '') sc.power_db[c] = Number(e(c, 'pw')); if (e(c, 'cf') !== '') sc.cfo_hz[c] = Number(e(c, 'cf')); }
  if (!Object.keys(sc.power_db).length) delete sc.power_db; if (!Object.keys(sc.cfo_hz).length) delete sc.cfo_hz;
  if ($('f-ple').value !== '') sc.path_loss_exponent = Number($('f-ple').value);
  if ($('f-snr').value !== '') sc.snr_db = Number($('f-snr').value);
  sc.monitor_noise_ns = Number($('f-mon').value); sc.cp = $('f-cp').value; sc.seconds = Number($('f-seconds').value);
  return sc;
}
function resolvedPower(sc) {
  const out = {};
  for (const c of [2, 3, 4]) {
    if (sc.power_db && sc.power_db[c] !== undefined) out[c] = sc.power_db[c];
    else if (sc.path_loss_exponent) out[c] = -10 * sc.path_loss_exponent * Math.log10(Math.max(dist(sc.cells_enu[c - 1], sc.ue_enu), 1) / Math.max(dist(sc.cells_enu[0], sc.ue_enu), 1));
    else out[c] = -6;
  }
  return out;
}
function updateStart() {
  const running = RUN.state === 'running', mbs = PLATFORM.mbs_demo_up, demo = PLATFORM.demo_up && !running;
  const b = $('startBtn'); b.disabled = problems.length > 0 || running || mbs || demo;
  b.title = running ? 'A run is in progress' : mbs ? 'The MBS demo is up' : demo ? 'A positioning demo is already up' : '';
  const banner = $('platformBanner');
  banner.hidden = !(mbs || demo);
  banner.textContent = mbs ? 'The MBS demo is up. It shares the radio ports and the memory with a run: stop it before starting one.' : demo ? 'A positioning demo is already up (not started from here). Stop it with ./demo down before starting a run.' : '';
}
function updatePower() {
  const sc = formScenario(), p = resolvedPower(sc); problems = [];
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$/.test(sc.name)) problems.push('the name is 1 to 60 letters, digits, dots, dashes or underscores');
  if (sc.ue_enu.some(Number.isNaN) || sc.cells_enu.flat().some(Number.isNaN)) problems.push('a position is empty');
  if (!(sc.monitor_noise_ns > 0)) problems.push('monitor noise must be positive');
  if (sc.path_loss_exponent !== undefined && !(sc.path_loss_exponent > 0)) problems.push('path loss exponent must be positive');
  if (!(sc.seconds >= 20)) problems.push('the UE duration is at least 20 s');
  $('powerTiles').innerHTML = [2, 3, 4].map(c => {
    const d = dist(sc.cells_enu[c - 1], sc.ue_enu), weak = p[c] < -11;
    return `<div class="stat-tile"><div class="label">Cell ${c}</div><div class="val">${fmt(p[c])} dB</div><div class="sub">${len(d)} from the UE${weak ? ' &middot; <span style="color:var(--warning)">below -11 dB the modem may not measure it</span>' : ''}</div></div>`;
  }).join('');
  const line = $('checkLine');
  line.className = 'status-line ' + (problems.length ? 'status-error' : 'status-success');
  line.textContent = problems.length ? 'Not valid: ' + problems.join(', ') : 'Scenario valid. One run takes about ' + Math.round((60 + 20 + sc.seconds) / 60 * 10) / 10 + ' minutes.';
  $('jsonBox').textContent = JSON.stringify(sc, null, 1);
  updateStart(); drawMap();
}
$('preset').addEventListener('change', e => loadPreset(e.target.value));
$('jsonBtn').addEventListener('click', () => { $('jsonBox').hidden = !$('jsonBox').hidden; });
['f-name', 'f-seconds', 'ue-0', 'ue-1', 'ue-2', 'f-ple', 'f-snr', 'f-mon', 'f-cp'].forEach(id => $(id).addEventListener('input', updatePower));
$('startBtn').addEventListener('click', async () => {
  try { await api('/api/run', { scenario: formScenario() }); toast('Run started'); liveKey = null; await poll(); showTab('live'); }
  catch (e) { toast(e.message, 'error'); }
});

/* layout canvas: cells and the UE in metres, dragged with the pointer */
let view = null, dragging = null, gridStep = 1000;
const len = m => Math.abs(m) >= 1000 ? +(m / 1000).toFixed(m % 1000 === 0 ? 0 : 2) + ' km' : +m.toFixed(m < 10 ? 1 : 0) + ' m';
const axisLabel = (v, st) => v === 0 ? '0' : (st >= 1000 ? +(v / 1000).toFixed(3) + ' km' : +v.toFixed(3) + ' m');
const mapSvg = () => $('layoutMap'), MW = 1100, MH = 580, MP = 50;
function mapPoints() {
  const sc = formScenario(); return { cells: sc.cells_enu.map(c => [c[0], c[1]]), ue: [sc.ue_enu[0], sc.ue_enu[1]], sc };
}
function fitView() {
  const { cells, ue } = mapPoints(), pts = cells.concat([ue]).filter(p => !p.some(Number.isNaN));
  if (!pts.length) return;
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]), cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  const sx = (Math.max(...xs) - Math.min(...xs)) / (MW - 2 * MP), sy = (Math.max(...ys) - Math.min(...ys)) / (MH - 2 * MP), mpp = Math.max(sx, sy, 0.02) * 1.25;   // metres per pixel
  view = { cx, cy, mpp };
}
const toPx = (x, y) => [MW / 2 + (x - view.cx) / view.mpp, MH / 2 - (y - view.cy) / view.mpp];
const toM = (px, py) => [view.cx + (px - MW / 2) * view.mpp, view.cy - (py - MH / 2) * view.mpp];
function drawMap() {
  if (!view) fitView(); if (!view) return;
  const { cells, ue, sc } = mapPoints(), pw = resolvedPower(sc), svg = mapSvg();
  const x0 = toM(0, 0)[0], x1 = toM(MW, 0)[0], y1 = toM(0, 0)[1], y0 = toM(0, MH)[1], st = niceStep((x1 - x0) / 6); gridStep = st;
  let s = `<rect x="0" y="0" width="${MW}" height="${MH}" fill="none" stroke="currentColor" opacity=".35"/>`;
  for (let v = Math.ceil(x0 / st) * st; v <= x1; v += st) { const px = toPx(v, 0)[0]; s += `<line x1="${px}" x2="${px}" y1="0" y2="${MH}" stroke="currentColor" opacity=".12"/><text x="${px}" y="${MH - 6}" text-anchor="middle">${axisLabel(v, st)}</text>`; }
  for (let v = Math.ceil(y0 / st) * st; v <= y1; v += st) { const py = toPx(0, v)[1]; s += `<line x1="0" x2="${MW}" y1="${py}" y2="${py}" stroke="currentColor" opacity=".12"/><text x="6" y="${py - 4}">${axisLabel(v, st)}</text>`; }
  // north arrow and scale bar
  s += `<path d="M${MW - 30} 56 V22 M${MW - 30} 22 l-5 9 M${MW - 30} 22 l5 9" stroke="currentColor" fill="none"/><text x="${MW - 30}" y="70" text-anchor="middle">N</text>`;
  const bar = niceStep(150 * view.mpp / 2) , bw = bar / view.mpp; s += `<path d="M${MW - 20 - bw} ${MH - 30} h${bw} M${MW - 20 - bw} ${MH - 35} v10 M${MW - 20} ${MH - 35} v10" stroke="currentColor"/><text x="${MW - 20 - bw / 2}" y="${MH - 38}" text-anchor="middle">${len(bar)}</text>`;
  const up = toPx(ue[0], ue[1]);
  cells.forEach((c, i) => {   // distance lines first, so the markers sit on top
    if (i === 0) return; const p = toPx(c[0], c[1]), d = Math.hypot(c[0] - ue[0], c[1] - ue[1]), weak = pw[i + 1] < -11;
    s += `<line x1="${up[0]}" y1="${up[1]}" x2="${p[0]}" y2="${p[1]}" stroke="${weak ? '#b9770e' : 'currentColor'}" stroke-dasharray="5 4" opacity=".6"/>`;
    const mx = up[0] + 0.62 * (p[0] - up[0]), my = up[1] + 0.62 * (p[1] - up[1]);
    s += `<rect x="${mx - 52}" y="${my - 11}" width="104" height="18" rx="4" fill="${cssv('--surface')}" opacity=".9"/><text x="${mx}" y="${my + 2}" text-anchor="middle" style="fill:${weak ? '#b9770e' : 'currentColor'}">${len(d)} · ${pw[i + 1].toFixed(1)} dB</text>`;
  });
  const r1 = Math.hypot(cells[0][0] - ue[0], cells[0][1] - ue[1]), p1 = toPx(cells[0][0], cells[0][1]);
  s += `<line x1="${up[0]}" y1="${up[1]}" x2="${p1[0]}" y2="${p1[1]}" stroke="${accent()}" stroke-dasharray="5 4" opacity=".8"/><text x="${(up[0] + p1[0]) / 2 - 8}" y="${(up[1] + p1[1]) / 2 + 4}" text-anchor="end" style="fill:${accent()}">${len(r1)}</text>`;
  cells.forEach((c, i) => {
    const p = toPx(c[0], c[1]), real = i === 0, col = real ? accent() : (pw[i + 1] < -11 ? '#b9770e' : cssv('--text'));
    s += `<g class="drag" data-k="cell${i}" style="cursor:grab"><circle cx="${p[0]}" cy="${p[1]}" r="22" fill="transparent"/><path d="M${p[0]} ${p[1] - 13} l11 20 h-22 z" fill="${col}"/>
          <text x="${real ? p[0] - 15 : p[0] + 15}" y="${p[1] + 5}" text-anchor="${real ? 'end' : 'start'}" style="fill:${col};font-weight:700;font-size:13px">${real ? '1 (reference)' : i + 1}</text></g>`;
  });
  s += `<g class="drag" data-k="ue" style="cursor:grab"><circle cx="${up[0]}" cy="${up[1]}" r="22" fill="transparent"/><path d="M${up[0] - 9} ${up[1]} h18 M${up[0]} ${up[1] - 9} v18" stroke="#d9534f" stroke-width="3"/><circle cx="${up[0]}" cy="${up[1]}" r="6" fill="none" stroke="#d9534f" stroke-width="2"/><text x="${up[0] + 13}" y="${up[1] - 10}" style="fill:#d9534f;font-weight:700;font-size:13px">UE</text></g>`;
  svg.innerHTML = s;
  $('mapHint').textContent = [2, 3, 4].some(c => pw[c] < -11) ? 'Amber: below -11 dB. In this lab, cell 3 at -12 dB gave almost no fixes.' : 'All emulated cells are above -11 dB.';
}
(function () {
  const svg = $('layoutMap'), clamp = v => Math.min(500, Math.max(0.02, v));
  const pos = e => { const r = svg.getBoundingClientRect(); return [(e.clientX - r.left) * MW / r.width, (e.clientY - r.top) * MH / r.height]; };
  let pan = null;
  function zoomAt(px, py, f) { const [mx, my] = toM(px, py); view.mpp = clamp(view.mpp * f); view.cx = mx - (px - MW / 2) * view.mpp; view.cy = my + (py - MH / 2) * view.mpp; drawMap(); }
  svg.addEventListener('pointerdown', e => {
    const g = e.target.closest('.drag'); svg.setPointerCapture(e.pointerId);
    if (g) { dragging = g.dataset.k; g.style.cursor = 'grabbing'; } else { dragging = 'pan'; pan = { p: pos(e), cx: view.cx, cy: view.cy }; svg.style.cursor = 'grabbing'; }
  });
  svg.addEventListener('pointermove', e => {
    if (!dragging) return;
    if (dragging === 'pan') { const q = pos(e); view.cx = pan.cx - (q[0] - pan.p[0]) * view.mpp; view.cy = pan.cy + (q[1] - pan.p[1]) * view.mpp; drawMap(); return; }
    let [x, y] = toM(...pos(e)), sn = gridStep / 10; if ($('snap').checked) { x = Math.round(x / sn) * sn; y = Math.round(y / sn) * sn; }
    x = +x.toFixed(3); y = +y.toFixed(3);
    if (dragging === 'ue') { $('ue-0').value = x; $('ue-1').value = y; }
    else { const i = dragging.slice(4); document.querySelector(`[data-c="${i}"][data-k="0"]`).value = x; document.querySelector(`[data-c="${i}"][data-k="1"]`).value = y; }
    updatePower();
  });
  svg.addEventListener('pointerup', () => { dragging = null; svg.style.cursor = 'default'; });
  svg.addEventListener('wheel', e => { e.preventDefault(); zoomAt(...pos(e), e.deltaY < 0 ? 0.8 : 1.25); }, { passive: false });
  $('zoomIn').addEventListener('click', () => zoomAt(MW / 2, MH / 2, 0.5));
  $('zoomOut').addEventListener('click', () => zoomAt(MW / 2, MH / 2, 2));
  $('fitBtn').addEventListener('click', () => { fitView(); drawMap(); });
})();

/* live run */
const PHASES = ['starting', 'warmup', 'measuring', 'stopping', 'done'];
let liveKey = null, liveFixes = [], liveInfo = { documents: 0, refusals: {}, last_refusal: null };
async function poll() {
  let st;
  try { st = await api('/api/state'); } catch (e) { return; }
  PLATFORM = st.platform; const was = RUN.state; RUN = st.run;
  if (RUN.state !== 'idle') {
    const key = RUN.started + '|' + RUN.name;
    if (key !== liveKey) { liveKey = key; liveFixes = []; $('liveLog').textContent = ''; }
    try {
      const f = await api('/api/run/fixes?after=' + liveFixes.length);
      liveFixes = liveFixes.concat(f.fixes); liveInfo = f;
      const l = await api('/api/log'), el = $('liveLog'), atEnd = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
      el.textContent = l.log; if (atEnd) el.scrollTop = el.scrollHeight;
    } catch (e) {}
  }
  if (was === 'running' && RUN.state !== 'running') {
    toast(RUN.state === 'finished' ? 'Run finished' : RUN.state === 'stopped' ? 'Run stopped' : 'Run failed: ' + (RUN.error || ''), RUN.state === 'finished' ? 'success' : 'error');
    if (!$('tab-results').hidden) loadResults();
  }
  updateStart(); renderLive();
}
function renderLive() {
  const phase = RUN.state === 'idle' ? null : (RUN.phase || 'starting');
  const idx = PHASES.indexOf(phase === 'failed' || phase === 'stopped' ? 'done' : phase);
  document.querySelectorAll('#phasePills .pill').forEach((el, k) => {
    el.classList.toggle('active', k === idx && RUN.state === 'running'); el.classList.toggle('done', idx >= 0 && (k < idx || (k === idx && RUN.state !== 'running')));
  });
  const last = $('phasePills').lastElementChild; last.textContent = RUN.state === 'failed' ? 'Failed' : RUN.state === 'stopped' ? 'Stopped' : 'Done';
  $('liveDot').className = 'tab-dot ' + (RUN.state === 'running' ? 'up' : RUN.state === 'failed' ? 'down' : '');
  $('stopBtn').disabled = RUN.state !== 'running';
  $('liveName').textContent = RUN.state === 'idle' ? '' : RUN.name + (RUN.error ? ' - ' + RUN.error : '');
  const f = liveFixes, errs = f.map(x => x.err), rms = errs.length ? Math.sqrt(errs.reduce((a, v) => a + v * v, 0) / errs.length) : null;
  const p95 = errs.length ? errs.slice().sort((a, b) => a - b)[Math.min(errs.length - 1, Math.floor(0.95 * errs.length))] : null, ratio = errs.length ? errs.reduce((a, v, i) => a + v / f[i].claimed, 0) / errs.length : null;
  const refusals = Object.values(liveInfo.refusals || {}).reduce((a, v) => a + v, 0), total = (RUN.seconds || 0) + 90;
  const names = { starting: 'Starting demo', warmup: 'Warmup', measuring: 'Measuring', stopping: 'Stopping demo', done: 'Done', failed: 'Failed', stopped: 'Stopped' };
  const tiles = [['Phase', phase ? names[phase] : 'Idle', RUN.state === 'idle' ? 'Start a run from the Scenario tab' : RUN.name], ['Elapsed', RUN.state === 'idle' ? '-' : Math.round(RUN.elapsed) + ' s', RUN.state === 'running' ? 'of about ' + total + ' s' : ''],
    ['Fixes', f.length, refusals + ' refusals' + (liveInfo.last_refusal ? ' (last: ' + liveInfo.last_refusal + ')' : '')], ['rms error', fmt(rms) + ' m', 'so far'], ['95 %', fmt(p95) + ' m', 'so far'],
    ['Real / claimed', fmt(ratio, 2), 'below 1: claim is on the safe side']];
  $('liveTiles').innerHTML = tiles.map(t => `<div class="stat-tile"><div class="label">${t[0]}</div><div class="val">${t[1]}</div><div class="sub">${t[2] || ''}</div></div>`).join('');
  $('progress').style.width = RUN.state === 'idle' ? '0%' : RUN.state === 'running' ? Math.min(99, RUN.elapsed / total * 100) + '%' : '100%';
  if ($('tab-live').hidden) return;
  drawScatter($('plotScatter'), f, rms); drawTime($('plotTime'), f, RUN.seconds || 150); drawRstd($('plotRstd'), f);
}
$('stopBtn').addEventListener('click', async () => { try { await api('/api/run/stop', {}); toast('Stopping: the demo is being brought down'); } catch (e) { toast(e.message, 'error'); } });

/* results */
const selected = new Set(), cache = {};
let RESULTS = [];
const refusalText = r => Object.entries(r.summary.refusals).map(([k, v]) => `${k}: ${v}`).join(', ') || 'none';
const stampText = s => s.replace(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/, '$1-$2-$3 $4:$5');
async function loadResults() {
  try { RESULTS = (await api('/api/results')).results; } catch (e) { toast(e.message, 'error'); return; }
  const tb = $('resTable').querySelector('tbody'); tb.innerHTML = '';
  if (!RESULTS.length) { tb.innerHTML = '<tr><td colspan="9" class="note">No stored runs yet. Start one from the Scenario tab.</td></tr>'; return; }
  RESULTS.forEach(r => {
    const s = r.summary, tr = document.createElement('tr'); tr.className = 'row' + (selected.has(r.file) ? ' sel' : '');
    const bad = s.fixes === 0 ? 'badge-danger' : s.rms_m > 10 ? 'badge-warning' : 'badge-success';
    tr.innerHTML = `<td><input type="checkbox" ${selected.has(r.file) ? 'checked' : ''} style="width:auto"></td><td><strong>${r.name}</strong></td><td>${stampText(r.started)}</td><td class="num">${s.fixes}</td><td class="num"><span class="badge ${bad}">${fmt(s.rms_m)}</span></td><td class="num">${fmt(s.p95_m)}</td><td class="num">${fmt(s.claimed_mean_m)}</td><td class="num">${fmt(s.claimed_ratio, 2)}</td><td>${refusalText(r)}</td>`;
    tr.querySelector('input').addEventListener('click', e => { e.stopPropagation(); e.target.checked ? selected.add(r.file) : selected.delete(r.file); tr.classList.toggle('sel', e.target.checked); });
    tr.addEventListener('click', () => showDetail(r));
    tb.appendChild(tr);
  });
}
async function full(file) { if (!cache[file]) cache[file] = await api('/api/results/' + encodeURIComponent(file)); return cache[file]; }
$('compareBtn').addEventListener('click', async () => {
  const picked = RESULTS.filter(r => selected.has(r.file)); if (!picked.length) { toast('Select one or more runs first', 'error'); return; }
  const runs = [];
  for (const [i, r] of picked.entries()) { const d = await full(r.file); runs.push({ name: picked.filter(x => x.name === r.name).length > 1 ? r.name + ' ' + stampText(r.started) : r.name, fixes: d.fixes, color: COLORS[i % COLORS.length] }); }
  $('cmpCard').hidden = false; drawCdf($('plotCdf'), runs);
  $('cdfLegend').innerHTML = runs.map(r => `<span><i style="background:${r.color}"></i>${r.name}</span>`).join('');
  window._cmp = runs;
});
$('reportBtn').addEventListener('click', async () => {
  const files = RESULTS.filter(r => selected.has(r.file)).map(r => r.file); if (!files.length) { toast('Select one or more runs first', 'error'); return; }
  const r = await fetch('/api/report', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ files }) });
  if (!r.ok) { toast('The report could not be made', 'error'); return; }
  window.open(URL.createObjectURL(await r.blob()), '_blank');
});
$('clearBtn').addEventListener('click', () => { selected.clear(); $('cmpCard').hidden = true; loadResults(); });
async function showDetail(r) {
  const d = await full(r.file), s = r.summary, per = Object.entries(s.rstd_error_ns || {});
  $('detail').innerHTML = `<div class="card"><div class="card-title">${r.name}</div><p class="note">${(d.scenario && d.scenario.note) || ''}</p>
    <div class="stat-grid">${[['Fixes', s.fixes], ['rms error', fmt(s.rms_m) + ' m'], ['Median', fmt(s.p50_m) + ' m'], ['95 %', fmt(s.p95_m) + ' m'], ['Max', fmt(s.max_m) + ' m'], ['Claimed', fmt(s.claimed_mean_m) + ' m'], ['Offset east / north', fmt(s.east_err_mean_m) + ' / ' + fmt(s.north_err_mean_m) + ' m'], ['Scatter east / north', fmt(s.east_err_sd_m) + ' / ' + fmt(s.north_err_sd_m) + ' m']]
      .map(t => `<div class="stat-tile"><div class="label">${t[0]}</div><div class="val">${t[1]}</div></div>`).join('')}</div>
    <div class="grid2"><div><svg class="plot" id="dScatter" viewBox="0 0 460 360"></svg></div><div><svg class="plot" id="dTime" viewBox="0 0 460 360"></svg></div></div>
    <h3 class="sec">Time-difference error per cell</h3>
    <table class="grid"><thead><tr><th>Cell</th><th class="num">n</th><th class="num">mean (ns)</th><th class="num">sd (ns)</th><th class="num">rms (ns)</th><th class="num">rms (m of range)</th></tr></thead><tbody>${per.map(([c, x]) => `<tr><td>${c}</td><td class="num">${x.n}</td><td class="num">${fmt(x.mean)}</td><td class="num">${fmt(x.sd)}</td><td class="num">${fmt(x.rms)}</td><td class="num">${fmt(x.rms * 0.299792458)}</td></tr>`).join('') || '<tr><td colspan="6">No fix, so no time differences.</td></tr>'}</tbody></table>
    <div class="note" style="margin-top:.8rem">Platform revisions: ${Object.entries(r.revisions).map(([k, v]) => k + ' ' + v).join(', ')}. UE duration ${r.seconds} s.</div></div>`;
  drawScatter($('dScatter'), d.fixes, s.rms_m); drawTime($('dTime'), d.fixes, r.seconds); $('detail').scrollIntoView({ behavior: 'smooth' });
}
function redrawAll() { drawMap(); renderLive(); if (window._cmp && !$('cmpCard').hidden) drawCdf($('plotCdf'), window._cmp); }

/* start */
(async function () {
  buildCellTable();
  try {
    const d = await api('/api/presets'); PRESETS = d.presets; LAB = d.lab;
    PRESETS.forEach(p => { const o = document.createElement('option'); o.value = p.name; o.textContent = p.name; $('preset').appendChild(o); });
    const first = (PRESETS.find(p => p.name === 'lab-default') || PRESETS[0]).name; $('preset').value = first; loadPreset(first);
  } catch (e) { toast('Could not load the presets: ' + e.message, 'error'); }
  await poll(); setInterval(poll, 2000);
})();
