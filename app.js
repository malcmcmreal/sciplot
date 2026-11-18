// SciPlot front end. All plotting happens in plotter.py (matplotlib via Pyodide);
// this file handles the controls, file loading and the interactive overlay.

const PY_VERSION = 14;          // bump with ?v= in index.html when plotter.py changes
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
    status('Installing seaborn…');
    await py.pyimport('micropip').install(['openpyxl', 'seaborn']);
    PYSRC = await (await fetch(`plotter.py?v=${PY_VERSION}`)).text();
    py.FS.mkdirTree('/home/pyodide/app');
    py.FS.writeFile('/home/pyodide/app/plotter.py', PYSRC);
    py.runPython("import sys; sys.path.insert(0, '/home/pyodide/app'); import plotter");
    status('Ready', 'ok');
    if (curFile?.bytes) loadData();
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
let keepSetup = false;      // true after opening a setup that is waiting for its data file
async function setFile(name, bytes) {
  if (keepSetup) {
    keepSetup = false;
    curFile = { name, bytes, sheet: curFile?.sheet ?? null };
    $('fname').textContent = name;
    hideBanner();
    if (py && PYSRC) loadData(); else status('Starting Python… (your file will load when ready)');
    return;
  }
  if (info) {
    // new dataset: keep the style, drop labels, ranges and picker formulas that belonged to the old one
    ['title', 'xlabel', 'ylabel', 'xmin', 'xmax', 'ymin', 'ymax'].forEach((id) => { $(id).value = ''; });
    const fxNames = new Set(calcRows().filter((r) => r.dataset.fx).map((r) => r.querySelector('.c-name').value.trim()));
    calcRows().filter((r) => r.dataset.fx).forEach((r) => r.remove());
    for (const sel of colSelects()) {
      if (!fxNames.has(sel.value)) continue;
      if (sel.id === 'x') sel.innerHTML = '';      // lets the new data pick its first numeric column
      else sel.value = '';
    }
    document.querySelectorAll('.fx-box').forEach((b) => b.remove());
  }
  curFile = { name, bytes, sheet: null };
  $('fname').textContent = name;
  excluded.clear();
  if (py && PYSRC) loadData();
  else status('Starting Python… (your file will load when ready)');
}

async function loadData(sheet = null) {
  if (!curFile?.bytes) return;
  $('err').textContent = '';
  try {
    if (/\.xls$/i.test(curFile.name)) await py.pyimport('micropip').install('xlrd');
    const path = '/tmp/' + curFile.name.replace(/[^\w.\-]/g, '_');
    py.FS.writeFile(path, curFile.bytes);
    info = pyCall('plotter.load_file(*_args)', [path, curFile.name, $('header').value, sheet ?? curFile.sheet,
      +$('headerrow').value || 1, +$('skipafter').value || 0, $('allsheets').checked, $('sheetcol').value || 'Sheet']);
    if (getCalc().length) applyCalcPy();
    curFile.sheet = info.sheet;
    $('allsheetsRow').hidden = info.sheets.length < 2;
    $('sheetRow').hidden = !info.sheets.length || ($('allsheets').checked && info.sheets.length > 1);
    $('sheet').innerHTML = info.sheets.map((s) => `<option ${s === info.sheet ? 'selected' : ''}>${esc(s)}</option>`).join('');
    fillColumns(pending || { x: $('x').options.length ? $('x').value : undefined, series: getSeries() });
    fillSS();
    if (currentView === 'ss') ssSchedule(0);
    showRaw();
    pending = null;
    showTable();
    scheduleRender(0);
  } catch (e) { showError(e); }
}

$('file').addEventListener('change', async (e) => {
  const f = e.target.files[0];
  if (f) openAny(f.name, new Uint8Array(await f.arrayBuffer()));
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
  if (f) openAny(f.name, new Uint8Array(await f.arrayBuffer()));
});
async function openAny(name, bytes) {
  if (/\.(sciplot|png|svg|pdf|json)$/i.test(name)) openProjectBytes(name, bytes);
  else setFile(name, bytes);
}
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
// ---------------------------------------------------------------- calculated columns
function getCalc() {
  return [...document.querySelectorAll('.calc-row')].map((r) => ({
    name: r.querySelector('.c-name').value.trim(), expr: r.querySelector('.c-expr').value.trim(),
    ...(r.dataset.fx ? { fx: true } : {}),
  })).filter((c) => c.name && c.expr);
}
function addCalcRow(c = {}) {
  const r = document.createElement('div');
  r.className = 'calc-row';
  if (c.fx) r.dataset.fx = '1';
  r.innerHTML = `<input type="text" class="c-name" placeholder="name" value="${esc(c.name || '')}">
    <input type="text" class="c-expr" placeholder="formula" value="${esc(c.expr || '')}">
    <button type="button" title="Remove">✕</button>`;
  r.querySelector('button').addEventListener('click', () => { r.remove(); applyCalc(); });
  r.addEventListener('input', () => { clearTimeout(calcTimer); calcTimer = setTimeout(applyCalc, 700); });
  $('calcList').appendChild(r);
}
let calcTimer = null;

// ---------------------------------------------------------------- formulas in column pickers
// Choosing "ƒ Formula…" in any column menu opens a formula box under it. Each formula becomes a
// calculated column named after the formula itself, so it is saved, shown in the data table and
// in the Calculated columns panel.
const colSelects = () => [$('x'), ...document.querySelectorAll('select[data-col]'),
  ...document.querySelectorAll('.series:not([data-source]) .s-y, .series:not([data-source]) .s-yerr, .series:not([data-source]) .s-xerr')];
const calcRows = () => [...document.querySelectorAll('.calc-row')];
const calcRowByName = (n) => calcRows().find((r) => r.querySelector('.c-name').value.trim() === n);

function selectState() {
  const cols = Object.fromEntries([...document.querySelectorAll('select[data-col]')].map((e) => [e.id, e.value]));
  return { x: $('x').value, series: getSeries(), cols };
}

function fxBox(sel, expr = '', err = '') {
  const row = sel.closest('.row');
  let box = row.nextElementSibling;
  if (!box || !box.classList.contains('fx-box')) {
    box = document.createElement('div');
    box.className = 'fx-box';
    box.innerHTML = `<input type="text" class="fx-input" spellcheck="false"
        placeholder='e.g. "Angle to Caxis" + "Grain Orientation"'>
      <div class="fx-err"></div>
      <div class="fx-hint">Columns in "quotes" · + − * / ^ · sind() cosd() wrap90() sqrt() log10() abs() mean() …</div>`;
    row.after(box);
    const inp = box.querySelector('.fx-input');
    let t = null;
    inp.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => commitFormula(box), 800); });
    inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') { clearTimeout(t); commitFormula(box); } });
  }
  box._sel = sel;
  box._old = sel.value !== FX ? sel.value : (box._old || '');
  const inp = box.querySelector('.fx-input');
  if (document.activeElement !== inp) inp.value = expr;
  box.querySelector('.fx-err').textContent = err;
  return box;
}

function commitFormula(box) {
  const sel = box._sel;
  const expr = box.querySelector('.fx-input').value.trim();
  if (!expr || !info) return;
  const old = box._old;                                   // formula column this picker showed before
  const st = selectState();
  let row = old && calcRowByName(old);
  if (row && row.dataset.fx) {
    row.querySelector('.c-name').value = expr;
    row.querySelector('.c-expr').value = expr;
  } else {
    row = calcRowByName(expr);
    if (!row) { addCalcRow({ name: expr, expr, fx: true }); row = calcRowByName(expr); }
  }
  try { applyCalcPy(); } catch (e) { showError(e); return; }
  const bad = (info.calc_errors || []).find((m) => m.startsWith(expr + ':'));
  if (bad) {
    box.querySelector('.fx-err').textContent = bad.slice(expr.length + 1).trim();
    return;
  }
  // point this picker (and any other showing the old formula) at the new column
  const swap = (v) => (v === old && old ? expr : v);
  st.x = swap(st.x);
  for (const k in st.cols) st.cols[k] = swap(st.cols[k]);
  st.series = st.series.map((s) => ({ ...s, y: swap(s.y), yerr: swap(s.yerr), xerr: swap(s.xerr) }));
  if (sel.id === 'x') st.x = expr;
  else if (sel.hasAttribute('data-col')) st.cols[sel.id] = expr;
  else {
    const i = [...document.querySelectorAll('.series')].indexOf(sel.closest('.series'));
    const key = sel.classList.contains('s-y') ? 'y' : sel.classList.contains('s-yerr') ? 'yerr' : 'xerr';
    if (st.series[i]) st.series[i][key] = expr;
  }
  fillColumns(st);
  showTable();
  scheduleRender(0);
}

function decorateFormulas() {
  // show the formula under every picker that is set to a formula column; tidy unused formulas
  const used = new Set();
  for (const sel of colSelects()) {
    const row = sel.closest('.row');
    const next = row && row.nextElementSibling;
    const r = calcRowByName(sel.value);
    if (sel.value === FX) { fxBox(sel); continue; }
    if (r && r.dataset.fx) { used.add(sel.value); fxBox(sel, r.querySelector('.c-expr').value); }
    else if (next && next.classList.contains('fx-box')) next.remove();
  }
  let removed = false;
  for (const r of calcRows()) {
    if (r.dataset.fx && !used.has(r.querySelector('.c-name').value.trim())) { r.remove(); removed = true; }
  }
  return removed;
}

// "ƒ Formula…" picked: open the box instead of plotting a missing column
for (const ev of ['input', 'change']) {
  document.addEventListener(ev, (e) => {
    const sel = e.target;
    if (sel.tagName !== 'SELECT') return;
    if (sel.value === FX) {
      e.stopImmediatePropagation();
      if (ev === 'change') { const b = fxBox(sel); b.querySelector('.fx-input').focus(); }
      return;
    }
  }, true);
}
document.addEventListener('change', (e) => {
  if (e.target.tagName === 'SELECT' && info && colSelects().includes(e.target)) {
    if (decorateFormulas()) { applyCalcPy(); }
  }
});

function applyCalcPy() {
  info = pyCall('plotter.add_columns(_args[0])', [JSON.stringify(getCalc())]);
  $('calcErr').textContent = (info.calc_errors || []).join('\n');
}
function applyCalc() {
  if (!py || !info) return;
  try {
    const keep = { x: $('x').value, series: getSeries(), cols: Object.fromEntries([...document.querySelectorAll('select[data-col]')].map((e) => [e.id, e.value])) };
    applyCalcPy();
    fillColumns(keep);
    fillSS();
    showTable();
    scheduleRender(0);
  } catch (e) { showError(e); }
}
$('calcAdd').addEventListener('click', () => addCalcRow());
$('allsheets').addEventListener('change', () => { excluded.clear(); curFile && py && loadData(); });
let sheetcolTimer = null;
$('sheetcol').addEventListener('input', () => { clearTimeout(sheetcolTimer); sheetcolTimer = setTimeout(() => { if ($('allsheets').checked && curFile && py) loadData(); }, 700); });

let headerTimer = null;
function reloadHeader() {
  $('headerRows').hidden = $('header').value !== 'row';
  clearTimeout(headerTimer);
  headerTimer = setTimeout(() => curFile && py && loadData(), 300);
}
$('header').addEventListener('change', () => {
  if ($('header').value === 'row' && info) $('rawPanel').open = true;
  reloadHeader();
});
$('headerrow').addEventListener('input', reloadHeader);
$('skipafter').addEventListener('input', reloadHeader);

function showRaw() {
  const hr = $('header').value === 'row' ? (+$('headerrow').value || 1) : null;
  $('rawTable').innerHTML = (info.raw || []).map(([n, t]) =>
    `<tr data-n="${n}" class="${hr === n ? 'hdr' : hr && n < hr ? 'above' : ''}"><td class="rown">${n}</td><td>${esc(t)}</td></tr>`).join('');
}
$('rawTable').addEventListener('click', (e) => {
  const tr = e.target.closest('tr');
  if (!tr) return;
  $('header').value = 'row';
  $('headerrow').value = tr.dataset.n;
  excluded.clear();
  reloadHeader();
});
$('sheet').addEventListener('change', () => { excluded.clear(); loadData($('sheet').value); });

// ---------------------------------------------------------------- columns & series
function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]); }
const FX = '__fx__';
function colOptions(sel, blank) {
  return (blank ? `<option value="">${blank}</option>` : '') +
    info.columns.map((c) => `<option ${c === sel ? 'selected' : ''}>${esc(c)}</option>`).join('') +
    `<option value="${FX}">ƒ Formula…</option>`;
}

function fillColumns(pref = {}) {
  fillColumnsInner(pref);
  decorateFormulas();
  buildRefExtra();
}

// ---- mean/median lines of other columns
let refPending = null;
const REF_COLOURS = [['', 'Line colour setting'], ['black', 'Black'], ['0.45', 'Grey'], ['#BE5103', 'Orange'],
  ['#D55E00', 'Vermilion'], ['#c8372d', 'Red'], ['#0072B2', 'Blue'], ['#009E73', 'Green'], ['#CC79A7', 'Pink']];
function getRefExtra() {
  return [...document.querySelectorAll('.ref-row')].map((r) => ({
    col: r.querySelector('.r-col').value, axis: r.querySelector('.r-axis').value,
    err: r.querySelector('.r-err').value, color: r.querySelector('.r-color').value,
  })).filter((o) => o.col);
}
function addRefRow(o = {}) {
  if (!info) return;
  const nums = info.columns.filter((c, i) => info.numeric[i]);
  const opt = (list, want, blank) => (blank ? `<option value="">${blank}</option>` : '') +
    list.map((c) => `<option ${c === want ? 'selected' : ''}>${esc(c)}</option>`).join('');
  const r = document.createElement('div');
  r.className = 'ref-row';
  r.innerHTML = `<button type="button" class="rm" title="Remove">✕</button>
    <div class="row"><label>Column</label><select class="r-col">${opt(nums, o.col ?? nums[0])}</select></div>
    <div class="row"><label>Draw as</label><select class="r-axis">
      <option value="y">Horizontal line (Y axis)</option><option value="x">Vertical line (X axis)</option></select></div>
    <div class="row"><label>Error col.</label><select class="r-err">${opt(nums, o.err, 'none (for weighted mean)')}</select></div>
    <div class="row"><label>Colour</label><select class="r-color">${REF_COLOURS.map(([v, t]) => `<option value="${v}">${t}</option>`).join('')}</select></div>`;
  r.querySelector('.r-axis').value = o.axis || 'y';
  r.querySelector('.r-color').value = o.color || '';
  const paint = () => r.style.setProperty('--rc', /^#|black/.test(r.querySelector('.r-color').value) ? r.querySelector('.r-color').value : '');
  paint();
  r.querySelector('.rm').addEventListener('click', () => { r.remove(); scheduleRender(0); });
  r.addEventListener('change', () => { paint(); scheduleRender(0); });
  $('refExtraList').appendChild(r);
  updateVisibility();
}
function buildRefExtra() {
  if (!info) return;
  const want = refPending || getRefExtra();
  refPending = null;
  $('refExtraList').innerHTML = '';
  want.filter((o) => info.columns.includes(o.col)).forEach((o) => addRefRow(o));
}
$('refExtraAdd').addEventListener('click', () => {
  addRefRow();
  if (!$('refmean').checked && !$('refmedian').checked && !$('refwmean').checked) $('refmean').checked = true;
  scheduleRender(0);
});
function fillColumnsInner(pref = {}) {
  const cols = info.columns;
  const firstNum = cols.find((c, i) => info.numeric[i]);
  let x = pref.x;
  if (x !== '' && !cols.includes(x)) x = firstNum ?? cols[0];
  $('x').innerHTML = colOptions(x, '(row number)');
  $('x').value = x;
  for (const el of document.querySelectorAll('select[data-col]')) {
    const want = pref.cols && el.id in pref.cols ? pref.cols[el.id] : el.value;
    el.innerHTML = colOptions(cols.includes(want) ? want : '', 'none');
  }
  if ($('groupby').options[0]) $('groupby').options[0].textContent = '(X values)';
  $('seriesList').innerHTML = '';
  const keep = (pref.series || []).filter((s) => (s.source
    ? (info.tables?.[s.source]?.columns || []).includes(s.y) : cols.includes(s.y)));
  if (keep.length) keep.forEach((s) => addSeries(s));
  else addSeries({ y: cols.find((c, i) => info.numeric[i] && c !== x) ?? cols[0] });
  updateVisibility();
}

const MARKER_OPTS = [['auto', 'auto'], ['o', '● circle'], ['s', '■ square'], ['^', '▲ triangle'],
  ['v', '▼ triangle down'], ['D', '◆ diamond'], ['P', '✚ plus (filled)'], ['X', '✖ cross (filled)'],
  ['h', '⬢ hexagon'], ['p', '⬟ pentagon'], ['*', '★ star'], ['+', '+ plus'], ['x', '× cross'], ['.', '· point']]
  .map(([v, t]) => `<option value="${v}">${t}</option>`).join('');

function seriesBadge(d) {
  // one-line summary of what's set inside the collapsed "Error bars, fit & style"
  const b = [];
  if (d.querySelector('.s-yerr').value || d.querySelector('.s-xerr').value) b.push('error bars');
  const fit = +d.querySelector('.s-fit').value;
  if (fit) b.push(['', 'linear', 'quadratic', 'cubic'][fit] + ' fit');
  if (d.querySelector('.s-color').dataset.set) b.push('colour');
  if (d.querySelector('.s-marker').value !== 'auto') b.push('marker');
  if (d.querySelector('.s-size').value || d.querySelector('.s-alpha').value) b.push('size/opacity');
  d.querySelector('.s-badge').textContent = b.join(' · ');
}
function tableOptions(t, sel, blank) {
  return (blank ? `<option value="">${blank}</option>` : '') +
    t.columns.map((c) => `<option ${c === sel ? 'selected' : ''}>${esc(c)}</option>`).join('');
}
function addSeries(s = {}, replace = null) {
  const d = document.createElement('div');
  d.className = 'series';
  const tables = info.tables || {};
  const src = s.source && tables[s.source] ? s.source : '';
  if (src) d.dataset.source = src;
  const opts = src ? (sel, blank) => tableOptions(tables[src], sel, blank) : colOptions;
  const srcRow = Object.keys(tables).length ? `<div class="row s-src"><label>Data from</label><select class="s-source">
      <option value="">Main table</option>${(info.table_order || Object.keys(tables)).map((k) => `<option value="${esc(k)}" ${k === src ? 'selected' : ''}>Sheet: ${esc(k)}</option>`).join('')}
    </select></div>` : '';
  d.innerHTML = `<button type="button" class="rm" title="Remove series">✕</button>
    ${srcRow}
    ${src ? `<div class="row s-xrow"><label>X</label><select class="s-x">${opts(s.x)}</select></div>` : ''}
    <div class="row"><label>Y</label><select class="s-y">${opts(s.y)}</select></div>
    <div class="row"><label>Label</label><input type="text" class="s-label" placeholder="column name" value="${esc(s.label || '')}"></div>
    <details class="s-more"${s.open ? ' open' : ''}><summary>Error bars, fit &amp; style <span class="s-badge"></span></summary>
    <div class="row s-err"><label>Y error</label><select class="s-yerr">${opts(s.yerr, 'none')}</select></div>
    <div class="row s-err"><label>X error</label><select class="s-xerr">${opts(s.xerr, 'none')}</select></div>
    <div class="row s-fitrow"><label>Fit</label><select class="s-fit">
      <option value="0">None</option><option value="1">Linear</option><option value="2">Quadratic</option><option value="3">Cubic</option></select></div>
    <div class="row s-look"><label>Colour</label><div class="s-colorwrap">
      <input type="color" class="s-color" value="${s.color || '#000000'}" ${s.color ? 'data-set="1"' : ''}>
      <span class="s-auto">${s.color ? '' : 'from palette'}</span>
      <button type="button" class="s-colorreset" ${s.color ? '' : 'hidden'}>reset</button></div></div>
    <div class="row2 s-look">
      <div class="row s-markerrow"><label>Marker</label><select class="s-marker">${MARKER_OPTS}</select></div>
      <div class="row s-lsrow"><label>Line</label><select class="s-ls">
        <option value="-">solid</option><option value="--">dashed</option><option value=":">dotted</option><option value="-.">dash-dot</option></select></div>
    </div>
    <div class="row3 s-look">
      <div class="row"><label>Size</label><input type="number" class="s-size" step="0.5" min="0.5" placeholder="auto" value="${esc(s.size ?? '')}"></div>
      <div class="row"><label>Opacity</label><input type="number" class="s-alpha" step="0.05" min="0.05" max="1" placeholder="auto" value="${esc(s.alpha ?? '')}"></div>
    </div>
    </details>`;
  const srcSel = d.querySelector('.s-source');
  if (srcSel) srcSel.addEventListener('change', (e) => {
    e.stopPropagation();
    const cur = seriesFrom(d);
    const v = srcSel.value;
    const t = v ? tables[v] : info;
    const nums = t.columns.filter((c, i) => t.numeric[i]);
    const next = v ? { source: v, x: nums[0] ?? t.columns[0], y: nums[1] ?? nums[0] ?? t.columns[0], size: 7, alpha: 1,
      color: cur.color, marker: cur.marker === 'auto' ? 's' : cur.marker }
      : { y: nums.find((c) => c !== $('x').value) ?? t.columns[0], color: cur.color, marker: cur.marker };
    addSeries(next, d);
    scheduleRender(0);
  });
  d.querySelector('.s-fit').value = String(s.fit || 0);
  d.querySelector('.s-marker').value = s.marker || 'auto';
  d.querySelector('.s-ls').value = s.ls || '-';
  const ci = d.querySelector('.s-color');
  ci.addEventListener('input', () => {
    ci.dataset.set = '1';
    d.querySelector('.s-auto').textContent = '';
    d.querySelector('.s-colorreset').hidden = false;
  });
  d.querySelector('.s-colorreset').addEventListener('click', () => {
    delete ci.dataset.set;
    d.querySelector('.s-auto').textContent = 'from palette';
    d.querySelector('.s-colorreset').hidden = true;
    scheduleRender(0);
  });
  d.querySelector('.rm').addEventListener('click', () => {
    if (document.querySelectorAll('.series').length > 1) { d.remove(); scheduleRender(); }
  });
  d.addEventListener('input', () => { seriesBadge(d); scheduleRender(); });
  d.addEventListener('change', () => seriesBadge(d));
  if (replace) replace.replaceWith(d); else $('seriesList').appendChild(d);
  seriesBadge(d);
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
  return [...document.querySelectorAll('.series')].map(seriesFrom);
}
function seriesFrom(d) {
  return ({
    source: d.dataset.source || '',
    x: d.querySelector('.s-x')?.value || '',
    size: d.querySelector('.s-size').value,
    alpha: d.querySelector('.s-alpha').value,
    y: d.querySelector('.s-y').value,
    yerr: d.querySelector('.s-yerr').value,
    xerr: d.querySelector('.s-xerr').value,
    label: d.querySelector('.s-label').value,
    fit: +d.querySelector('.s-fit').value,
    color: d.querySelector('.s-color').dataset.set ? d.querySelector('.s-color').value : '',
    marker: d.querySelector('.s-marker').value,
    ls: d.querySelector('.s-ls').value,
  });
}

const DIST = new Set(['hist', 'kde', 'box', 'violin', 'strip', 'swarm']);
const GROUPED = new Set(['box', 'violin', 'strip', 'swarm']);
const CATEGORICAL = new Set(['bar', ...GROUPED]);

const TYPE_TITLES = { polar: 'Polar options', hist: 'Histogram options', kde: 'Density (KDE) options',
  violin: 'Violin options', box: 'Box plot options' };
function updateBadges() {
  const set = (id, t) => { const el = $('b-' + id); if (el) el.textContent = t; };
  set('data', curFile?.name ? curFile.name + ($('allsheets').checked ? ' · all sheets' : '') : '');
  const k = $('kind').value;
  set('plot', `${$('kind').selectedOptions[0]?.textContent || ''} · ${document.querySelectorAll('.series').length} series`);
  const m = [];
  if ($('hue').value) m.push('colour: ' + $('hue').value);
  if ($('sizeby').value) m.push('size: ' + $('sizeby').value);
  if ($('styleby').value) m.push('shape: ' + $('styleby').value);
  if ($('vector').checked) m.push('vectors');
  if (k === 'hist' && $('histcolor').value) m.push('bins by ' + $('histcolor').value);
  set('map', m.join(' · '));
  const s = [];
  const st = ['refmean', 'refmedian', 'refwmean'].filter((id) => $(id).checked).map((id) => ({ refmean: 'mean', refmedian: 'median', refwmean: 'wtd mean' })[id]);
  if (st.length) s.push(st.join('/') + (document.querySelectorAll('.ref-row').length ? ` (+${document.querySelectorAll('.ref-row').length} col)` : ''));
  if ($('hlines').value || $('vlines').value || $('oneone').checked) s.push('fixed lines');
  if (getSeries().some((x) => x.fit)) s.push('fit' + (+$('ci').value ? ` ${$('ci').value}% band` : ''));
  if ($('groupstats').checked) s.push('group ' + $('groupstat').value);
  set('stats', s.join(' · '));
  const a = [];
  if ($('xlog').checked || $('ylog').checked) a.push('log ' + [$('xlog').checked && 'X', $('ylog').checked && 'Y'].filter(Boolean).join('+'));
  if (['xmin', 'xmax', 'ymin', 'ymax'].some((id) => $(id).value !== '')) a.push('ranges set');
  if ($('xinvert').checked || $('yinvert').checked) a.push('inverted');
  if ($('title').value) a.push('title');
  if ($('grid').value !== 'none') a.push('grid');
  set('axes', a.join(' · '));
  set('style', [$('size').selectedOptions[0]?.textContent.replace(/ \(.*\)/, ''), $('palette').selectedOptions[0]?.textContent,
    $('theme').value !== 'publication' ? $('theme').selectedOptions[0]?.textContent : ''].filter(Boolean).join(' · '));
  const n = document.querySelectorAll('.saved-item').length;
  set('saved', n ? `${n} saved` : '');
}

function updateVisibility() {
  const k = $('kind').value;
  const hist = k === 'hist', bar = k === 'bar', dist = DIST.has(k), grouped = GROUPED.has(k);
  const polar = k === 'polar';
  const mappable = k === 'scatter' || k === 'linemarkers' || polar;
  $('polarRows').hidden = !polar;
  $('refcustom').hidden = $('refcolor').value !== 'custom';
  document.querySelectorAll('.ref-row .r-axis').forEach((el) => { el.closest('.row').hidden = k === 'hist'; });
  const xyk = ['scatter', 'linemarkers', 'line', 'step', 'polar'].includes(k);
  $('groupRows').hidden = !xyk;
  $('groupOpts').hidden = !$('groupstats').checked;
  document.querySelectorAll('.series .s-src').forEach((el) => { el.hidden = !xyk; });
  $('polspanRow').hidden = $('polstyle').value !== 'quarter';
  $('vecRows').hidden = !$('vector').checked;
  $('histRows').hidden = !hist;
  $('binwidthRow').hidden = $('binmode').value !== 'width';
  $('binsRow').hidden = $('binmode').value !== 'count';
  const hc = hist && $('histcolor').value !== '';
  $('histstatRow').hidden = !hc;
  $('histcolorHint').hidden = !hc;
  $('kdeRows').hidden = k !== 'kde';
  $('bwRow').hidden = !(k === 'kde' || (hist && $('histkde').checked));
  $('sec-map').hidden = !(mappable || hc);
  $('mapRows').hidden = !mappable;
  const hueCol = $('hue').value, hueIdx = info ? info.columns.indexOf(hueCol) : -1;
  const hueNumeric = hueIdx >= 0 && info.numeric[hueIdx];
  $('huecatRow').hidden = !(mappable && hueNumeric);
  $('scaleRows').hidden = !((mappable && hueNumeric && !$('huecat').checked) || hc);
  $('huelogRow').hidden = hc && !mappable;
  $('sizeRangeRow').hidden = $('sizeby').value === '';
  const xy = !dist && !bar;
  $('refaxisRow').hidden = !xy;
  $('refwmeanRow').hidden = !xy;
  $('refwmeanHint').hidden = !xy;
  $('vlinesRow').hidden = CATEGORICAL.has(k);
  $('oneoneRow').hidden = !xy || polar;
  document.querySelectorAll('.series .s-look').forEach((el) => { el.hidden = GROUPED.has(k) || k === 'kde'; });
  document.querySelectorAll('.series .s-markerrow').forEach((el) => { el.hidden = !(k === 'scatter' || k === 'linemarkers' || polar) || $('vector').checked; });
  document.querySelectorAll('.series .s-lsrow').forEach((el) => { el.hidden = !(k === 'line' || k === 'linemarkers' || k === 'step'); });
  // collapse the series "more" box away when nothing inside applies to this plot type
  document.querySelectorAll('.series .s-more').forEach((m) => {
    m.hidden = [...m.querySelectorAll(':scope > .row, :scope > .row2, :scope > .row3')].every((el) => el.hidden);
  });
  // the plot-type section only appears for types with their own options
  const typeIds = ['polarRows', 'histRows', 'kdeRows', 'bwRow', 'violinRow', 'distpointsRow'];
  $('sec-type').hidden = typeIds.every((id) => $(id).hidden);
  $('typeTitle').textContent = TYPE_TITLES[k] || 'Plot options';
  updateBadges();
  $('violinRow').hidden = k !== 'violin';
  $('distpointsRow').hidden = !(k === 'box' || k === 'violin');
  $('xRow').hidden = hist || k === 'kde';
  $('groupHint').hidden = !grouped;
  $('xLabel').textContent = grouped ? 'Group by' : polar ? 'Angle column' : 'X column';
  const blank = $('x').querySelector('option[value=""]');
  if (blank) blank.textContent = grouped ? '(series)' : '(row number)';
  $('ticksRow').hidden = $('theme').value !== 'publication';
  $('customSize').hidden = $('size').value !== 'custom';
  document.querySelectorAll('.series .s-err').forEach((el, i) => { el.hidden = dist || (bar && i % 2 === 1); });
  document.querySelectorAll('.series .s-fitrow').forEach((el) => { el.hidden = dist || bar; });
}

// ---------------------------------------------------------------- config
function getConfig({ withExcluded = true } = {}) {
  const c = {};
  for (const el of cfgEls()) {
    c[el.id] = el.type === 'checkbox' ? el.checked : el.type === 'number' ? +el.value : el.value;
  }
  c.series = getSeries();
  c.header = $('header').value;
  c.allsheets = $('allsheets').checked;
  c.refextra = getRefExtra();
  c.ss = ssConfig();
  c.view = currentView;
  c.sheetcol = $('sheetcol').value;
  c.calc = getCalc();
  c.headerrow = +$('headerrow').value || 1;
  c.skipafter = +$('skipafter').value || 0;
  if (curFile?.sheet) c.sheet = curFile.sheet;
  if (withExcluded) c.excluded = [...excluded].sort((a, b) => a - b);
  return c;
}

function applyConfig(c) {
  const cols = {};
  for (const el of cfgEls()) {
    if (!(el.id in c) || el.id === 'x') continue;
    if (el.hasAttribute('data-col')) { cols[el.id] = c[el.id]; continue; }
    if (el.type === 'checkbox') el.checked = !!c[el.id];
    else el.value = c[el.id];
  }
  if (c.header) $('header').value = c.header;
  if ('allsheets' in c) $('allsheets').checked = !!c.allsheets;
  if (Array.isArray(c.refextra)) { refPending = c.refextra; if (info) buildRefExtra(); }
  if (c.ss) applySS(c.ss);
  if (c.view) setView(c.view);
  if (c.sheetcol) $('sheetcol').value = c.sheetcol;
  if (Array.isArray(c.calc)) { $('calcList').innerHTML = ''; c.calc.forEach(addCalcRow); if (c.calc.length) $('calcPanel').open = true; }
  if (c.headerrow) $('headerrow').value = c.headerrow;
  if (c.skipafter !== undefined) $('skipafter').value = c.skipafter;
  if (Array.isArray(c.excluded)) excluded = new Set(c.excluded);
  const pref = { x: c.x ?? $('x').value, series: c.series || [], cols };
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

function pyRender(cfg, fmt, dpi, meta = '') {
  return pyCall(`plotter.render(_args[0], "${fmt}", ${dpi}, _args[1])`, [JSON.stringify(cfg), meta]);
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
    document.querySelectorAll('.series').forEach((d, i) => {
      d.style.setProperty('--sc', r.colors[i] || '#2f6fb0');
      const ci = d.querySelector('.s-color');
      if (!ci.dataset.set && /^#[0-9a-f]{6}$/i.test(r.colors[i] || '')) ci.value = r.colors[i];
    });
    if (info) showRaw();
    $('swatches').innerHTML = r.swatches.map((h) => `<span style="background:${h}" title="${h}"></span>`).join('');
    document.querySelectorAll('[data-exp], #dlScript').forEach((b) => { b.disabled = false; });
    $('resetZoom').disabled = !['xmin', 'xmax', 'ymin', 'ymax'].some((id) => $(id).value !== '');
    $('clearExcl').hidden = excluded.size === 0;
    $('clearExcl').textContent = `Clear exclusions (${excluded.size})`;
    markTableRows();
    try { localStorage.setItem(LS_KEY, JSON.stringify(getConfig({ withExcluded: false }))); } catch {}
    clearTimeout(autoTimer);
    autoTimer = setTimeout(autoSave, 1200);
    $('saveBrowser').disabled = false;
    $('saveProject').disabled = false;
    $('dlGroups').hidden = !r.ngroups;
    updateBadges();
  } catch (e) { showError(e); }
}

// ---------------------------------------------------------------- export
document.querySelectorAll('[data-exp]').forEach((b) => b.addEventListener('click', () => {
  const fmt = b.dataset.exp;
  try {
    const meta = 'SciPlot1:' + b64utf8(JSON.stringify(buildProject({ withData: $('embedData').checked, base64: true }))) + '!';
    const r = pyRender(getConfig(), fmt, +$('dpi').value, meta);
    const bin = Uint8Array.from(atob(r.data), (ch) => ch.charCodeAt(0));
    const mime = { pdf: 'application/pdf', svg: 'image/svg+xml', png: 'image/png' }[fmt];
    download(`${baseName()}.${fmt}`, new Blob([bin], { type: mime }));
  } catch (e) { showError(e); }
}));
$('dlGroups').addEventListener('click', () => {
  try {
    const csv = py.runPython('plotter.group_table_csv()');
    download(`${baseName()}_group_summary.csv`, new Blob([csv], { type: 'text/csv' }));
  } catch (e) { showError(e); }
});
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
  const shown = Math.min(info.preview.length, 2000);
  $('dataInfo').textContent = `${info.nrows} rows × ${info.columns.length} columns` +
    (shown < info.nrows ? ` (first ${shown} shown)` : '') +
    '. Click a row number to exclude or include that row.';
  const head = '<tr><th class="rown">#</th>' + info.columns.map((c, i) =>
    `<th class="${info.numeric[i] ? '' : 'txt'}" title="${info.numeric[i] ? 'numeric' : 'text'}">${esc(c)}</th>`).join('') + '</tr>';
  const body = info.preview.slice(0, 2000).map((r, ri) => `<tr data-row="${ri}"><td class="rown">${ri + 1}</td>` +
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
  if (g?.polar) return false;
  return g && px >= g.x0 * w && px <= g.x1 * w && py_ >= g.y0 * h && py_ <= g.y1 * h;
}
function nearest(px, py_, maxDist = 10) {
  let best = null, bd = maxDist;
  const { w, h } = box();
  for (const s of last?.points || []) {
    for (let k = 0; k < s.pts.length; k++) {
      const [row, x, y, ex] = s.pts[k];
      let qx, qy;
      if (s.px) {
        if (s.px[k][0] == null || s.px[k][1] == null) continue;   // point outside the plotted range
        qx = s.px[k][0] * w; qy = s.px[k][1] * h;
      }
      else {
        if ((last.geom.xlog && x <= 0) || (last.geom.ylog && y <= 0)) continue;
        [qx, qy] = dataToPx(x, y);
      }
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
    ctx.strokeStyle = 'rgba(190,81,3,0.45)';
    ctx.setLineDash([3, 3]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(g.x0 * w, mouse[1]); ctx.lineTo(g.x1 * w, mouse[1]);
    if (!CATEGORICAL.has(last.kind)) { ctx.moveTo(mouse[0], g.y0 * h); ctx.lineTo(mouse[0], g.y1 * h); }
    ctx.stroke();
    ctx.setLineDash([]);
  }
  if (hover) {
    ctx.strokeStyle = tool === 'exclude' ? '#c8372d' : hover.color;
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(hover.qx, hover.qy, 7, 0, 2 * Math.PI); ctx.stroke();
  }
  if (drag?.moved) {
    const x0 = CATEGORICAL.has(last.kind) ? g.x0 * w : drag.x0, x1 = CATEGORICAL.has(last.kind) ? g.x1 * w : drag.x1;
    ctx.fillStyle = 'rgba(190,81,3,0.12)';
    ctx.strokeStyle = 'rgba(190,81,3,0.85)';
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
    $('readout').textContent = CATEGORICAL.has(last.kind) ? `y = ${fmt(dy)}` : `x = ${fmt(dx)}   y = ${fmt(dy)}`;
  } else $('readout').textContent = '';
  if (hover) {
    const rl = info.rowlabels ? `<b>${esc(info.rowlabels.values[hover.row])}</b> · ` : '';
    tip.innerHTML = `${rl}row ${hover.row + 1}${hover.ex ? ' (excluded)' : ''}<br>${esc(hover.label)}<br>` +
      `x = ${fmt(hover.x)}, y = ${fmt(hover.y)}` + mappedValues(hover.row) +
      (tool === 'exclude' ? `<br><i>Click to ${hover.ex ? 'include' : 'exclude'}</i>` : '');
    tip.style.left = hover.qx + 'px';
    tip.style.top = hover.qy + 'px';
    tip.hidden = false;
  } else tip.hidden = true;
  const newRow = hover ? hover.row : null;
  if (newRow !== hoverRow) { hoverRow = newRow; markTableRows(); }
  drawOverlay([mx, my]);
});
function mappedValues(row) {
  const out = [];
  for (const id of ['hue', 'sizeby', 'styleby']) {
    const col = $(id).value;
    const ci = info.columns.indexOf(col);
    if (col && ci >= 0 && info.preview[row]) out.push(`${esc(col)} = ${esc(info.preview[row][ci])}`);
  }
  return out.length ? '<br>' + out.join('<br>') : '';
}

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
canvas.addEventListener('dblclick', () => { if (tool === 'zoom' && !last?.geom?.polar) resetZoom(); });

function zoomTo(d) {
  const [ax, ay] = pxToData(d.x0, d.y0);
  const [bx, by] = pxToData(d.x1, d.y1);
  const r4 = (v) => String(+v.toPrecision(4));
  if (!CATEGORICAL.has(last.kind)) {
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

// ---------------------------------------------------------------- saving plots
// A "project" is everything needed to rebuild a plot: the settings (incl. excluded rows and
// calculated columns) and, optionally, the data file itself.
function bytesToB64(u8) {
  let s = '';
  for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
  return btoa(s);
}
function b64ToBytes(b) { return Uint8Array.from(atob(b), (ch) => ch.charCodeAt(0)); }
function b64utf8(str) { return bytesToB64(new TextEncoder().encode(str)); }

function buildProject({ withData = true, base64 = false } = {}) {
  const p = { sciplot: 1, saved: new Date().toISOString(), cfg: getConfig() };
  if (curFile) {
    p.file = { name: curFile.name, sheet: curFile.sheet ?? null };
    if (withData) p.file[base64 ? 'b64' : 'bytes'] = base64 ? bytesToB64(curFile.bytes) : curFile.bytes;
  }
  return p;
}

function restoreProject(p, how = 'Opened') {
  if (!p || !p.cfg) throw new Error('Not a SciPlot project');
  info = null;                                   // columns come from the project's data
  applyConfig(p.cfg);
  excluded = new Set(p.cfg.excluded || []);
  $('headerRows').hidden = $('header').value !== 'row';
  updateVisibility();
  const bytes = p.file?.bytes ? new Uint8Array(p.file.bytes) : p.file?.b64 ? b64ToBytes(p.file.b64) : null;
  if (p.file && bytes) {
    curFile = { name: p.file.name, bytes, sheet: p.cfg.sheet ?? p.file.sheet ?? null };
    $('fname').textContent = p.file.name;
    keepSetup = false;
    if (py && PYSRC) loadData(); else status('Starting Python… (your plot will open when ready)');
    showBanner(`${how} “${p.name || p.file.name}” (saved ${new Date(p.saved).toLocaleString()}).`);
  } else if (p.file) {
    curFile = { name: p.file.name, bytes: null, sheet: p.cfg.sheet ?? p.file.sheet ?? null };
    keepSetup = true;
    $('fname').textContent = '';
    showBanner(`Setup loaded. Now load the data file “${p.file.name}” and the plot will be rebuilt.`);
  }
}

async function openProjectBytes(name, bytes) {
  try {
    let p;
    if (/\.(sciplot|json)$/i.test(name)) {
      p = JSON.parse(new TextDecoder().decode(bytes));
      if (!p.sciplot && p.kind) {             // a style/settings file
        applyConfig(p); scheduleRender(0); showBanner('Style applied.'); return;
      }
    } else {
      const txt = new TextDecoder('latin1').decode(bytes);
      const m = txt.match(/SciPlot1:([A-Za-z0-9+\/=]+)!/);
      if (!m) throw new Error(`No SciPlot setup found in ${name}. Only figures exported from SciPlot can be reopened.`);
      p = JSON.parse(new TextDecoder().decode(b64ToBytes(m[1])));
    }
    restoreProject(p);
  } catch (e) { showError(e); }
}

$('saveProject').addEventListener('click', () => {
  const nm = ($('saveName').value.trim() || (curFile ? curFile.name.replace(/\.[^.]+$/, '') : 'plot'));
  const p = buildProject({ withData: true, base64: true });
  p.name = nm;
  download(nm.replace(/[^\w\- ]+/g, '_') + '.sciplot', new Blob([JSON.stringify(p)], { type: 'application/json' }));
});
$('openProject').addEventListener('click', () => $('projectFile').click());
$('projectFile').addEventListener('change', async (e) => {
  const f = e.target.files[0];
  if (f) openProjectBytes(f.name, new Uint8Array(await f.arrayBuffer()));
  e.target.value = '';
});

// ---- browser library (IndexedDB), including an automatic copy of the current plot
const DB = {
  open() {
    return new Promise((res, rej) => {
      const r = indexedDB.open('sciplot', 1);
      r.onupgradeneeded = () => r.result.createObjectStore('plots', { keyPath: 'id' });
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    });
  },
  async tx(mode, fn) {
    const db = await this.open();
    return new Promise((res, rej) => {
      const t = db.transaction('plots', mode);
      const out = fn(t.objectStore('plots'));
      t.oncomplete = () => res(out?.result);
      t.onerror = () => rej(t.error);
    });
  },
  put(o) { return this.tx('readwrite', (s) => s.put(o)); },
  get(id) { return this.tx('readonly', (s) => s.get(id)); },
  all() { return this.tx('readonly', (s) => s.getAll()); },
  del(id) { return this.tx('readwrite', (s) => s.delete(id)); },
};

function thumbnail() {
  const img = $('fig');
  if (!img.naturalWidth) return '';
  const cv = document.createElement('canvas');
  cv.width = 168; cv.height = 126;
  const g = cv.getContext('2d');
  g.fillStyle = '#fff'; g.fillRect(0, 0, 168, 126);
  const s = Math.min(168 / img.naturalWidth, 126 / img.naturalHeight);
  const w = img.naturalWidth * s, h = img.naturalHeight * s;
  g.drawImage(img, (168 - w) / 2, (126 - h) / 2, w, h);
  return cv.toDataURL('image/png');
}

let autoTimer = null;
async function autoSave() {
  if (!curFile?.bytes || !info) return;
  try { await DB.put({ id: '__last__', name: curFile.name, saved: Date.now(), thumb: '', project: buildProject() }); } catch {}
}

async function refreshSaved() {
  let items = [];
  try { items = (await DB.all()).filter((o) => o.id !== '__last__').sort((a, b) => b.saved - a.saved); } catch {}
  $('savedEmpty').hidden = items.length > 0;
  setTimeout(updateBadges, 0);
  $('savedList').innerHTML = items.map((o) => `
    <div class="saved-item" data-id="${esc(o.id)}" title="Open this plot">
      ${o.thumb ? `<img src="${o.thumb}" alt="">` : '<span></span>'}
      <div><div class="nm">${esc(o.name)}</div>
        <div class="sub">${esc(o.project?.file?.name || '')} · ${new Date(o.saved).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}</div></div>
      <button type="button" class="del" title="Delete">✕</button>
    </div>`).join('');
}
$('savedList').addEventListener('click', async (e) => {
  const item = e.target.closest('.saved-item');
  if (!item) return;
  const id = item.dataset.id;
  if (e.target.closest('.del')) {
    const b = e.target.closest('.del');
    if (b.dataset.confirm) { await DB.del(id); refreshSaved(); }
    else { b.dataset.confirm = '1'; b.textContent = 'Delete?'; setTimeout(() => { b.textContent = '✕'; delete b.dataset.confirm; }, 3000); }
    return;
  }
  const o = await DB.get(id);
  if (o) { $('saveName').value = o.name; restoreProject({ ...o.project, name: o.name }); }
});
$('saveBrowser').addEventListener('click', async () => {
  if (!curFile?.bytes) return;
  const name = $('saveName').value.trim() || `${curFile.name} · ${new Date().toLocaleDateString()}`;
  try {
    const existing = (await DB.all()).find((o) => o.name === name && o.id !== '__last__');
    await DB.put({ id: existing?.id || 'p' + Date.now(), name, saved: Date.now(), thumb: thumbnail(), project: buildProject() });
    $('saveName').value = name;
    flash($('saveBrowser'), existing ? 'Updated' : 'Saved');
    refreshSaved();
  } catch (e) { showError('Could not save in this browser: ' + e); }
});

function showBanner(text) { $('bannerText').textContent = text; $('banner').hidden = false; }
function hideBanner() { $('banner').hidden = true; }
$('bannerClose').addEventListener('click', hideBanner);
async function startFresh() {
  try { await DB.del('__last__'); } catch {}
  try { localStorage.removeItem(LS_KEY); } catch {}
  curFile = null; info = null; last = null; excluded.clear(); keepSetup = false;
  applyConfig({ ...DEFAULTS, series: [], calc: [] });
  $('fname').textContent = ''; $('saveName').value = '';
  $('stage').classList.remove('show'); $('placeholder').hidden = false;
  $('dataPanel').hidden = true; $('msgs').textContent = ''; $('err').textContent = '';
  document.querySelectorAll('[data-exp], #dlScript, #saveBrowser, #saveProject').forEach((b) => { b.disabled = true; });
  hideBanner();
}
$('bannerFresh').addEventListener('click', startFresh);
$('newPlot').addEventListener('click', startFresh);

// ---------------------------------------------------------------- Subsampling tab
// Running mean / running 2σ of values drawn in a random order (how many measurements are enough?).
let currentView = 'plot';
let ssPending = null;          // settings waiting for data
let ssFollow = true;           // slider follows the last value (show the full run)
let ssLast = null, ssTimer = null, ssPlaying = false, ssTolTouched = false;
let ssTouched = new Set();     // column menus the user has set (others get sensible defaults)
const SS_DEFAULT = { mean: /^dpar$/i, '2sd': /angle.*c/i };

function setView(v) {
  currentView = v === 'ss' ? 'ss' : 'plot';
  $('plotView').hidden = currentView !== 'plot';
  $('ssView').hidden = currentView !== 'ss';
  document.querySelectorAll('.tabs button').forEach((b) => b.classList.toggle('on', b.dataset.view === currentView));
  if (currentView === 'ss') ssSchedule(0);
}
document.querySelectorAll('.tabs button').forEach((b) => b.addEventListener('click', () => {
  setView(b.dataset.view);
  clearTimeout(autoTimer); autoTimer = setTimeout(autoSave, 800);
}));

function ssConfig() {
  const o = {};
  document.querySelectorAll('[data-ss]').forEach((el) => {
    o[el.id.slice(3)] = el.type === 'checkbox' ? el.checked : el.value;
  });
  o.n = ssFollow ? 1e9 : +$('ss-n').value;
  o.touched = [...ssTouched];
  return o;
}
function applySS(o) {
  document.querySelectorAll('[data-ss]').forEach((el) => {
    const k = el.id.slice(3);
    if (!(k in o) || el.tagName === 'SELECT') return;
    if (el.type === 'checkbox') el.checked = !!o[k]; else el.value = o[k];
  });
  ['mode', 'size', 'runcolor', 'legend', 'readoutpos'].forEach((k) => { if (k in o) $('ss-' + k).value = o[k]; });
  ssTouched = new Set(o.touched || []);
  ssFollow = !(o.n && o.n < 1e9);
  if (!ssFollow) $('ss-n').value = o.n;
  ssPending = o;
  if (info) fillSS(o);
}

function ssTable() {
  const src = $('ss-source').value;
  return src && info?.tables?.[src] ? info.tables[src] : info;
}
function ssOptions(sel, list, want, blank) {
  sel.innerHTML = (blank ? `<option value="">${blank}</option>` : '') +
    list.map((c) => `<option ${c === want ? 'selected' : ''}>${esc(c)}</option>`).join('');
  sel.value = list.includes(want) ? want : (blank ? '' : list[0] ?? '');
}
function fillSS(pref) {
  if (!info) return;
  // before the menus have ever been filled, treat column choices as "not chosen yet"
  pref = pref || ssPending || ($('ss-value').options.length ? ssConfig() : { mode: $('ss-mode').value });
  ssPending = null;
  $('ssNoData').hidden = true;
  const order = info.table_order || Object.keys(info.tables || {});
  $('ss-source').innerHTML = '<option value="">Main table</option>' +
    order.map((k) => `<option value="${esc(k)}">Sheet: ${esc(k)}</option>`).join('');
  $('ss-source').value = order.includes(pref.source) ? pref.source : '';
  $('ssSourceRow').hidden = !order.length;
  const t = ssTable();
  const nums = t.columns.filter((c, i) => t.numeric[i]);
  const mode = pref.mode || $('ss-mode').value;
  const dflt = nums.find((c) => SS_DEFAULT[mode].test(c)) ?? nums[0];
  const pick = (k, list, auto) => (ssTouched.has(k) && (pref[k] === '' || list.includes(pref[k])) ? pref[k] : auto);
  ssOptions($('ss-value'), nums, ssTouched.has('value') && nums.includes(pref.value) ? pref.value : dflt);
  ssOptions($('ss-anglecol'), nums, pick('anglecol', nums, nums.find((c) => /angle.*c/i.test(c)) ?? ''), 'none');
  ssOptions($('ss-dparcol'), nums, pick('dparcol', nums, nums.find((c) => /^dpar$/i.test(c)) ?? ''), 'none');
  const sheetcol = $('allsheets').checked && !$('ss-source').value ? ($('sheetcol').value || 'Sheet') : '';
  ssOptions($('ss-wherecol'), t.columns, pick('wherecol', t.columns, t.columns.includes(sheetcol) ? sheetcol : ''), '(all rows)');
  fillWhereVals(pref.whereval);
  ssVisibility();
}
function fillWhereVals(want) {
  const col = $('ss-wherecol').value;
  $('ssWhereValRow').hidden = !col;
  if (!col || !py) { $('ss-whereval').innerHTML = ''; return; }
  const vals = JSON.parse(py.runPython(`plotter.ss_unique(${JSON.stringify(col)}, ${JSON.stringify($('ss-source').value)})`));
  ssOptions($('ss-whereval'), vals, vals.includes(String(want)) ? String(want) : vals[0]);
}
function ssVisibility() {
  $('ssSdRow').hidden = $('ss-mode').value !== 'mean';
  ssBadges();
}
function ssBadges() {
  const set = (id, t) => { const el = $('b-' + id); if (el) el.textContent = t; };
  const w = $('ss-wherecol').value;
  set('ssdata', [$('ss-source').value ? 'sheet ' + $('ss-source').value : '', w ? `${w} = ${$('ss-whereval').value}` : 'all rows'].filter(Boolean).join(' · '));
  set('sswhat', `${$('ss-mode').value === 'mean' ? 'mean' : '2σ'} of ${$('ss-value').value || '…'}`);
  const f = [];
  if ($('ss-amin').value || $('ss-amax').value) f.push(`angle ${$('ss-amin').value || '…'}–${$('ss-amax').value || '…'}`);
  if ($('ss-dmin').value || $('ss-dmax').value) f.push(`Dpar ${$('ss-dmin').value || '…'}–${$('ss-dmax').value || '…'}`);
  set('ssfilt', f.join(' · ') || 'none');
  set('ssrand', `seed ${$('ss-seed').value}${+$('ss-ghosts').value ? ` · ${$('ss-ghosts').value} ghosts` : ''} · ±${$('ss-tol').value}%`);
  set('sslook', $('ss-size').selectedOptions[0]?.textContent.replace(/ \(.*\)/, '') || '');
}

function ssSchedule(d = 150) { clearTimeout(ssTimer); ssTimer = setTimeout(ssRender, d); }
function ssRender() {
  if (!py || !info || currentView !== 'ss') return;
  $('ssErr').textContent = '';
  try {
    const r = pyCall('plotter.subsample_render(_args[0], "png", 160)', [JSON.stringify(getConfig())]);
    ssLast = r;
    const s = $('ss-n');
    s.min = r.start; s.max = Math.max(r.N, r.start + 1);
    if (ssFollow) s.value = s.max;
    $('ssNval').textContent = r.N ? `n = ${r.n} of ${r.N}` : '';
    const img = $('ssFig');
    img.onload = () => { img.style.width = Math.round(img.naturalWidth / 160 * 130) + 'px'; };
    img.src = 'data:image/png;base64,' + r.data;
    $('ssStage').classList.add('show');
    $('ssPlaceholder').hidden = true;
    $('ssMsgs').textContent = r.msgs.join('\n');
    clearTimeout(autoTimer); autoTimer = setTimeout(autoSave, 1200);
    try { localStorage.setItem(LS_KEY, JSON.stringify(getConfig({ withExcluded: false }))); } catch {}
  } catch (e) { $('ssErr').textContent = String(e?.message ?? e).split('\n').filter(Boolean).slice(-2).join('\n'); }
}

document.querySelectorAll('[data-ss]').forEach((el) => {
  el.addEventListener(el.type === 'text' || el.type === 'number' ? 'input' : 'change', () => {
    const k = el.id.slice(3);
    if (k === 'tol') ssTolTouched = true;
    if (el.tagName === 'SELECT') ssTouched.add(k);
    if (k === 'source') { fillSS({ ...ssConfig(), value: undefined, wherecol: undefined, whereval: undefined }); }
    if (k === 'wherecol') fillWhereVals();
    if (k === 'mode') {
      const t = ssTable();
      const nums = t.columns.filter((c, i) => t.numeric[i]);
      const d = nums.find((c) => SS_DEFAULT[el.value].test(c));
      if (d) $('ss-value').value = d;
      if (!ssTolTouched) $('ss-tol').value = el.value === 'mean' ? 2 : 10;
      ssVisibility();
    }
    ssBadges();
    ssSchedule(k === 'seed' || k === 'ghosts' ? 250 : 120);
  });
});
$('ss-n').addEventListener('input', () => {
  ssFollow = +$('ss-n').value >= +$('ss-n').max;
  ssSchedule(30);
});
$('ssNewOrder').addEventListener('click', () => { $('ss-seed').value = Math.floor(Math.random() * 1e6); ssSchedule(0); });
$('ssPlay').addEventListener('click', async () => {
  if (ssPlaying) { ssPlaying = false; return; }
  if (!ssLast?.N) return;
  ssPlaying = true;
  $('ssPlay').textContent = '❚❚ Pause';
  const s = $('ss-n'), N = ssLast.N;
  let n = +s.value >= N ? +s.min : +s.value;
  const step = Math.max(1, Math.ceil(N / 90));     // ~90 frames for a full run
  ssFollow = false;
  while (ssPlaying && n < N) {
    n = Math.min(N, n + step);
    s.value = n;
    ssRender();
    await new Promise((res) => setTimeout(res, +$('ssSpeed').value));
  }
  ssPlaying = false;
  ssFollow = +s.value >= N;
  $('ssPlay').textContent = '▶ Play';
});
document.querySelectorAll('[data-ssexp]').forEach((b) => b.addEventListener('click', () => {
  const fmt = b.dataset.ssexp;
  try {
    const meta = 'SciPlot1:' + b64utf8(JSON.stringify(buildProject({ withData: $('embedData').checked, base64: true }))) + '!';
    const r = pyCall(`plotter.subsample_render(_args[0], "${fmt}", ${+$('dpi').value}, _args[1])`, [JSON.stringify(getConfig()), meta]);
    const bin = Uint8Array.from(atob(r.data), (ch) => ch.charCodeAt(0));
    const mime = { pdf: 'application/pdf', svg: 'image/svg+xml', png: 'image/png' }[fmt];
    download(`${baseName()}_running_${$('ss-mode').value}.${fmt}`, new Blob([bin], { type: mime }));
  } catch (e) { $('ssErr').textContent = String(e); }
}));
$('ssCsv').addEventListener('click', () => {
  try {
    py.globals.set('_args', py.toPy([JSON.stringify(getConfig())]));
    const csv = py.runPython('plotter.subsample_csv(_args[0])');
    download(`${baseName()}_running_${$('ss-mode').value}.csv`, new Blob([csv], { type: 'text/csv' }));
  } catch (e) { $('ssErr').textContent = String(e); }
});

// ---------------------------------------------------------------- sections: remember open / closed
const SEC_KEY = 'sciplot-sections';
(() => {
  let st = {};
  try { st = JSON.parse(localStorage.getItem(SEC_KEY)) || {}; } catch {}
  document.querySelectorAll('details.sec[id]').forEach((d) => {
    if (d.id in st) d.open = !!st[d.id];
    d.addEventListener('toggle', () => {
      try {
        const cur = JSON.parse(localStorage.getItem(SEC_KEY) || '{}');
        cur[d.id] = d.open;
        localStorage.setItem(SEC_KEY, JSON.stringify(cur));
      } catch {}
    });
  });
})();

// ---------------------------------------------------------------- start-up
DEFAULTS = getConfig({ withExcluded: false });
delete DEFAULTS.series;
delete DEFAULTS.x;
['source', 'value', 'anglecol', 'dparcol', 'wherecol', 'whereval'].forEach((k) => delete DEFAULTS.ss[k]);
(async () => {
  // a shared settings link wins; otherwise reopen the last plot (with its data); otherwise last settings
  const m = location.hash.match(/s=([\w-]+)/);
  let saved = null;
  if (m) { try { saved = decodeCfg(m[1]); } catch {} }
  let lastSession = null;
  if (!saved) { try { lastSession = await DB.get('__last__'); } catch {} }
  if (lastSession?.project?.file?.bytes) {
    restoreProject(lastSession.project, 'Reopened your last plot,');
  } else {
    if (!saved) { try { saved = JSON.parse(localStorage.getItem(LS_KEY)); } catch {} }
    if (saved) applyConfig(saved);
  }
  $('headerRows').hidden = $('header').value !== 'row';
  updateVisibility();
  refreshSaved();
})();
boot();
