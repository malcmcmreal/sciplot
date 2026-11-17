// SciPlot front end. All plotting happens in plotter.py (matplotlib via Pyodide);
// this file handles the controls, file loading and the interactive overlay.

const PY_VERSION = 1;          // bump with ?v= in index.html when plotter.py changes
const PREVIEW_DPI = 200;
const LS_KEY = 'sciplot-settings';

const $ = (id) => document.getElementById(id);
const cfgEls = () => [...document.querySelectorAll('[data-cfg]')];

let py = null;            // Pyodide instance
let PYSRC = '';           // plotter.py source (also offered as a download)
let info = null;          // columns / preview returned by load_file
let curFile = null;       // { name, bytes, sheet }
let pending = null;       // { x, series } to apply once data is loaded
let excluded = new Set(); // excluded row indices (0-based)
let tool = 'zoom';
let last = null;          // last preview render { geom, points }
let renderTimer = null;
let hoverRow = null;
let DEFAULTS = null;

// ---------------------------------------------------------------- status
function status(text, cls = '') {
  const el = $('status');
  el.textContent = text;
  el.className = 'status ' + cls;
}
function showError(e) {
  const msg = String(e?.message ?? e);
  // Python tracebacks: keep the last meaningful lines
  $('err').textContent = msg.split('\n').filter(Boolean).slice(-2).join('\n');
}

// ---------------------------------------------------------------- Python
async function boot() {
  try {
    py = await loadPyodide();
    status('Loading numpy, pandas, matplotlib…');
    await py.loadPackage(['numpy', 'pandas', 'matplotlib', 'micropip']);
    await py.pyimport('micropip').install(['openpyxl']);
    PYSRC = await (await fetch(`plotter.py?v=${PY_VERSION}`)).text();
    py.FS.mkdirTree('/home/pyodide/app');
    py.FS.writeFile('/home/pyodide/app/plotter.py', PYSRC);
    py.runPython("import sys; sys.path.insert(0, '/home/pyodide/app'); import plotter");
    status('Ready', 'ok');
    if (curFile) loadData();
  } catch (e) {
    status('Python failed to load', 'fail');
    showError(e);
  }
}

function pyCall(expr, args) {
  py.globals.set('_args', py.toPy(args));
  return JSON.parse(py.runPython(expr));
}

// ---------------------------------------------------------------- data loading
async function setFile(name, bytes) {
  if (info) {
    // new dataset: keep the style, drop labels and ranges that belonged to the old one
    ['title', 'xlabel', 'ylabel', 'xmin', 'xmax', 'ymin', 'ymax'].forEach((id) => { $(id).value = ''; });
  }
  curFile = { name, bytes, sheet: null };
  $('fname').textContent = name;
  excluded.clear();
  if (py && PYSRC) loadData();
  else status('Starting Python… (your file will load when ready)');
}

async function loadData(sheet = null) {
  $('err').textContent = '';
  try {
    if (/\.xls$/i.test(curFile.name)) await py.pyimport('micropip').install('xlrd');
    const path = '/tmp/' + curFile.name.replace(/[^\w.\-]/g, '_');
    py.FS.writeFile(path, curFile.bytes);
    info = pyCall('plotter.load_file(*_args)', [path, curFile.name, $('header').value, sheet ?? curFile.sheet]);
    curFile.sheet = info.sheet;
    $('sheetRow').hidden = !info.sheets.length;
    $('sheet').innerHTML = info.sheets.map((s) => `<option ${s === info.sheet ? 'selected' : ''}>${esc(s)}</option>`).join('');
    fillColumns(pending || { x: $('x').options.length ? $('x').value : undefined, series: getSeries() });
    pending = null;
    showTable();
    scheduleRender(0);
  } catch (e) { showError(e); }
}

$('file').addEventListener('change', async (e) => {
  const f = e.target.files[0];
  if (f) setFile(f.name, new Uint8Array(await f.arrayBuffer()));
  e.target.value = '';
});
const drop = $('drop');
drop.addEventListener('click', () => $('file').click());
drop.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('file').click(); } });
drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', async (e) => {
  e.preventDefault(); drop.classList.remove('over');
  const f = e.dataTransfer.files[0];
  if (f) setFile(f.name, new Uint8Array(await f.arrayBuffer()));
});
$('example').addEventListener('change', async (e) => {
  const url = e.target.value;
  if (!url) return;
  try {
    const buf = new Uint8Array(await (await fetch(url)).arrayBuffer());
    setFile(url.split('/').pop(), buf);
  } catch (err) { showError(err); }
  e.target.value = '';
});
$('pasteLoad').addEventListener('click', () => {
  const t = $('paste').value;
  if (t.trim()) setFile('pasted.txt', new TextEncoder().encode(t));
});
$('header').addEventListener('change', () => curFile && py && loadData());
$('sheet').addEventListener('change', () => { excluded.clear(); loadData($('sheet').value); });

// ---------------------------------------------------------------- columns & series
function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]); }
function colOptions(sel, blank) {
  return (blank ? `<option value="">${blank}</option>` : '') +
    info.columns.map((c) => `<option ${c === sel ? 'selected' : ''}>${esc(c)}</option>`).join('');
}

function fillColumns(pref = {}) {
  const cols = info.columns;
  const firstNum = cols.find((c, i) => info.numeric[i]);
  let x = pref.x;
  if (x !== '' && !cols.includes(x)) x = firstNum ?? cols[0];
  $('x').innerHTML = colOptions(x, '(row number)');
  $('x').value = x;
  $('seriesList').innerHTML = '';
  const keep = (pref.series || []).filter((s) => cols.includes(s.y));
  if (keep.length) keep.forEach(addSeries);
  else addSeries({ y: cols.find((c, i) => info.numeric[i] && c !== x) ?? cols[0] });
  updateVisibility();
}

function addSeries(s = {}) {
  const d = document.createElement('div');
  d.className = 'series';
  d.innerHTML = `<button type="button" class="rm" title="Remove series">✕</button>
    <div class="row"><label>Y</label><select class="s-y">${colOptions(s.y)}</select></div>
    <div class="row s-err"><label>Y error</label><select class="s-yerr">${colOptions(s.yerr, 'none')}</select></div>
    <div class="row s-err"><label>X error</label><select class="s-xerr">${colOptions(s.xerr, 'none')}</select></div>
    <div class="row"><label>Label</label><input type="text" class="s-label" placeholder="column name" value="${esc(s.label || '')}"></div>
    <div class="row s-fitrow"><label>Fit</label><select class="s-fit">
      <option value="0">None</option><option value="1">Linear</option><option value="2">Quadratic</option><option value="3">Cubic</option></select></div>`;
  d.querySelector('.s-fit').value = String(s.fit || 0);
  d.querySelector('.rm').addEventListener('click', () => {
    if (document.querySelectorAll('.series').length > 1) { d.remove(); scheduleRender(); }
  });
  d.addEventListener('input', () => scheduleRender());
  $('seriesList').appendChild(d);
  updateVisibility();
}
$('addSeries').addEventListener('click', () => {
  if (!info) return;
  const used = getSeries().map((s) => s.y);
  const y = info.columns.find((c, i) => info.numeric[i] && c !== $('x').value && !used.includes(c)) ?? info.columns[0];
  addSeries({ y });
  scheduleRender(0);
});

function getSeries() {
  return [...document.querySelectorAll('.series')].map((d) => ({
    y: d.querySelector('.s-y').value,
    yerr: d.querySelector('.s-yerr').value,
    xerr: d.querySelector('.s-xerr').value,
    label: d.querySelector('.s-label').value,
    fit: +d.querySelector('.s-fit').value,
  }));
}

function updateVisibility() {
  const k = $('kind').value;
  const hist = k === 'hist', bar = k === 'bar';
  $('binsRow').hidden = !hist;
  $('densRow').hidden = !hist;
  $('xRow').hidden = hist;
  $('customSize').hidden = $('size').value !== 'custom';
  document.querySelectorAll('.series .s-err').forEach((el, i) => { el.hidden = hist || (bar && i % 2 === 1); });
  document.querySelectorAll('.series .s-fitrow').forEach((el) => { el.hidden = hist || bar; });
}

// ---------------------------------------------------------------- config
function getConfig({ withExcluded = true } = {}) {
  const c = {};
  for (const el of cfgEls()) {
    c[el.id] = el.type === 'checkbox' ? el.checked : el.type === 'number' ? +el.value : el.value;
  }
  c.series = getSeries();
  c.header = $('header').value;
  if (curFile?.sheet) c.sheet = curFile.sheet;
  if (withExcluded) c.excluded = [...excluded].sort((a, b) => a - b);
  return c;
}

function applyConfig(c) {
  for (const el of cfgEls()) {
    if (!(el.id in c) || el.id === 'x') continue;
    if (el.type === 'checkbox') el.checked = !!c[el.id];
    else el.value = c[el.id];
  }
  if (c.header) $('header').value = c.header;
  if (Array.isArray(c.excluded)) excluded = new Set(c.excluded);
  const pref = { x: c.x ?? $('x').value, series: c.series || [] };
  if (info) fillColumns(pref); else pending = pref;
  updateVisibility();
}

// URL hash holds settings (not data) so a style can be shared or bookmarked
function encodeCfg(c) {
  const bytes = new TextEncoder().encode(JSON.stringify(c));
  let bin = ''; bytes.forEach((b) => { bin += String.fromCharCode(b); });
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function decodeCfg(s) {
  const bin = atob(s.replace(/-/g, '+').replace(/_/g, '/'));
  return JSON.parse(new TextDecoder().decode(Uint8Array.from(bin, (ch) => ch.charCodeAt(0))));
}

function download(name, blob) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
const baseName = () => (curFile ? curFile.name.replace(/\.[^.]+$/, '') : 'figure');

$('copyLink').addEventListener('click', async () => {
  const url = location.href.split('#')[0] + '#s=' + encodeCfg(getConfig({ withExcluded: false }));
  history.replaceState(null, '', url);
  try { await navigator.clipboard.writeText(url); flash($('copyLink'), 'Link copied'); }
  catch { flash($('copyLink'), 'Link is in the address bar'); }
});
$('saveCfg').addEventListener('click', () => {
  download('sciplot-settings.json', new Blob([JSON.stringify(getConfig({ withExcluded: false }), null, 2)], { type: 'application/json' }));
});
$('loadCfg').addEventListener('click', () => $('cfgFile').click());
$('cfgFile').addEventListener('change', async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  try { applyConfig(JSON.parse(await f.text())); scheduleRender(0); } catch (err) { showError(err); }
  e.target.value = '';
});
$('reset').addEventListener('click', () => {
  try { localStorage.removeItem(LS_KEY); } catch {}
  history.replaceState(null, '', location.href.split('#')[0]);
  excluded.clear();
  applyConfig({ ...DEFAULTS, series: [] });
  scheduleRender(0);
});
function flash(btn, text) {
  const old = btn.textContent;
  btn.textContent = text;
  setTimeout(() => { btn.textContent = old; }, 1600);
}

// ---------------------------------------------------------------- rendering
for (const el of cfgEls()) {
  el.addEventListener(el.type === 'text' || el.type === 'number' ? 'input' : 'change', () => {
    updateVisibility();
    scheduleRender();
  });
}

function scheduleRender(delay = 200) {
  clearTimeout(renderTimer);
  renderTimer = setTimeout(renderPreview, delay);
}

function pyRender(cfg, fmt, dpi) {
  return pyCall(`plotter.render(_args[0], "${fmt}", ${dpi})`, [JSON.stringify(cfg)]);
}

function renderPreview() {
  if (!py || !info) return;
  $('err').textContent = '';
  try {
    const cfg = getConfig();
    const r = pyRender(cfg, 'png', PREVIEW_DPI);
    last = { geom: r.geom, points: r.points, kind: cfg.kind };
    const img = $('fig');
    img.onload = () => {
      // show at roughly 1.6× physical size, capped by the panel width
      img.style.width = Math.round(img.naturalWidth / PREVIEW_DPI * 190) + 'px';
      sizeOverlay();
    };
    img.src = 'data:image/png;base64,' + r.data;
    $('stage').classList.add('show');
    $('placeholder').hidden = true;
    $('msgs').textContent = r.msgs.join('\n');
    document.querySelectorAll('.series').forEach((d, i) => d.style.setProperty('--sc', r.colors[i] || '#2f6fb0'));
    document.querySelectorAll('[data-exp], #dlScript').forEach((b) => { b.disabled = false; });
    $('resetZoom').disabled = !['xmin', 'xmax', 'ymin', 'ymax'].some((id) => $(id).value !== '');
    $('clearExcl').hidden = excluded.size === 0;
    $('clearExcl').textContent = `Clear exclusions (${excluded.size})`;
    markTableRows();
    try { localStorage.setItem(LS_KEY, JSON.stringify(getConfig({ withExcluded: false }))); } catch {}
  } catch (e) { showError(e); }
}

// ---------------------------------------------------------------- export
document.querySelectorAll('[data-exp]').forEach((b) => b.addEventListener('click', () => {
  const fmt = b.dataset.exp;
  try {
    const r = pyRender(getConfig(), fmt, +$('dpi').value);
    const bin = Uint8Array.from(atob(r.data), (ch) => ch.charCodeAt(0));
    const mime = { pdf: 'application/pdf', svg: 'image/svg+xml', png: 'image/png' }[fmt];
    download(`${baseName()}.${fmt}`, new Blob([bin], { type: mime }));
  } catch (e) { showError(e); }
}));
$('dlScript').addEventListener('click', () => {
  const c = getConfig();
  c.dpi = +$('dpi').value;
  download(`${baseName()}_plot.json`, new Blob([JSON.stringify(c, null, 2)], { type: 'application/json' }));
  setTimeout(() => download('plotter.py', new Blob([PYSRC], { type: 'text/x-python' })), 400);
  $('msgs').textContent += (($('msgs').textContent ? '\n' : '') +
    `To rebuild offline:  python plotter.py ${curFile?.name ?? 'data.csv'} ${baseName()}_plot.json figure.pdf`);
});

// ---------------------------------------------------------------- data table
function showTable() {
  $('dataPanel').hidden = false;
  $('dataInfo').textContent = `${info.nrows} rows × ${info.columns.length} columns` +
    (info.preview.length < info.nrows ? ` (first ${info.preview.length} shown)` : '') +
    '. Click a row number to exclude or include that row.';
  const head = '<tr><th class="rown">#</th>' + info.columns.map((c, i) =>
    `<th class="${info.numeric[i] ? '' : 'txt'}" title="${info.numeric[i] ? 'numeric' : 'text'}">${esc(c)}</th>`).join('') + '</tr>';
  const body = info.preview.map((r, ri) => `<tr data-row="${ri}"><td class="rown">${ri + 1}</td>` +
    r.map((v, i) => `<td class="${info.numeric[i] ? '' : 'txt'}">${esc(v)}</td>`).join('') + '</tr>').join('');
  $('dataTable').innerHTML = `<thead>${head}</thead><tbody>${body}</tbody>`;
}
$('dataTable').addEventListener('click', (e) => {
  const td = e.target.closest('td.rown');
  if (!td) return;
  toggleRow(+td.parentElement.dataset.row);
});
function markTableRows() {
  document.querySelectorAll('#dataTable tbody tr').forEach((tr) => {
    const r = +tr.dataset.row;
    tr.classList.toggle('excl', excluded.has(r));
    tr.classList.toggle('hl', r === hoverRow);
  });
}
function toggleRow(r) {
  if (excluded.has(r)) excluded.delete(r); else excluded.add(r);
  scheduleRender(0);
}
$('clearExcl').addEventListener('click', () => { excluded.clear(); scheduleRender(0); });

// ---------------------------------------------------------------- interactive overlay
const canvas = $('overlay');
const ctx = canvas.getContext('2d');
let drag = null;

function sizeOverlay() {
  const r = $('fig').getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(r.width * dpr);
  canvas.height = Math.round(r.height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  drawOverlay();
}
new ResizeObserver(sizeOverlay).observe($('fig'));

const box = () => { const r = canvas.getBoundingClientRect(); return { w: r.width, h: r.height }; };
const tr = (v, log) => (log ? Math.log10(v) : v);
const untr = (v, log) => (log ? 10 ** v : v);

function dataToPx(x, y) {
  const g = last.geom, { w, h } = box();
  const fx = (tr(x, g.xlog) - tr(g.xlim[0], g.xlog)) / (tr(g.xlim[1], g.xlog) - tr(g.xlim[0], g.xlog));
  const fy = (tr(y, g.ylog) - tr(g.ylim[0], g.ylog)) / (tr(g.ylim[1], g.ylog) - tr(g.ylim[0], g.ylog));
  return [(g.x0 + fx * (g.x1 - g.x0)) * w, (g.y1 - fy * (g.y1 - g.y0)) * h];
}
function pxToData(px, py_) {
  const g = last.geom, { w, h } = box();
  const fx = (px / w - g.x0) / (g.x1 - g.x0);
  const fy = (g.y1 - py_ / h) / (g.y1 - g.y0);
  const lx = tr(g.xlim[0], g.xlog) + fx * (tr(g.xlim[1], g.xlog) - tr(g.xlim[0], g.xlog));
  const ly = tr(g.ylim[0], g.ylog) + fy * (tr(g.ylim[1], g.ylog) - tr(g.ylim[0], g.ylog));
  return [untr(lx, g.xlog), untr(ly, g.ylog)];
}
function inAxes(px, py_) {
  const g = last?.geom, { w, h } = box();
  return g && px >= g.x0 * w && px <= g.x1 * w && py_ >= g.y0 * h && py_ <= g.y1 * h;
}
function nearest(px, py_, maxDist = 10) {
  let best = null, bd = maxDist;
  for (const s of last?.points || []) {
    for (const [row, x, y, ex] of s.pts) {
      if ((last.geom.xlog && x <= 0) || (last.geom.ylog && y <= 0)) continue;
      const [qx, qy] = dataToPx(x, y);
      const d = Math.hypot(qx - px, qy - py_);
      if (d < bd) { bd = d; best = { row, x, y, ex, label: s.label, color: s.color, qx, qy }; }
    }
  }
  return best;
}
const fmt = (v) => (Math.abs(v) >= 1e5 || (Math.abs(v) < 1e-3 && v !== 0) ? v.toExponential(3) : +v.toPrecision(5)).toString();

let hover = null;
function drawOverlay(mouse) {
  const { w, h } = box();
  ctx.clearRect(0, 0, w, h);
  if (!last?.geom) return;
  const g = last.geom;
  if (mouse && inAxes(mouse[0], mouse[1]) && !drag) {
    ctx.strokeStyle = 'rgba(47,111,176,0.35)';
    ctx.setLineDash([3, 3]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(g.x0 * w, mouse[1]); ctx.lineTo(g.x1 * w, mouse[1]);
    if (last.kind !== 'bar') { ctx.moveTo(mouse[0], g.y0 * h); ctx.lineTo(mouse[0], g.y1 * h); }
    ctx.stroke();
    ctx.setLineDash([]);
  }
  if (hover) {
    ctx.strokeStyle = tool === 'exclude' ? '#c8372d' : hover.color;
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(hover.qx, hover.qy, 7, 0, 2 * Math.PI); ctx.stroke();
  }
  if (drag?.moved) {
    const x0 = last.kind === 'bar' ? g.x0 * w : drag.x0, x1 = last.kind === 'bar' ? g.x1 * w : drag.x1;
    ctx.fillStyle = 'rgba(47,111,176,0.12)';
    ctx.strokeStyle = 'rgba(47,111,176,0.8)';
    ctx.lineWidth = 1;
    ctx.fillRect(x0, drag.y0, x1 - x0, drag.y1 - drag.y0);
    ctx.strokeRect(x0, drag.y0, x1 - x0, drag.y1 - drag.y0);
  }
}

function mousePos(e) {
  const r = canvas.getBoundingClientRect();
  return [e.clientX - r.left, e.clientY - r.top];
}

canvas.addEventListener('mousemove', (e) => {
  if (!last?.geom) return;
  const [mx, my] = mousePos(e);
  if (drag) {
    drag.x1 = mx; drag.y1 = my;
    drag.moved = drag.moved || Math.hypot(mx - drag.x0, my - drag.y0) > 5;
    drawOverlay();
    return;
  }
  hover = nearest(mx, my);
  const tip = $('tip');
  if (inAxes(mx, my)) {
    const [dx, dy] = pxToData(mx, my);
    $('readout').textContent = last.kind === 'bar' ? `y = ${fmt(dy)}` : `x = ${fmt(dx)}   y = ${fmt(dy)}`;
  } else $('readout').textContent = '';
  if (hover) {
    const rl = info.rowlabels ? `<b>${esc(info.rowlabels.values[hover.row])}</b> · ` : '';
    tip.innerHTML = `${rl}row ${hover.row + 1}${hover.ex ? ' (excluded)' : ''}<br>${esc(hover.label)}<br>` +
      `x = ${fmt(hover.x)}, y = ${fmt(hover.y)}` +
      (tool === 'exclude' ? `<br><i>Click to ${hover.ex ? 'include' : 'exclude'}</i>` : '');
    tip.style.left = hover.qx + 'px';
    tip.style.top = hover.qy + 'px';
    tip.hidden = false;
  } else tip.hidden = true;
  const newRow = hover ? hover.row : null;
  if (newRow !== hoverRow) { hoverRow = newRow; markTableRows(); }
  drawOverlay([mx, my]);
});
canvas.addEventListener('mouseleave', () => {
  hover = null; drag = null; hoverRow = null;
  $('tip').hidden = true; $('readout').textContent = '';
  markTableRows(); drawOverlay();
});
canvas.addEventListener('mousedown', (e) => {
  if (!last?.geom || e.button !== 0) return;
  const [mx, my] = mousePos(e);
  if (tool === 'zoom' && inAxes(mx, my)) drag = { x0: mx, y0: my, x1: mx, y1: my, moved: false };
});
window.addEventListener('mouseup', (e) => {
  if (!last?.geom) return;
  if (drag) {
    const d = drag; drag = null;
    if (d.moved) { zoomTo(d); return; }
  }
  if (e.target !== canvas) return;
  const [mx, my] = mousePos(e);
  const p = nearest(mx, my);
  if (tool === 'exclude' && p) toggleRow(p.row);
  drawOverlay([mx, my]);
});
canvas.addEventListener('dblclick', () => { if (tool === 'zoom') resetZoom(); });

function zoomTo(d) {
  const [ax, ay] = pxToData(d.x0, d.y0);
  const [bx, by] = pxToData(d.x1, d.y1);
  const r4 = (v) => String(+v.toPrecision(4));
  if (last.kind !== 'bar') {
    $('xmin').value = r4(Math.min(ax, bx)); $('xmax').value = r4(Math.max(ax, bx));
  }
  $('ymin').value = r4(Math.min(ay, by)); $('ymax').value = r4(Math.max(ay, by));
  scheduleRender(0);
}
function resetZoom() {
  ['xmin', 'xmax', 'ymin', 'ymax'].forEach((id) => { $(id).value = ''; });
  scheduleRender(0);
}
$('resetZoom').addEventListener('click', resetZoom);

document.querySelectorAll('[data-tool]').forEach((b) => b.addEventListener('click', () => {
  tool = b.dataset.tool;
  document.querySelectorAll('[data-tool]').forEach((o) => o.classList.toggle('on', o === b));
  $('stage').classList.toggle('exclude', tool === 'exclude');
}));

// ---------------------------------------------------------------- start-up
DEFAULTS = getConfig({ withExcluded: false });
delete DEFAULTS.series;
delete DEFAULTS.x;
(() => {
  // settings from a shared link take priority over the last-used settings
  const m = location.hash.match(/s=([\w-]+)/);
  let saved = null;
  if (m) { try { saved = decodeCfg(m[1]); } catch {} }
  if (!saved) { try { saved = JSON.parse(localStorage.getItem(LS_KEY)); } catch {} }
  if (saved) applyConfig(saved);
  updateVisibility();
})();
boot();
