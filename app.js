/* NeverRed — contabilidad personal por partida doble. Sin dependencias. */
'use strict';

const LS_KEY = 'neverred_v1';
// Versión de esta carcasa: debe subir con cada release (ver checklist).
// Si el servidor informa otra, la carcasa está obsoleta y se refresca sola.
const NEVERRED_BUILD = '2.5.0';
// Lógica contable pura compartida con los tests (lib/contabilidad.js)
const TYPES = NR.TYPES;
const DEBIT_NATURE = NR.DEBIT_NATURE;

const BASE_ACCOUNTS = [
  { code: '570', name: 'Caja · Efectivo', type: 'Activo' },
  { code: '572', name: 'Banco cuenta principal', type: 'Activo' },
  { code: '573', name: 'Ahorros', type: 'Activo' },
  { code: '520', name: 'Tarjeta de crédito', type: 'Pasivo' },
  { code: '521', name: 'Préstamos por pagar', type: 'Pasivo' },
  { code: '101', name: 'Capital inicial', type: 'Patrimonio' },
  { code: '700', name: 'Sueldos y salarios', type: 'Ingreso' },
  { code: '701', name: 'Ingresos extra', type: 'Ingreso' },
  { code: '600', name: 'Alquiler / Vivienda', type: 'Gasto' },
  { code: '601', name: 'Comida y supermercado', type: 'Gasto' },
  { code: '602', name: 'Transporte', type: 'Gasto' },
  { code: '603', name: 'Ocio y suscripciones', type: 'Gasto' },
  { code: '604', name: 'Salud', type: 'Gasto' },
];

// ---------- Estado ----------
let currentUser = null;
let sessionToken = null;
try { sessionToken = localStorage.getItem('neverred_session') || null; } catch { sessionToken = null; }
let state = load() || { user: { name: '', currency: 'EUR' }, accounts: [], entries: [], seq: 1 };
if (!state.accounts.length && !state.entries.length && !localStorage.getItem(LS_KEY)) {
  state.accounts = BASE_ACCOUNTS.map((a, i) => ({ id: uid(), ...a, archived: false, _seed: i }));
}
let editingEntryId = null;
let editingAccountId = null;
let saveTimer = null;
let serverEtag = null;
let conflictData = null;
function setSync(s) {
  const b = document.getElementById('syncBadge');
  if (!b) return;
  b.className = 'badge ' + (s === 'ok' ? 'ok' : 'bad');
  b.textContent = s === 'ok' ? '✓ sincronizado' : (s === 'dirty' ? '● guardando…' : '⚠ revisa sincronización');
  b.title = s === 'ok' ? 'Datos sincronizados con la base de datos'
    : (s === 'dirty' ? 'Subiendo cambios…' : 'Sin conexión o conflicto: tus cambios están a salvo en este navegador');
}

function uid() { return Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4); }
function load() { try { const raw = localStorage.getItem(LS_KEY); return raw ? JSON.parse(raw) : null; } catch { return null; } }
/** Clave de caché local por usuario (la fuente de verdad es la BD vía API). */
function userKey() { return LS_KEY + ':' + (currentUser ? currentUser.id : 'local'); }
// ---------- Telemetría (solo store; sin forward) ----------
// Opt-in por usuario: apagado = track() no hace nada. La cola vive en memoria
// y se envía por lotes a /api/telemetry (allowlist en el servidor).
let telemetryOn = false;
let telemetryAsked = true; // si el servidor no dice lo contrario, no preguntar
let telemetryQueue = [];
function track(event, props) {
  if (!telemetryOn || FROM_FILE) return;
  telemetryQueue.push({ event, props: props || {} });
  if (telemetryQueue.length > 500) telemetryQueue.splice(0, telemetryQueue.length - 500);
}
async function flushTelemetry() {
  if (!telemetryOn || !telemetryQueue.length || !currentUser) return;
  const batch = telemetryQueue.splice(0, 100);
  try {
    const res = await fetch(api('/api/telemetry'), {
      method: 'POST', headers: authHeaders(), body: JSON.stringify({ events: batch }),
    });
    // 403 = consentimiento revocado en otro dispositivo: no reintentar.
    if (!res.ok && res.status !== 403) telemetryQueue.unshift(...batch);
  } catch { telemetryQueue.unshift(...batch); }
}
setInterval(flushTelemetry, 30000);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') flushTelemetry();
});
async function loadTelemetryConsent() {
  telemetryOn = false;
  telemetryAsked = true;
  if (FROM_FILE || !currentUser) return;
  try {
    const res = await fetch(api('/api/telemetry-consent'), { headers: authHeaders() });
    const data = await res.json().catch(() => ({}));
    telemetryOn = !!data.enabled;
    telemetryAsked = !!data.asked;
  } catch {}
}
async function answerTelemetry(enabled) {
  try {
    await fetch(api('/api/telemetry-consent'), {
      method: 'PUT', headers: authHeaders(),
      body: JSON.stringify({ enabled }),
    });
  } catch {}
  telemetryOn = enabled;
  telemetryAsked = true;
  if (!enabled) telemetryQueue = [];
  consentModal.hidden = true;
}
document.getElementById('btnConsentYes').addEventListener('click', () => answerTelemetry(true));
document.getElementById('btnConsentNo').addEventListener('click', () => answerTelemetry(false));
function save() {
  try { localStorage.setItem(userKey(), JSON.stringify(state)); } catch {}
  queueSync();
}
/** Sube los datos a la base de datos (con anti-rebote para no saturar la API). */
function queueSync() {
  if (!sessionToken && !currentUser) return;
  setSync('dirty');
  clearTimeout(saveTimer);
  saveTimer = setTimeout(syncToServer, 800);
}
async function syncToServer(force) {
  if (!sessionToken && !currentUser) return null;
  try {
    const headers = authHeaders();
    if (serverEtag && !force) headers['If-Match'] = serverEtag;
    const res = await fetch(api('/api/data'), {
      method: 'PUT', headers,
      body: JSON.stringify({ accounts: state.accounts, entries: state.entries, seq: state.seq, currency: state.user.currency, budgets: state.budgets || {}, recurring: state.recurring || [] }),
    });
    if (res.status === 401) { endSession(); return false; }
    const data = await res.json().catch(() => ({}));
    if (res.status === 409 && data.server) {
      conflictData = data;
      track('conflicto_409');
      document.getElementById('conflictBar').hidden = false;
      setSync('conflict');
      return false;
    }
    if (res.ok) {
      serverEtag = res.headers.get('ETag') || data.etag || serverEtag;
      setSync('ok');
      return true;
    }
    setSync('conflict');
    return false;
  } catch { return null; /* sin conexión: queda la copia local y se reintenta luego */ }
}
document.getElementById('btnConflictReload').addEventListener('click', () => {
  if (!conflictData) return;
  const s = conflictData.server;
  state.accounts = s.accounts || [];
  state.entries = s.entries || [];
  state.seq = s.seq || state.entries.length + 1;
  state.budgets = s.budgets || {};
  state.recurring = s.recurring || [];
  serverEtag = conflictData.etag || null;
  conflictData = null;
  document.getElementById('conflictBar').hidden = true;
  save(); renderAll();
});
document.getElementById('btnConflictKeep').addEventListener('click', async () => {
  conflictData = null;
  document.getElementById('conflictBar').hidden = true;
  await syncToServer(true); // sobrescribe con lo mío
  renderAll();
});
function todayISO() { return new Date().toISOString().slice(0, 10); }
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function fmt(n) {
  try { return new Intl.NumberFormat('es-ES', { style: 'currency', currency: state.user.currency || 'EUR' }).format(n || 0); }
  catch { return (n || 0).toFixed(2); }
}
function fmtNum(n) { return new Intl.NumberFormat('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n || 0); }
function round2(n) { return NR.round2(n); }

// ---------- Lógica contable (delegada en NR, testeada en frontend/tests/) ----------
const accById = id => state.accounts.find(a => a.id === id);
function entryTotal(e) { return NR.entryTotal(e); }
/** Totales por cuenta: {id: {debit, credit}} */
function totalsByAccount() { return NR.totalsByAccount(state.accounts, state.entries); }
/** Saldo contable signed según naturaleza: + significa saldo normal. */
function balanceOf(acc, t) { return NR.balanceOf(acc, t); }
function typeTotals() { return NR.typeTotals(state.accounts, state.entries); }
function isBooksBalanced() { return NR.isBooksBalanced(state.accounts, state.entries); }

// ---------- Navegación ----------
document.querySelectorAll('.tab').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById('view-' + b.dataset.view).classList.add('active');
  if (b.dataset.view) track('vista_' + b.dataset.view);
}));

// ---------- Dashboard ----------
function renderDashboard() {
  document.getElementById('userName').textContent = state.user.name || 'contable';
  const tt = typeTotals();
  const net = round2(tt.Activo - tt.Pasivo);
  const m = todayISO().slice(0, 7);
  let inc = 0, exp = 0;
  for (const e of state.entries) {
    if (!e.date.startsWith(m)) continue;
    for (const l of e.lines) {
      const a = accById(l.accountId); if (!a) continue;
      if (a.type === 'Ingreso') inc += (Number(l.credit) || 0) - (Number(l.debit) || 0);
      if (a.type === 'Gasto') exp += (Number(l.debit) || 0) - (Number(l.credit) || 0);
    }
  }
  const res = round2(inc - exp);
  setKpi('kpiNet', net); setKpi('kpiAssets', tt.Activo); setKpi('kpiLiab', tt.Pasivo); setKpi('kpiResult', res);
  const ok = isBooksBalanced();
  const badge = document.getElementById('balanceBadge');
  badge.textContent = ok ? '✓ Cuadrado' : '✗ Descuadre';
  badge.className = 'badge ' + (ok ? 'ok' : 'bad');
  document.getElementById('equationStatus').textContent =
    `${fmt(tt.Activo)} = ${fmt(tt.Pasivo)} + ${fmt(tt.Patrimonio)} + (${fmt(tt.Ingreso)} − ${fmt(tt.Gasto)})` + (ok ? '  ✓ todo cuadra' : '  ✗ revisa tus asientos');
  const rec = [...state.entries].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 5);
  document.getElementById('recentList').innerHTML = rec.length ? rec.map(entryCard).join('')
    : '<p class="muted">Aún no hay asientos. Crea el primero con “+ Nuevo asiento”.</p>';
  drawChart();
  renderBudgetAlerts();
}
function setKpi(id, v) {
  const el = document.getElementById(id);
  el.textContent = fmt(v);
  el.style.color = v < 0 ? 'var(--red)' : (v > 0 && id === 'kpiResult' ? 'var(--green)' : 'var(--text)');
}

function last6Months() {
  const out = []; const d = new Date(); d.setDate(1);
  for (let i = 5; i >= 0; i--) { const x = new Date(d); x.setMonth(d.getMonth() - i); out.push(x.toISOString().slice(0, 7)); }
  return out;
}
function drawChart() {
  const cv = document.getElementById('chartFlow'); const ctx = cv.getContext('2d');
  const W = cv.width, H = cv.height; ctx.clearRect(0, 0, W, H);
  const months = last6Months();
  const data = months.map(m => {
    let inc = 0, exp = 0;
    for (const e of state.entries) {
      if (!e.date.startsWith(m)) continue;
      for (const l of e.lines) {
        const a = accById(l.accountId); if (!a) continue;
        if (a.type === 'Ingreso') inc += (Number(l.credit) || 0) - (Number(l.debit) || 0);
        if (a.type === 'Gasto') exp += (Number(l.debit) || 0) - (Number(l.credit) || 0);
      }
    }
    return { m: m.slice(5), inc: round2(inc), exp: round2(exp) };
  });
  const max = Math.max(1, ...data.map(d => Math.max(d.inc, d.exp)));
  const bw = W / (data.length * 3);
  data.forEach((d, i) => {
    const x = i * (W / data.length) + W / data.length / 2;
    const hi = (d.inc / max) * (H - 40), he = (d.exp / max) * (H - 40);
    ctx.fillStyle = '#22c55e'; ctx.fillRect(x - bw - 2, H - 20 - hi, bw, hi);
    ctx.fillStyle = '#ef4444'; ctx.fillRect(x + 2, H - 20 - he, bw, he);
    ctx.fillStyle = '#9aa5b4'; ctx.font = '11px system-ui'; ctx.textAlign = 'center';
    ctx.fillText(d.m, x, H - 6);
  });
  ctx.fillStyle = '#9aa5b4'; ctx.textAlign = 'left';
  ctx.fillStyle = '#22c55e'; ctx.fillRect(8, 8, 10, 10);
  ctx.fillStyle = '#9aa5b4'; ctx.fillText('Ingresos', 22, 17);
  ctx.fillStyle = '#ef4444'; ctx.fillRect(95, 8, 10, 10);
  ctx.fillStyle = '#9aa5b4'; ctx.fillText('Gastos', 109, 17);
  document.getElementById('chartFlowData').innerHTML =
    `<table class="table"><thead><tr><th>Mes</th><th class="num">Ingresos</th><th class="num">Gastos</th></tr></thead><tbody>` +
    data.map(d => `<tr><td>${d.m}</td><td class="num">${fmt(d.inc)}</td><td class="num">${fmt(d.exp)}</td></tr>`).join('') +
    `</tbody></table>`;
}

function renderBudgetAlerts() {
  const m = todayISO().slice(0, 7);
  const spent = {};
  for (const e of state.entries) {
    if (!e.date.startsWith(m)) continue;
    for (const l of e.lines) {
      const a = accById(l.accountId);
      if (a && a.type === 'Gasto') spent[a.id] = round2((spent[a.id] || 0) + (Number(l.debit) || 0) - (Number(l.credit) || 0));
    }
  }
  const hits = state.accounts
    .filter(a => a.type === 'Gasto' && !a.archived && Number(state.budgets[a.id]) > 0)
    .map(a => ({ a, s: spent[a.id] || 0, b: Number(state.budgets[a.id]) }))
    .filter(x => x.s / x.b >= 0.8);
  const card = document.getElementById('budgetAlertsCard');
  card.hidden = !hits.length;
  document.getElementById('budgetAlerts').innerHTML = hits.map(x =>
    `<div class="alert-row"><span>${x.s > x.b ? '🔴' : '🟡'}</span>
     <span>${esc(x.a.name)}: ${fmt(x.s)} de ${fmt(x.b)}</span>
     <button class="btn ghost small" data-goto="informes">Ver</button></div>`).join('');
}
document.getElementById('budgetAlerts').addEventListener('click', ev => {
  if (ev.target.dataset.goto) switchTab(ev.target.dataset.goto);
});

// ---------- Importar CSV del banco ----------
const csvModal = document.getElementById('csvModal');
let csvRows = [];
function fillCsvSelects() {
  const opts = type => state.accounts.filter(a => a.type === type && !a.archived)
    .map(a => `<option value="${a.id}">${esc(a.code + ' · ' + a.name)}</option>`).join('');
  document.getElementById('csvBank').innerHTML = opts('Activo') || '<option value="">—</option>';
  document.getElementById('csvIncome').innerHTML = opts('Ingreso') || '<option value="">—</option>';
  document.getElementById('csvExpense').innerHTML = opts('Gasto') || '<option value="">—</option>';
}
function parseCsv(text) {
  const lines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
  if (!lines.length) return { rows: [], bad: 0 };
  const delim = (lines[0].split(';').length >= lines[0].split(',').length) ? ';' : ',';
  const rows = [];
  let bad = 0;
  for (const ln of lines.slice(0, 500)) {
    const cols = ln.split(delim).map(c => c.trim().replace(/^"|"$/g, ''));
    if (cols.length < 3) { bad++; continue; }
    let iso = null;
    let m = cols[0].match(/^(\d{2})[\/\-.](\d{2})[\/\-.](\d{4})$/);
    if (m) iso = `${m[3]}-${m[2]}-${m[1]}`;
    else if (/^\d{4}-\d{2}-\d{2}$/.test(cols[0])) iso = cols[0];
    let num = cols[2].replace(/\s/g, '');
    if (num.includes(',')) num = num.replace(/\./g, '').replace(',', '.');
    const amount = round2(num);
    if (!iso || !cols[1] || !isFinite(amount) || amount === 0) { bad++; continue; }
    const dt = new Date(iso + 'T00:00:00');
    if (isNaN(dt)) { bad++; continue; }
    rows.push({ date: iso, desc: cols[1].slice(0, 120), amount });
  }
  return { rows, bad };
}
document.getElementById('btnCsv').addEventListener('click', () => {
  fillCsvSelects();
  document.getElementById('csvError').textContent = '';
  document.getElementById('csvPreview').innerHTML = '';
  document.getElementById('btnSaveCsv').disabled = true;
  csvRows = [];
  csvModal.hidden = false;
  document.getElementById('csvBank').focus();
});
document.getElementById('btnCancelCsv').addEventListener('click', () => csvModal.hidden = true);
document.getElementById('csvFileInput').addEventListener('change', ev => {
  const f = ev.target.files[0];
  if (!f) return;
  const r = new FileReader();
  r.onload = () => {
    const { rows, bad } = parseCsv(String(r.result || ''));
    csvRows = rows;
    document.getElementById('csvError').textContent = bad ? `${bad} fila(s) descartadas por formato.` : '';
    document.getElementById('csvPreview').innerHTML =
      (rows.slice(0, 5).map(x => `<div class="acc-row"><span class="code">${esc(x.date)}</span><span>${esc(x.desc)}</span><span class="bal">${fmtNum(x.amount)}</span></div>`).join('') || '<p class="muted">Sin filas válidas.</p>') +
      (rows.length > 5 ? `<p class="muted small">…y ${rows.length - 5} más (${rows.length} en total).</p>` : '');
    document.getElementById('btnSaveCsv').disabled = !rows.length;
  };
  r.readAsText(f);
  ev.target.value = '';
});
document.getElementById('btnSaveCsv').addEventListener('click', () => {
  const bank = document.getElementById('csvBank').value;
  const inc = document.getElementById('csvIncome').value;
  const exp = document.getElementById('csvExpense').value;
  const err = document.getElementById('csvError');
  if (!bank || !csvRows.length) { err.textContent = 'Falta la cuenta del banco o no hay filas.'; return; }
  const seen = new Set(state.entries.map(e => {
    const t = entryTotal({ lines: e.lines });
    return `${e.date}|${e.desc}|${t.d.toFixed(2)}`;
  }));
  let n = 0, dup = 0, maxM = null;
  for (const x of csvRows) {
    if (seen.has(`${x.date}|${x.desc}|${Math.abs(x.amount).toFixed(2)}`)) { dup++; continue; }
    const a = Math.abs(x.amount);
    const lines = x.amount > 0
      ? (inc ? [{ accountId: bank, debit: a, credit: 0 }, { accountId: inc, debit: 0, credit: a }] : null)
      : (exp ? [{ accountId: exp, debit: a, credit: 0 }, { accountId: bank, debit: 0, credit: a }] : null);
    if (!lines) continue;
    state.entries.push({ id: uid(), n: state.seq++, date: x.date, desc: x.desc, lines });
    const m = NR.monthKey(x.date);
    if (/^\d{4}-\d{2}$/.test(m) && (!maxM || m > maxM)) maxM = m;
    n++;
  }
  csvModal.hidden = true;
  save();
  clearDiarioFilters(); if (maxM) diarioMonth = maxM; renderAll();
  alert(`Importados ${n} movimientos como asientos cuadrados${dup ? ` (${dup} ya existían).` : '.'}`);
  track('csv_importado', { n_filas: n });
});

// ---------- Recurrentes ----------
function renderRecurring() {
  const list = state.recurring || [];
  document.getElementById('recurringList').innerHTML = list.length ? list.map(r => {
    const t = entryTotal({ lines: r.lines });
    return `<div class="acc-row"><span>↻ día ${r.day}</span><span>${esc(r.desc)}</span>
      <span class="bal">${fmt(t.d)}</span>
      <span class="muted small">${r.lastRun ? 'generado ' + r.lastRun : 'pendiente'}</span>
      <button class="btn ghost small" data-rec-del="${r.id}">Quitar</button></div>`;
  }).join('') : '<p class="muted small">Sin plantillas. Usa “↻ Mensual” en cualquier asiento.</p>';
}
document.getElementById('recurringList').addEventListener('click', ev => {
  const del = ev.target.dataset.recDel;
  if (del) { state.recurring = state.recurring.filter(r => r.id !== del); save(); renderAll(); }
});
function makeMonthly(id) {
  const e = state.entries.find(x => x.id === id);
  if (!e || (state.recurring || []).some(r => r.desc === e.desc)) return;
  state.recurring.push({
    id: uid(), desc: e.desc,
    day: Math.min(28, parseInt(e.date.slice(8), 10) || 1),
    lines: e.lines.map(l => ({ accountId: l.accountId, debit: l.debit, credit: l.credit })),
    lastRun: null,
  });
  save(); renderAll();
}
document.getElementById('btnRunRecurring').addEventListener('click', () => {
  const now = new Date();
  const mk = todayISO().slice(0, 7);
  const lastDay = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
  let n = 0;
  for (const r of state.recurring || []) {
    if (r.lastRun === mk) continue;
    const day = String(Math.min(r.day, lastDay)).padStart(2, '0');
    state.entries.push({
      id: uid(), n: state.seq++, date: `${mk}-${day}`, desc: r.desc,
      lines: r.lines.map(l => ({ accountId: l.accountId, debit: l.debit, credit: l.credit })),
    });
    r.lastRun = mk; n++;
  }
  if (!n) { alert('Nada pendiente: las plantillas de este mes ya están generadas.'); return; }
  save();
  clearDiarioFilters(); diarioMonth = NR.currentMonth(); renderAll();
  alert(`Generados ${n} asiento(s) del mes.`);
  track('recurrentes_generados', { n });
});

// ---------- Diario ----------
function entryCard(e) {
  const { d } = entryTotal(e);
  const lines = e.lines.map(l => {
    const a = accById(l.accountId);
    return `<tr><td>${esc(a ? `${a.code} · ${a.name}` : 'Cuenta eliminada')}</td><td class="num">${l.debit ? fmtNum(l.debit) : ''}</td><td class="num">${l.credit ? fmtNum(l.credit) : ''}</td></tr>`;
  }).join('');
  return `<article class="entry">
    <div class="entry-head"><time>${esc(e.date)}</time><strong>${esc(e.desc)}</strong><span class="amount">${fmt(d)}</span></div>
    <table class="entry-lines">${lines}</table>
    <div class="entry-actions">
      <button class="btn ghost small" data-edit="${e.id}">Editar</button>
      <button class="btn ghost small" data-dupe="${e.id}">Duplicar</button>
      <button class="btn ghost small" data-monthly="${e.id}" title="Crear plantilla mensual">↻ Mensual</button>
      <button class="btn ghost small" data-del="${e.id}">Eliminar</button>
    </div></article>`;
}
function renderDiario() {
  const q = (document.getElementById('searchDiario').value || '').toLowerCase();
  const fromEl = document.getElementById('filterFrom'), toEl = document.getElementById('filterTo');
  const prevBtn = document.getElementById('diarioPrev'), nextBtn = document.getElementById('diarioNext');
  const labelEl = document.getElementById('diarioMonthLabel'), todayBtn = document.getElementById('diarioToday');
  const from = fromEl.value, to = toEl.value;
  // Con rango de fechas: modo propio sobre todo el histórico (puede cruzar
  // meses); la navegación por mes se oculta y el título muestra el rango.
  const rangeActive = !!(from || to);
  const badMonth = e => !/^\d{4}-\d{2}$/.test(NR.monthKey(e.date)); // sin fecha válida: siempre visible
  const base = rangeActive ? [...state.entries]
    : [...state.entries].filter(e => NR.monthKey(e.date) === diarioMonth || badMonth(e));
  let list = base.sort((a, b) => b.date.localeCompare(a.date) || b.id.localeCompare(a.id));
  if (from) list = list.filter(e => e.date >= from);
  if (to) list = list.filter(e => e.date <= to);
  if (q) list = list.filter(e => e.desc.toLowerCase().includes(q) ||
    e.lines.some(l => (accById(l.accountId)?.name || '').toLowerCase().includes(q)));
  document.getElementById('diarioList').innerHTML = list.length ? list.map(entryCard).join('')
    : (base.length
      ? '<p class="muted">Sin resultados. Prueba con otro filtro o crea un asiento nuevo.</p>'
      : (rangeActive
        ? '<p class="muted">Sin asientos en este rango.</p>'
        : '<p class="muted">Este mes no tiene asientos todavía.</p>'));
  if (rangeActive) {
    labelEl.textContent = NR.rangeLabel(from, to);
    prevBtn.hidden = nextBtn.hidden = true;
    todayBtn.hidden = false;
  } else {
    labelEl.textContent = NR.monthLabel(diarioMonth);
    prevBtn.hidden = nextBtn.hidden = false;
    const minM = NR.minMonth(state.entries);
    prevBtn.disabled = !minM || diarioMonth <= minM;
    nextBtn.disabled = diarioMonth >= NR.currentMonth();
    todayBtn.hidden = diarioMonth === NR.currentMonth();
  }
  renderRecurring();
}
let diarioMonth = NR.currentMonth();
function clearDiarioFilters() {
  document.getElementById('searchDiario').value = '';
  document.getElementById('filterFrom').value = '';
  document.getElementById('filterTo').value = '';
}
function setDiarioMonth(ym) {
  diarioMonth = ym;
  clearDiarioFilters();
  renderDiario();
}
document.getElementById('diarioPrev').addEventListener('click', () => setDiarioMonth(NR.addMonths(diarioMonth, -1)));
document.getElementById('diarioNext').addEventListener('click', () => setDiarioMonth(NR.addMonths(diarioMonth, 1)));
document.getElementById('diarioToday').addEventListener('click', () => setDiarioMonth(NR.currentMonth()));
function dupeEntry(id) {
  const e = state.entries.find(x => x.id === id);
  if (!e) return;
  openEntryModal({
    desc: e.desc + ' (copia)',
    lines: e.lines.map(l => ({ accountId: l.accountId, debit: l.debit, credit: l.credit })),
  });
}
document.getElementById('diarioList').addEventListener('click', ev => {
  const ed = ev.target.dataset.edit, del = ev.target.dataset.del, dupe = ev.target.dataset.dupe, mon = ev.target.dataset.monthly;
  if (ed) openEntryModal(null, ed);
  if (dupe) dupeEntry(dupe);
  if (mon) makeMonthly(mon);
  if (del && confirm('¿Eliminar este asiento?')) {
    state.entries = state.entries.filter(e => e.id !== del); save(); renderAll();
    track('asiento_eliminado');
  }
});
document.getElementById('recentList').addEventListener('click', ev => {
  const ed = ev.target.dataset.edit, dupe = ev.target.dataset.dupe, mon = ev.target.dataset.monthly;
  if (ed) { switchTab('diario'); openEntryModal(null, ed); }
  if (dupe) dupeEntry(dupe);
  if (mon) makeMonthly(mon);
});
['searchDiario', 'filterFrom', 'filterTo'].forEach(id => document.getElementById(id).addEventListener('input', renderDiario));
function switchTab(name) {
  document.querySelector(`.tab[data-view="${name}"]`).click();
}

// ---------- Mayor ----------
let mayorMonthly = false;
let mayorMonth = NR.currentMonth();
function renderMayor() {
  const sel = document.getElementById('mayorAccount');
  const prev = sel.value;
  sel.innerHTML = state.accounts.filter(a => !a.archived).map(a =>
    `<option value="${a.id}">${esc(a.code + ' · ' + a.name + ' (' + a.type + ')')}</option>`).join('');
  if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
  if (!sel.value && sel.options.length) sel.selectedIndex = 0;
  const acc = accById(sel.value) || state.accounts[0];
  if (!acc) { document.getElementById('mayorTable').querySelector('tbody').innerHTML = ''; return; }
  const q = (document.getElementById('mayorSearch').value || '').toLowerCase();
  const r = NR.mayorMovements(state.entries, acc.id, acc.type, mayorMonthly ? mayorMonth : null);
  const rows = [];
  if (mayorMonthly && r.inicial !== 0) {
    rows.push(`<tr><td>—</td><td colspan="2">Saldo inicial</td><td class="num"></td><td class="num"></td><td class="num"><strong>${fmt(r.inicial)}</strong></td></tr>`);
  }
  for (const m of r.movs) {
    if (q && !m.desc.toLowerCase().includes(q) && !m.date.includes(q)) continue;
    rows.push(`<tr><td>${esc(m.date)}</td><td>${esc(m.desc)}</td><td>${esc(acc.name)}</td>
      <td class="num">${m.d ? fmtNum(m.d) : ''}</td><td class="num">${m.h ? fmtNum(m.h) : ''}</td><td class="num"><strong>${fmt(m.run)}</strong></td></tr>`);
  }
  document.getElementById('mayorName').textContent = `${acc.code} · ${acc.name}`;
  document.getElementById('mayorBalance').textContent = fmt(r.final);
  document.getElementById('mayorNature').textContent = `${acc.type} · ${DEBIT_NATURE.has(acc.type) ? 'deudora' : 'acreedora'}`;
  document.getElementById('mayorTable').querySelector('tbody').innerHTML =
    rows.join('') || '<tr><td colspan="6" class="muted">Sin movimientos en esta cuenta.</td></tr>';
  document.getElementById('mayorMonthNav').hidden = !mayorMonthly;
  document.getElementById('mayorViewToggle').textContent = mayorMonthly ? 'Vista global' : 'Vista mensual';
  if (mayorMonthly) {
    document.getElementById('mayorMonthLabel').textContent = NR.monthLabel(mayorMonth);
    const minM = NR.minMonth(state.entries);
    document.getElementById('mayorPrev').disabled = !minM || mayorMonth <= minM;
    document.getElementById('mayorNext').disabled = mayorMonth >= NR.currentMonth();
    document.getElementById('mayorToday').hidden = mayorMonth === NR.currentMonth();
  }
}
document.getElementById('mayorViewToggle').addEventListener('click', () => {
  mayorMonthly = !mayorMonthly;
  if (mayorMonthly) mayorMonth = NR.currentMonth();
  renderMayor();
});
document.getElementById('mayorPrev').addEventListener('click', () => { mayorMonth = NR.addMonths(mayorMonth, -1); renderMayor(); });
document.getElementById('mayorNext').addEventListener('click', () => { mayorMonth = NR.addMonths(mayorMonth, 1); renderMayor(); });
document.getElementById('mayorToday').addEventListener('click', () => { mayorMonth = NR.currentMonth(); renderMayor(); });
document.getElementById('mayorAccount').addEventListener('change', renderMayor);
document.getElementById('mayorSearch').addEventListener('input', renderMayor);

// ---------- Cuentas ----------
function renderAccounts() {
  const t = totalsByAccount();
  document.getElementById('accountsList').innerHTML = TYPES.map(type => {
    const accs = state.accounts.filter(a => a.type === type && !a.archived);
    const rows = accs.map(a => {
      const used = state.entries.some(e => e.lines.some(l => l.accountId === a.id));
      return `<div class="acc-row"><span class="code">${esc(a.code)}</span><span>${esc(a.name)}</span>
        <span class="bal">${fmt(balanceOf(a, t))}</span>
        <button data-acc-edit="${a.id}" title="Editar">✏️</button>
        <button data-acc-del="${a.id}" title="${used ? 'Tiene movimientos: se archivará' : 'Eliminar'}" ${a._seed !== undefined && used ? '' : ''}>${used ? '📦' : '🗑️'}</button></div>`;
    }).join('') || '<p class="muted small">Sin cuentas.</p>';
    return `<div class="acc-group"><h3>${type}</h3>${rows}</div>`;
  }).join('');
}
document.getElementById('accountsList').addEventListener('click', ev => {
  const ed = ev.target.dataset.accEdit, del = ev.target.dataset.accDel;
  if (ed) openAccountModal(ed);
  if (del) {
    const used = state.entries.some(e => e.lines.some(l => l.accountId === del));
    if (used) { accById(del).archived = true; }
    else state.accounts = state.accounts.filter(a => a.id !== del);
    save(); renderAll();
  }
});

function renderBudgets() {
  const m = todayISO().slice(0, 7);
  const spent = {};
  for (const e of state.entries) {
    if (!e.date.startsWith(m)) continue;
    for (const l of e.lines) {
      const a = accById(l.accountId);
      if (a && a.type === 'Gasto') spent[a.id] = round2((spent[a.id] || 0) + (Number(l.debit) || 0) - (Number(l.credit) || 0));
    }
  }
  const gastos = state.accounts.filter(a => a.type === 'Gasto' && !a.archived);
  document.getElementById('budgetsBox').innerHTML = gastos.length ? gastos.map(a => {
    const s = spent[a.id] || 0;
    const b = Number(state.budgets[a.id]) || 0;
    const pct = b > 0 ? Math.min(100, Math.round(s / b * 100)) : 0;
    const cls = !b ? '' : (s > b ? 'over' : (pct >= 80 ? 'warn' : 'ok'));
    const msg = !b ? '<span class="muted small">sin límite</span>'
      : (s > b ? `⚠️ superado por ${fmt(s - b)}` : (pct >= 80 ? `⚠️ ${pct} % usado` : `${pct} % usado`));
    return `<div class="budget-row">
      <span class="budget-name">${esc(a.name)}<br /><span class="muted small">${fmt(s)}${b ? ' de ' + fmt(b) : ''} · ${msg}</span></span>
      <span class="budget-bar"><span class="fill ${cls}" style="width:${pct}%"></span></span>
      <input type="number" min="0" step="1" placeholder="Límite €" value="${b || ''}" data-budget="${a.id}" title="Límite mensual en €" />
    </div>`;
  }).join('') : '<p class="muted">No hay cuentas de gasto.</p>';
}
document.getElementById('budgetsBox').addEventListener('change', ev => {
  const id = ev.target.dataset.budget;
  if (!id) return;
  const v = round2(ev.target.value);
  if (v > 0) state.budgets[id] = v;
  else delete state.budgets[id];
  save(); renderBudgets(); renderDashboard();
});

// ---------- Informes ----------
const ASSET_COLORS = ['#38bdf8', '#22c55e', '#f59e0b', '#f472b6', '#a78bfa', '#2dd4bf', '#facc15', '#ef4444'];
const MES_S = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
function assetsSVG(data, H, totalName) {
  const W = 640, padL = 56, padR = 10, padT = 8, padB = 20;
  H = H || 140;
  totalName = totalName || 'Patrimonio total';
  const n = data.dates.length;
  const all = data.total.concat(data.series.flatMap(s => s.points));
  let lo = Math.min(...all), hi = Math.max(...all);
  if (lo === hi) { lo -= 1; hi += 1; }
  const X = i => n < 2 ? padL : padL + (i * (W - padL - padR)) / (n - 1);
  const Y = v => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);
  const f1 = x => Math.round(x * 10) / 10;
  let s = `<svg class="assets-chart" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" role="img">`;
  for (let g = 0; g <= 3; g++) {
    const v = lo + ((hi - lo) * g) / 3, y = Y(v);
    s += `<line x1="${padL}" y1="${f1(y)}" x2="${W - padR}" y2="${f1(y)}" stroke="var(--line)"/>`;
    s += `<text x="${padL - 6}" y="${f1(y + 3)}" text-anchor="end" font-size="9" fill="var(--muted)">${esc(fmtNum(v))}</text>`;
  }
  let lastM = '';
  data.dates.forEach((d, i) => {
    const m = d.slice(5, 7);
    if (m !== lastM) {
      lastM = m;
      s += `<text x="${f1(X(i))}" y="${H - 5}" text-anchor="middle" font-size="9" fill="var(--muted)">${MES_S[Number(m) - 1]}</text>`;
    }
  });
  const path = pts => pts.map((v, i) => `${i ? 'L' : 'M'}${f1(X(i))},${f1(Y(v))}`).join('');
  data.series.forEach((se, si) => {
    const c = ASSET_COLORS[si % ASSET_COLORS.length];
    s += `<path d="${path(se.points)}" fill="none" stroke="${c}" stroke-width="1.5"/>`;
  });
  s += `<path d="${path(data.total)}" fill="none" stroke="#e5e7eb" stroke-width="2" stroke-dasharray="6 3"/>`;
  data.series.forEach((se, si) => {
    const c = ASSET_COLORS[si % ASSET_COLORS.length];
    se.points.forEach((v, i) => {
      s += `<circle class="pt" cx="${f1(X(i))}" cy="${f1(Y(v))}" r="2" fill="${c}" data-s="${si}" data-i="${i}"><title>${esc(se.name)} · ${esc(NR.fmtDateES(data.dates[i]))} · ${esc(fmt(v))}</title></circle>`;
    });
  });
  data.total.forEach((v, i) => {
    s += `<circle class="pt" cx="${f1(X(i))}" cy="${f1(Y(v))}" r="2" fill="#e5e7eb" data-s="-1" data-i="${i}"><title>${esc(totalName)} · ${esc(NR.fmtDateES(data.dates[i]))} · ${esc(fmt(v))}</title></circle>`;
  });
  return s + '</svg>';
}
function monthEndBalance(monthKey, type) {
  type = type === 'Pasivo' ? 'Pasivo' : 'Activo';
  const accs = state.accounts.filter(a => !a.archived);
  const t = NR.totalsByAccount(accs, state.entries.filter(e => (e.date || '') <= NR.monthEnd(monthKey)));
  let tA = 0, tP = 0;
  for (const a of accs) {
    const b = NR.balanceOf(a, t);
    if (a.type === 'Activo') tA = round2(tA + b);
    if (a.type === 'Pasivo') tP = round2(tP + b);
  }
  const per = {};
  for (const a of accs) if (a.type === type) per[a.id] = NR.balanceOf(a, t);
  return { per, total: type === 'Pasivo' ? tP : round2(tA - tP) };
}
function renderEvolution(boxId, readId, type, totalName, emptyMsg, colName) {
  const box = document.getElementById(boxId);
  const accs = state.accounts.filter(a => !a.archived && a.type === type);
  if (!state.entries.length || !accs.length) {
    box.innerHTML = `<p class="muted">${emptyMsg}</p>`;
    return;
  }
  const data = NR.balanceSeries(state.accounts, state.entries, 180, undefined, type);
  const last = data.dates.length - 1;
  const chips = data.series.map((se, si) =>
    `<span class="legend-chip"><i style="background:${ASSET_COLORS[si % ASSET_COLORS.length]}"></i>${esc(se.name)} <strong>${fmt(se.points[last])}</strong></span>`).join('') +
    `<span class="legend-chip total"><i></i>${esc(totalName)} <strong>${fmt(data.total[last])}</strong></span>`;
  const months = [];
  const now = new Date();
  for (let k = 5; k >= 0; k--) {
    const d = new Date(now.getFullYear(), now.getMonth() - k, 1);
    months.push(d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0'));
  }
  const tabRows = months.map(mk => {
    const b = monthEndBalance(mk, type);
    return `<tr><td>${esc(NR.monthLabel(mk))}</td>` +
      data.series.map(se => `<td class="num">${fmtNum(b.per[se.accountId] || 0)}</td>`).join('') +
      `<td class="num"><strong>${fmtNum(b.total)}</strong></td></tr>`;
  }).join('');
  box.innerHTML = `<div class="legend">${chips}</div>` + assetsSVG(data, 140, totalName) +
    `<div id="${readId}" class="muted small assets-readout"></div>` +
    `<details class="small muted"><summary>Ver saldos a fin de mes</summary>` +
    `<table class="table"><thead><tr><th>Mes</th>` +
    data.series.map(se => `<th class="num">${esc(se.name)}</th>`).join('') +
    `<th class="num">${esc(colName)}</th></tr></thead><tbody>${tabRows}</tbody></table></details>`;
  const readout = document.getElementById(readId);
  const showLast = () => {
    readout.textContent = `${NR.fmtDateES(data.dates[last])} · ${totalName}: ${fmt(data.total[last])}`;
  };
  showLast();
  box.querySelectorAll('circle.pt').forEach(c => {
    const say = () => {
      const i = Number(c.dataset.i), si = Number(c.dataset.s);
      const name = si < 0 ? totalName : data.series[si].name;
      const v = si < 0 ? data.total[i] : data.series[si].points[i];
      readout.textContent = `${NR.fmtDateES(data.dates[i])} · ${name}: ${fmt(v)}`;
    };
    c.addEventListener('mouseenter', say);
    c.addEventListener('click', say);
  });
  box.querySelector('svg').addEventListener('mouseleave', showLast);
}
function renderAssets() {
  renderEvolution('assetsBox', 'assetsReadout', 'Activo', 'Patrimonio total', 'Sin datos de activos todavía.', 'Patrimonio');
}
function renderLiab() {
  renderEvolution('liabBox', 'liabReadout', 'Pasivo', 'Pasivos totales', 'Sin datos de pasivos todavía.', 'Total pasivos');
}
function pnlHTML(t) {
  const res = round2(t.Ingreso - t.Gasto);
  return `
    <table class="table"><tbody>
    <tr><td>Total ingresos</td><td class="num">${fmt(t.Ingreso)}</td></tr>
    <tr><td>Total gastos</td><td class="num">${fmt(t.Gasto)}</td></tr>
    <tr><td><strong>Resultado</strong></td><td class="num"><strong style="color:${res < 0 ? 'var(--red)' : 'var(--green)'}">${fmt(res)}</strong></td></tr>
    </tbody></table>
    <p class="muted small">${res >= 0 ? '🟢 Ganas más de lo que gastas. Sigue así.' : '🔴 Gastas más de lo que ingresas: revisa tus gastos.'}</p>`;
}
let pnlMonthly = false;
let pnlMonth = NR.currentMonth();
function setPnlMonth(ym) { pnlMonth = ym; renderReports(); }
function renderReports() {
  renderBudgets();
  renderAssets();
  renderLiab();
  const t = totalsByAccount();
  let td = 0, th = 0;
  document.getElementById('trialTable').querySelector('tbody').innerHTML = state.accounts
    .filter(a => !a.archived && ((t[a.id]?.debit || 0) || (t[a.id]?.credit || 0)))
    .map(a => {
      const d = t[a.id].debit, h = t[a.id].credit; td = round2(td + d); th = round2(th + h);
      return `<tr><td>${esc(a.code + ' · ' + a.name)}</td><td>${a.type}</td><td class="num">${fmtNum(d)}</td><td class="num">${fmtNum(h)}</td><td class="num"><strong>${fmt(balanceOf(a, t))}</strong></td></tr>`;
    }).join('') || '<tr><td colspan="5" class="muted">Sin movimientos todavía.</td></tr>';
  document.getElementById('trialDebe').textContent = fmtNum(td);
  document.getElementById('trialHaber').textContent = fmtNum(th);
  document.getElementById('trialSaldo').textContent = td === th ? '✓ ' + fmtNum(td) : '✗ descuadre';
  const mEntries = pnlMonthly ? NR.entriesOfMonth(state.entries, pnlMonth) : null;
  const tt = pnlMonthly ? NR.typeTotals(state.accounts, mEntries) : typeTotals();
  document.getElementById('pnlBox').innerHTML = (pnlMonthly && !mEntries.length)
    ? '<p class="muted">Sin movimientos este mes.</p>'
    : pnlHTML(tt);
  const res = round2(tt.Ingreso - tt.Gasto);
  document.getElementById('pnlMonthNav').hidden = !pnlMonthly;
  document.getElementById('pnlViewToggle').textContent = pnlMonthly ? 'Volver a la vista acumulada' : 'Vista mensual';
  if (pnlMonthly) {
    document.getElementById('pnlMonthLabel').textContent = NR.monthLabel(pnlMonth);
    const minM = NR.minMonth(state.entries);
    document.getElementById('pnlPrev').disabled = !minM || pnlMonth <= minM;
    document.getElementById('pnlNext').disabled = pnlMonth >= NR.currentMonth();
    document.getElementById('pnlToday').hidden = pnlMonth === NR.currentMonth();
  }
  const net = round2(tt.Activo - tt.Pasivo);
  document.getElementById('balanceBox').innerHTML = `
    <table class="table"><tbody>
    <tr><td>Activo (tienes)</td><td class="num">${fmt(tt.Activo)}</td></tr>
    <tr><td>Pasivo (debes)</td><td class="num">${fmt(tt.Pasivo)}</td></tr>
    <tr><td>Patrimonio base</td><td class="num">${fmt(tt.Patrimonio)}</td></tr>
    <tr><td>Resultado del ejercicio</td><td class="num">${fmt(res)}</td></tr>
    <tr><td><strong>Patrimonio neto</strong></td><td class="num"><strong>${fmt(net)}</strong></td></tr>
    </tbody></table>
    <p class="muted small">${round2(tt.Patrimonio + res) === net ? '✓ Activo − Pasivo = Patrimonio + Resultado.' : '✗ El balance no cuadra.'}</p>`;
}
document.getElementById('pnlViewToggle').addEventListener('click', () => {
  pnlMonthly = !pnlMonthly;
  if (pnlMonthly) pnlMonth = NR.currentMonth();
  renderReports();
});
document.getElementById('pnlPrev').addEventListener('click', () => setPnlMonth(NR.addMonths(pnlMonth, -1)));
document.getElementById('pnlNext').addEventListener('click', () => setPnlMonth(NR.addMonths(pnlMonth, 1)));
document.getElementById('pnlToday').addEventListener('click', () => setPnlMonth(NR.currentMonth()));

// ---------- Modal asiento ----------
const entryModal = document.getElementById('entryModal');
function accountOptions(selected) {
  return state.accounts.filter(a => !a.archived).map(a =>
    `<option value="${a.id}" ${a.id === selected ? 'selected' : ''}>${esc(a.code + ' · ' + a.name + ' (' + a.type + ')')}</option>`).join('');
}
function addLine(accountId, debit, credit) {
  const tr = document.createElement('tr');
  tr.innerHTML = `<td><select>${accountOptions(accountId)}</select></td>
    <td><input type="number" min="0" step="0.01" placeholder="0,00" value="${debit || ''}" /></td>
    <td><input type="number" min="0" step="0.01" placeholder="0,00" value="${credit || ''}" /></td>
    <td><button class="btn ghost small" title="Quitar línea">✕</button></td>`;
  tr.querySelector('button').addEventListener('click', () => { tr.remove(); updateTotals(); });
  tr.querySelectorAll('input,select').forEach(el => el.addEventListener('input', updateTotals));
  document.getElementById('entryLines').appendChild(tr);
  updateTotals();
}
function readLines() {
  return [...document.getElementById('entryLines').querySelectorAll('tr')].map(tr => ({
    accountId: tr.querySelector('select').value,
    debit: round2(tr.querySelectorAll('input')[0].value),
    credit: round2(tr.querySelectorAll('input')[1].value),
  }));
}
function updateTotals() {
  const lines = readLines();
  const d = round2(lines.reduce((s, l) => s + l.debit, 0));
  const h = round2(lines.reduce((s, l) => s + l.credit, 0));
  document.getElementById('totalDebe').textContent = fmt(d);
  document.getElementById('totalHaber').textContent = fmt(h);
  const err = document.getElementById('entryError');
  err.textContent = d === h && d > 0 ? '' : `Debe (${fmt(d)}) ≠ Haber (${fmt(h)}). Ajusta los importes para poder guardar.`;
}
function openEntryModal(preset, editId) {
  editingEntryId = editId || null;
  document.getElementById('entryTitle').textContent = editId ? 'Editar asiento' : 'Nuevo asiento';
  document.getElementById('entryLines').innerHTML = '';
  document.getElementById('entryError').textContent = '';
  const e = editId ? state.entries.find(x => x.id === editId) : null;
  document.getElementById('entryDate').value = e?.date || todayISO();
  document.getElementById('entryDesc').value = e?.desc || preset?.desc || '';
  if (e) e.lines.forEach(l => addLine(l.accountId, l.debit, l.credit));
  else if (preset) preset.lines.forEach(l => addLine(l.accountId, l.debit, l.credit));
  else { addLine(firstOf('Activo'), '', ''); addLine(firstOf('Gasto'), '', ''); }
  entryModal.hidden = false;
  document.getElementById('entryDesc').focus();
}
function firstOf(type) { return state.accounts.find(a => a.type === type && !a.archived)?.id || ''; }
function findAccByName(part) { return state.accounts.find(a => !a.archived && a.name.toLowerCase().includes(part))?.id || ''; }
document.getElementById('btnNewEntry').addEventListener('click', () => openEntryModal());
document.getElementById('btnNewEntry2').addEventListener('click', () => openEntryModal());
document.getElementById('btnCancelEntry').addEventListener('click', () => entryModal.hidden = true);
document.getElementById('btnAddLine').addEventListener('click', () => addLine(firstOf('Activo'), '', ''));
document.getElementById('btnQuickIncome').addEventListener('click', () => openEntryModal({
  desc: 'Nómina del mes',
  lines: [{ accountId: findAccByName('banco') || firstOf('Activo'), debit: 1500, credit: 0 },
          { accountId: findAccByName('sueldo') || firstOf('Ingreso'), debit: 0, credit: 1500 }],
}));
document.getElementById('btnQuickExpense').addEventListener('click', () => openEntryModal({
  desc: 'Compra supermercado',
  lines: [{ accountId: findAccByName('comida') || firstOf('Gasto'), debit: 60, credit: 0 },
          { accountId: findAccByName('banco') || firstOf('Activo'), debit: 0, credit: 60 }],
}));
document.getElementById('btnQuickTransfer').addEventListener('click', () => openEntryModal({
  desc: 'Transferencia a ahorros',
  lines: [{ accountId: findAccByName('ahorro') || firstOf('Activo'), debit: 200, credit: 0 },
          { accountId: findAccByName('principal') || firstOf('Activo'), debit: 0, credit: 200 }],
}));
document.getElementById('btnSaveEntry').addEventListener('click', () => {
  const date = document.getElementById('entryDate').value || todayISO();
  const desc = document.getElementById('entryDesc').value.trim();
  const lines = readLines().filter(l => l.accountId && (l.debit > 0 || l.credit > 0));
  const err = document.getElementById('entryError');
  if (!desc) { err.textContent = 'Pon una descripción al asiento.'; return; }
  if (lines.length < 2) { err.textContent = 'La partida doble exige al menos 2 líneas con importe.'; return; }
  if (lines.some(l => l.debit > 0 && l.credit > 0)) { err.textContent = 'Una misma línea no puede tener debe y haber a la vez.'; return; }
  const d = round2(lines.reduce((s, l) => s + l.debit, 0));
  const h = round2(lines.reduce((s, l) => s + l.credit, 0));
  if (d !== h || d <= 0) { err.textContent = `Asiento descuadrado: debe (${fmt(d)}) ≠ haber (${fmt(h)}).`; return; }
  if (editingEntryId) {
    const e = state.entries.find(x => x.id === editingEntryId);
    Object.assign(e, { date, desc, lines });
  } else {
    state.entries.push({ id: uid(), n: state.seq++, date, desc, lines });
  }
  save(); entryModal.hidden = true;
  clearDiarioFilters(); diarioMonth = NR.monthKey(date); renderAll();
  track('asiento_creado', { n_lineas: lines.length });
});

// ---------- Modal cuenta ----------
const accountModal = document.getElementById('accountModal');
function openAccountModal(editId) {
  editingAccountId = editId || null;
  const a = editId ? accById(editId) : null;
  document.getElementById('accountTitle').textContent = a ? 'Editar cuenta' : 'Nueva cuenta';
  document.getElementById('accName').value = a?.name || '';
  document.getElementById('accType').value = a?.type || 'Activo';
  document.getElementById('accCode').value = a?.code || '';
  document.getElementById('accError').textContent = '';
  accountModal.hidden = false;
  document.getElementById('accName').focus();
}
document.getElementById('btnNewAccount').addEventListener('click', () => openAccountModal());
document.getElementById('btnCancelAcc').addEventListener('click', () => accountModal.hidden = true);
document.getElementById('btnSaveAcc').addEventListener('click', () => {
  const name = document.getElementById('accName').value.trim();
  const type = document.getElementById('accType').value;
  const code = document.getElementById('accCode').value.trim() || String(600 + state.accounts.length);
  const err = document.getElementById('accError');
  if (!name) { err.textContent = 'La cuenta necesita un nombre.'; return; }
  if (editingAccountId) Object.assign(accById(editingAccountId), { name, type, code });
  else state.accounts.push({ id: uid(), name, type, code, archived: false });
  save(); accountModal.hidden = true; renderAll();
});
document.getElementById('btnReset').addEventListener('click', () => {
  if (!confirm('¿Restablecer el plan de cuentas base? No se borran tus asientos.')) return;
  const existing = new Set(state.accounts.map(a => a.code + '|' + a.name));
  BASE_ACCOUNTS.forEach((a, i) => { if (!existing.has(a.code + '|' + a.name)) state.accounts.push({ id: uid(), ...a, archived: false }); });
  save(); renderAll();
});

// ---------- Export / import / wipe ----------
document.getElementById('btnExport').addEventListener('click', () => {
  const blob = new Blob([JSON.stringify(state, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = `neverred-${todayISO()}.json`; a.click();
  URL.revokeObjectURL(a.href);
  track('export_json');
});
document.getElementById('btnImport').addEventListener('click', () => document.getElementById('fileImport').click());
document.getElementById('fileImport').addEventListener('change', ev => {
  const f = ev.target.files[0]; if (!f) return;
  const r = new FileReader();
  r.onload = async () => {
    const backup = JSON.stringify(state);
    try {
      const data = JSON.parse(r.result);
      if (!Array.isArray(data.accounts) || !Array.isArray(data.entries)) throw new Error('formato inválido');
      state.accounts = data.accounts;
      state.entries = data.entries || [];
      state.seq = data.seq || state.entries.length + 1;
      state.budgets = (data.budgets && typeof data.budgets === 'object') ? data.budgets : {};
      state.recurring = Array.isArray(data.recurring) ? data.recurring : [];
      if (data.currency) state.user.currency = data.currency;
      save(); renderAll();
      const ok = await syncToServer();
      if (ok === false) {
        state = JSON.parse(backup); save(); renderAll();
        alert('Archivo no válido: la base de datos lo ha rechazado. Se han restaurado tus datos.');
      } else {
        alert('Datos importados correctamente.');
        track('import_json', { n_asientos: (data.entries || []).length });
      }
    } catch { alert('Archivo no válido.'); }
  };
  r.readAsText(f); ev.target.value = '';
});
document.getElementById('btnCsvTrial').addEventListener('click', () => {
  const t = totalsByAccount();
  const rows = [['codigo', 'cuenta', 'tipo', 'debe', 'haber', 'saldo']];
  for (const a of state.accounts) rows.push([a.code, a.name, a.type, t[a.id]?.debit || 0, t[a.id]?.credit || 0, balanceOf(a, t)]);
  const csv = rows.map(r => r.map(x => `"${String(x).replaceAll('"', '""')}"`).join(';')).join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  a.download = `neverred-balance-${todayISO()}.csv`; a.click();
});
document.getElementById('btnWipe').addEventListener('click', () => {
  if (!confirm('¿Borrar TODOS los datos de NeverRed? Exporta antes si los quieres conservar.')) return;
  localStorage.removeItem(LS_KEY);
  state = { user: { name: '', currency: 'EUR' }, accounts: BASE_ACCOUNTS.map(a => ({ id: uid(), ...a, archived: false })), entries: [], seq: 1, budgets: {}, recurring: [] };
  save(); renderAll();
});

// ---------- Bienvenida / apertura ----------
const welcomeModal = document.getElementById('welcomeModal');
document.getElementById('btnSkipWelcome').addEventListener('click', () => {
  if (!state.user.name) state.user.name = 'contable';
  save(); welcomeModal.hidden = true; renderAll();
});
document.getElementById('btnStartWelcome').addEventListener('click', () => {
  state.user.name = document.getElementById('wName').value.trim() || 'contable';
  state.user.currency = document.getElementById('wCurrency').value;
  const cash = round2(document.getElementById('wCash').value);
  const bank = round2(document.getElementById('wBank').value);
  const debt = round2(document.getElementById('wDebt').value);
  const lines = [];
  if (cash > 0) lines.push({ accountId: findAccByName('efectivo') || firstOf('Activo'), debit: cash, credit: 0 });
  if (bank > 0) lines.push({ accountId: findAccByName('principal') || firstOf('Activo'), debit: bank, credit: 0 });
  const capital = round2(cash + bank - debt);
  if (debt > 0) lines.push({ accountId: findAccByName('tarjeta') || firstOf('Pasivo'), debit: 0, credit: debt });
  if (capital > 0) lines.push({ accountId: findAccByName('capital') || firstOf('Patrimonio'), debit: 0, credit: capital });
  if (lines.length >= 2) state.entries.push({ id: uid(), n: state.seq++, date: todayISO(), desc: 'Asiento de apertura', lines });
  save(); welcomeModal.hidden = true; renderAll();
});

// ---------- Autenticación (registro/login contra la BD) ----------
// Si la página se abrió como archivo local (file://), la API vive en el servidor local.
function apiBase() {
  if (location.protocol !== 'file:') return '';
  try { return localStorage.getItem('neverred_api') || 'http://127.0.0.1:8000'; }
  catch { return 'http://127.0.0.1:8000'; }
}
const API_BASE = apiBase();
const api = p => API_BASE + p;
const FROM_FILE = location.protocol === 'file:';
// Sesión por cookie HttpOnly; el token Bearer solo se usa en modo archivo local.
function authHeaders(extra) {
  const h = Object.assign({ 'Content-Type': 'application/json' }, extra);
  if (FROM_FILE && sessionToken) h['Authorization'] = 'Bearer ' + sessionToken;
  return h;
}
const authOverlay = document.getElementById('authOverlay');
const authNote = document.getElementById('authBackendNote');

function setAuthTab(which) {
  const reg = which === 'register';
  document.getElementById('tabRegister').classList.toggle('active', reg);
  document.getElementById('tabLogin').classList.toggle('active', !reg);
  document.getElementById('formRegister').hidden = !reg;
  document.getElementById('formLogin').hidden = reg;
}
document.getElementById('tabRegister').addEventListener('click', () => setAuthTab('register'));
document.getElementById('tabLogin').addEventListener('click', () => setAuthTab('login'));

// ---------- Usuarios conocidos (estilo login de macOS) ----------
const DEMO_EMAIL = 'demo@neverred.local';
const KNOWN_KEY = 'neverred_known_users';
function knownUsers() { try { return JSON.parse(localStorage.getItem(KNOWN_KEY) || '[]'); } catch { return []; } }
function rememberKnown(user) {
  try {
    const list = knownUsers().filter(u => u.email !== user.email);
    list.unshift({ id: user.id, name: user.name, email: user.email });
    localStorage.setItem(KNOWN_KEY, JSON.stringify(list.slice(0, 8)));
  } catch {}
}
function avatarSrc(name) {
  const ch = String(name || '?').trim().toLowerCase().normalize('NFC')[0] || '?';
  const file = /[a-zñ]/.test(ch) ? encodeURIComponent(ch) : 'default';
  return `assets/avatars/${file}.svg`;
}
function maskEmail(email) {
  const parts = String(email).split('@');
  if (parts.length !== 2) return email;
  return parts[0].slice(0, 3) + '***@' + parts[1];
}
let quickUser = null;
function demoRow() {
  return `
    <div class="acc-row">
      <img class="avatar" src="${avatarSrc('Demo')}" alt="Avatar demo" />
      <button class="btn ghost known-row" data-demo="1" type="button">
        <span class="who">Demo<small>${esc(maskEmail(DEMO_EMAIL))} · datos públicos de prueba, se restablecen solos</small></span>
      </button>
    </div>`;
}
function renderKnown() {
  const list = knownUsers().filter(u => u.email !== DEMO_EMAIL);
  const rows = list.map(u => `
    <div class="acc-row">
      <img class="avatar" src="${avatarSrc(u.name)}" alt="Avatar de ${esc(u.name)}" />
      <button class="btn ghost known-row" data-known="${esc(u.email)}" type="button">
        <span class="who">${esc(u.name)}<small>${esc(maskEmail(u.email))}</small></span>
      </button>
      <button class="btn ghost small known-forget" data-forget="${esc(u.email)}" type="button" title="Olvidar en este navegador">✕</button>
    </div>`).join('');
  document.getElementById('knownBox').hidden = !!quickUser;
  document.getElementById('knownList').innerHTML = rows + demoRow(); // Demo siempre el último
}
async function enterDemo() {
  authNote.textContent = 'Preparando datos de prueba…';
  try {
    const res = await fetch(api('/api/demo'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.token) {
      authNote.textContent = data.error || 'No se pudo preparar la demo.';
      return;
    }
    startSession(data.token, data.user);
  } catch {
    authNote.textContent = FROM_FILE
      ? 'No se pudo contactar con la API en http://127.0.0.1:8000.'
      : 'Si usas la app de escritorio, vuelve a abrir NeverRed; si la arrancas a mano: python3 backend/server.py';
  }
}
document.getElementById('knownList').addEventListener('click', ev => {
  const f = ev.target.closest('[data-forget]');
  if (f) {
    try { localStorage.setItem(KNOWN_KEY, JSON.stringify(knownUsers().filter(u => u.email !== f.dataset.forget))); } catch {}
    renderKnown();
    return;
  }
  if (ev.target.closest('[data-demo]')) { enterDemo(); return; }
  const k = ev.target.closest('[data-known]');
  if (k) {
    const u = knownUsers().find(x => x.email === k.dataset.known);
    if (u) openQuick(u);
  }
});
function openQuick(u) {
  quickUser = u;
  document.getElementById('knownBox').hidden = true;
  document.getElementById('authTabsWrap').hidden = true;
  document.getElementById('quickLogin').hidden = false;
  document.getElementById('quickAvatar').src = avatarSrc(u.name);
  document.getElementById('quickAvatar').alt = 'Avatar de ' + u.name;
  document.getElementById('quickName').textContent = u.name;
  document.getElementById('quickEmail').textContent = maskEmail(u.email);
  document.getElementById('quickPassword').value = '';
  document.getElementById('quickError').textContent = '';
  document.getElementById('quickPassword').focus();
}
function closeQuick() {
  quickUser = null;
  document.getElementById('quickLogin').hidden = true;
  document.getElementById('authTabsWrap').hidden = false;
  renderKnown();
}
document.getElementById('linkOther').addEventListener('click', ev => { ev.preventDefault(); closeQuick(); });
document.getElementById('formQuick').addEventListener('submit', ev => {
  ev.preventDefault();
  if (!quickUser) return;
  handleAuth('/api/login', {
    email: quickUser.email,
    password: document.getElementById('quickPassword').value,
    remember: document.getElementById('quickRemember').checked,
  }, document.getElementById('quickError'));
});

async function handleAuth(endpoint, payload, errEl) {
  errEl.textContent = '';
  let res;
  try {
    res = await fetch(api(endpoint), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch {
    errEl.textContent = FROM_FILE
      ? 'No se pudo contactar con la API en http://127.0.0.1:8000. Comprueba que el servidor está arrancado (python3 backend/server.py) o, mejor, abre http://127.0.0.1:8000 en el navegador.'
      : 'Si usas la app de escritorio, vuelve a abrir NeverRed; si la arrancas a mano: python3 backend/server.py';
    return;
  }
  let data = {};
  try { data = await res.json(); } catch {}
  if (!res.ok) { errEl.textContent = data.error || 'Error inesperado.'; return; }
  startSession(data.token, data.user);
}
document.getElementById('formRegister').addEventListener('submit', ev => {
  ev.preventDefault();
  handleAuth('/api/register', {
    name: document.getElementById('regName').value.trim(),
    email: document.getElementById('regEmail').value.trim(),
    password: document.getElementById('regPassword').value,
  }, document.getElementById('regError'));
});
document.getElementById('formLogin').addEventListener('submit', ev => {
  ev.preventDefault();
  handleAuth('/api/login', {
    email: document.getElementById('loginEmail').value.trim(),
    password: document.getElementById('loginPassword').value,
    remember: document.getElementById('loginRemember').checked,
  }, document.getElementById('loginError'));
});
document.getElementById('btnLogout').addEventListener('click', async () => {
  try {
    await fetch(api('/api/logout'), { method: 'POST', headers: authHeaders() });
  } catch {}
  endSession();
});

function startSession(token, user) {
  // En modo servidor manda la cookie HttpOnly (el token no se guarda en JS);
  // en modo archivo se conserva el Bearer en localStorage.
  sessionToken = FROM_FILE ? token : null;
  currentUser = user;
  rememberKnown(user);
  try {
    if (FROM_FILE) localStorage.setItem('neverred_session', token);
    else localStorage.removeItem('neverred_session');
  } catch {}
  clearDiarioFilters(); diarioMonth = NR.currentMonth(); pnlMonth = NR.currentMonth();
  pnlMonthly = false;
  mayorMonthly = false; mayorMonth = NR.currentMonth();
  loadUserData().then(enterApp);
}
/** Contabilidad vacía con el plan de cuentas base: cada usuario empieza de cero. */
function freshState(name) {
  return {
    user: { name: name || '', currency: 'EUR' },
    accounts: BASE_ACCOUNTS.map(a => ({ id: uid(), ...a, archived: false })),
    entries: [],
    seq: 1,
    budgets: {},
    recurring: [],
  };
}
function endSession() {
  // La contabilidad cacheada es tan sensible como la sesión: al salir no queda
  // rastro local (en modo archivo no hay sesión; ahí la caché local ES el dato).
  if (currentUser) { try { localStorage.removeItem(userKey()); } catch {} }
  telemetryQueue = []; // los eventos sin enviar de esta sesión se descartan
  sessionToken = null; currentUser = null;
  clearTimeout(saveTimer);
  state = freshState(''); // que el siguiente usuario no vea ni herede nada del anterior
  resetMarket(); // tampoco tickers ni gráficas de Mercado del anterior
  resetSimulators(); // ni resultados de simuladores (solo viven en el DOM)
  nukeServiceWorker(); // la próxima entrada cargará la última versión, nunca caché vieja
  try { localStorage.removeItem('neverred_session'); } catch {}
  quickUser = null;
  document.getElementById('quickLogin').hidden = true;
  document.getElementById('authTabsWrap').hidden = false;
  setAuthTab('login');
  renderKnown();
  authOverlay.hidden = false;
}
/** Descarga los datos del usuario desde la BD (o migra la copia local antigua). */
async function loadUserData() {
  let server = null;
  try {
    const res = await fetch(api('/api/data'), { headers: authHeaders() });
    if (res.status === 401) { endSession(); return; }
    serverEtag = res.headers.get('ETag');
    server = (await res.json()).data || {};
  } catch { server = null; }
  const hasServerData = server && ((server.accounts || []).length || (server.entries || []).length);
  if (hasServerData) {
    state.accounts = server.accounts;
    state.entries = server.entries || [];
    state.seq = server.seq || state.entries.length + 1;
    state.budgets = (server.budgets && typeof server.budgets === 'object') ? server.budgets : {};
    state.recurring = Array.isArray(server.recurring) ? server.recurring : [];
    state.user = { name: currentUser.name, currency: server.currency || 'EUR' };
  } else {
    // Usuario sin datos en la BD: NUNCA hereda la contabilidad de otro usuario.
    // 1) caché local propia (si este usuario ya usó este navegador),
    let cached = null;
    try { cached = JSON.parse(localStorage.getItem(userKey()) || 'null'); } catch { cached = null; }
    if (cached && ((cached.accounts || []).length || (cached.entries || []).length)) {
      state = cached;
    } else {
      // 2) migración única del formato anterior a usuarios (solo el primer usuario la reclama),
      // 3) o plan base vacío: el usuario empieza su propia contabilidad de cero.
      const legacy = load();
      if (legacy && ((legacy.accounts || []).length || (legacy.entries || []).length)) {
        state = legacy;
        try { localStorage.removeItem(LS_KEY); } catch {}
      } else {
        state = freshState(currentUser.name);
      }
    }
    state.user = { name: currentUser.name, currency: (state.user && state.user.currency) || 'EUR' };
    if (!state.budgets || typeof state.budgets !== 'object') state.budgets = {};
    if (!Array.isArray(state.recurring)) state.recurring = [];
    save();
    await syncToServer();
  }
  try { localStorage.setItem(userKey(), JSON.stringify(state)); } catch {}
}
function enterApp() {
  if (!currentUser) return;
  resetMarket(); // la pestaña recargará los tickers de ESTE usuario al abrirse
  resetSimulators(); // los simuladores no persisten: empiezan limpios
  authOverlay.hidden = true;
  document.getElementById('userEmail').textContent = currentUser.email;
  state.user.name = currentUser.name;
  document.getElementById('conflictBar').hidden = true;
  conflictData = null;
  setSync('ok');
  loadTelemetryConsent().then(() => {
    track('app_opened');
    if (currentUser && currentUser.email === DEMO_EMAIL) track('demo_entrada');
    // Primer arranque: una sola pregunta, sin bloquear (Esc = decidir luego).
    if (!telemetryAsked && !FROM_FILE) {
      document.getElementById('consentModal').hidden = false;
      document.getElementById('btnConsentYes').focus();
    }
    switchTab('inicio'); // al entrar siempre se muestra la vista general (+vista_inicio)
  });
  renderAll();
  if (!state.entries.length) {
    document.getElementById('wName').value = currentUser.name === 'contable' ? '' : currentUser.name;
    welcomeModal.hidden = false;
    document.getElementById('wName').focus();
  }
}

// ---------- Eliminar cuenta ----------
document.getElementById('btnDeleteAccount').addEventListener('click', async () => {
  if (!confirm('¿Eliminar tu cuenta y TODA tu contabilidad? Esta acción no se puede deshacer.')) return;
  if (!confirm('Última confirmación: se borrarán tu usuario y todos tus datos del servidor.')) return;
  const current = prompt('Escribe tu contraseña actual para confirmar el borrado:');
  if (current === null) return;
  try {
    const res = await fetch(api('/api/account'), {
      method: 'DELETE',
      headers: authHeaders(),
      body: JSON.stringify({ current }),
    });
    if (!res.ok) { alert('No se pudo eliminar: la contraseña no es correcta.'); return; }
  } catch { return; }
  try {
    localStorage.removeItem(userKey());
  } catch {}
  endSession();
  alert('Tu cuenta ha sido eliminada.');
});

// ---------- Recuperar contraseña ----------
const resetModal = document.getElementById('resetModal');
let resetToken = null;
try { resetToken = new URLSearchParams(location.search).get('reset'); } catch { resetToken = null; }
function openReset(stepNew) {
  document.getElementById('resetStepEmail').hidden = !!stepNew;
  document.getElementById('resetStepNew').hidden = !stepNew;
  document.getElementById('resetError').textContent = '';
  document.getElementById('resetError2').textContent = '';
  resetModal.hidden = false;
  (document.getElementById(stepNew ? 'resetNew' : 'resetEmail') || {}).focus?.();
}
document.getElementById('linkReset').addEventListener('click', ev => { ev.preventDefault(); openReset(false); });
document.getElementById('btnCancelReset').addEventListener('click', () => resetModal.hidden = true);
document.getElementById('btnSendReset').addEventListener('click', async () => {
  const err = document.getElementById('resetError');
  err.textContent = '';
  try {
    await fetch(api('/api/reset-request'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: document.getElementById('resetEmail').value.trim() }),
    });
    err.style.color = 'var(--green)';
    err.textContent = 'Si el correo está registrado, recibirás el enlace en unos minutos.';
  } catch { err.textContent = 'Sin conexión con el servidor.'; }
});
document.getElementById('btnConfirmReset').addEventListener('click', async () => {
  const err = document.getElementById('resetError2');
  err.textContent = '';
  try {
    const res = await fetch(api('/api/reset-confirm'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: resetToken, new: document.getElementById('resetNew').value }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { err.textContent = data.error || 'No se pudo completar.'; return; }
    resetModal.hidden = true;
    resetToken = null;
    try { history.replaceState(null, '', location.pathname); } catch {}
    setAuthTab('login');
    authNote.textContent = 'Contraseña actualizada. Inicia sesión con la nueva.';
  } catch { err.textContent = 'Sin conexión con el servidor.'; }
});

// ---------- Sesiones ----------
const sessionsModal = document.getElementById('sessionsModal');
async function loadSessions() {
  const box = document.getElementById('sessionsList');
  box.innerHTML = '<p class="muted small">Cargando…</p>';
  try {
    const res = await fetch(api('/api/sessions'), { headers: authHeaders() });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error();
    box.innerHTML = (data.sessions || []).map(s => {
      const until = new Date(s.expires_at * 1000).toLocaleDateString('es-ES');
      return `<div class="acc-row"><span class="code">…${esc(s.id.slice(-4))}</span>
        <span>${s.current ? 'Este dispositivo ✓' : 'Otro dispositivo'}</span>
        <span class="bal muted small">hasta ${until}</span></div>`;
    }).join('') || '<p class="muted">Sin sesiones.</p>';
  } catch { box.innerHTML = '<p class="error">No se pudieron cargar.</p>'; }
}
document.getElementById('btnSessions').addEventListener('click', () => { sessionsModal.hidden = false; loadSessions(); document.getElementById('btnCloseSessions').focus(); });
document.getElementById('btnCloseSessions').addEventListener('click', () => sessionsModal.hidden = true);
// ---------- Telemetría: opt-in con interruptor simple ----------
const telemetryModal = document.getElementById('telemetryModal');
const consentModal = document.getElementById('consentModal');
function updateTelemetryState() {
  document.getElementById('telemetryState').textContent =
    'Estado: ' + (telemetryOn ? 'activada.' : 'desactivada.');
}
document.getElementById('btnTelemetry').addEventListener('click', () => {
  document.getElementById('telemetryToggle').checked = telemetryOn;
  telemetryModal.hidden = false; updateTelemetryState();
  document.getElementById('btnCloseTelemetry').focus();
});
document.getElementById('btnCloseTelemetry').addEventListener('click', () => telemetryModal.hidden = true);
document.getElementById('btnForwardNow').addEventListener('click', async () => {
  const fwdP = document.getElementById('forwardState');
  fwdP.hidden = false; fwdP.textContent = 'Enviando…';
  try {
    const res = await fetch(api('/api/telemetry-forward'), { method: 'POST', headers: authHeaders() });
    const f = await res.json().catch(() => ({}));
    fwdP.textContent = !f.configured ? 'Sin receptor configurado.'
      : f.sent ? 'Enviado ✓ (o nada pendiente).'
      : 'No se pudo enviar; se reintentará solo.';
  } catch { fwdP.textContent = 'Fallo de envío; se reintentará solo.'; }
});
document.getElementById('telemetryToggle').addEventListener('change', async ev => {
  try {
    const res = await fetch(api('/api/telemetry-consent'), {
      method: 'PUT', headers: authHeaders(),
      body: JSON.stringify({ enabled: ev.target.checked }),
    });
    const data = await res.json().catch(() => ({}));
    telemetryOn = !!data.enabled;
    if (!telemetryOn) telemetryQueue = [];
  } catch { ev.target.checked = telemetryOn; }
  updateTelemetryState();
});
document.getElementById('btnRotateSessions').addEventListener('click', async () => {
  try {
    await fetch(api('/api/sessions/rotate'), { method: 'POST', headers: authHeaders() });
    loadSessions();
  } catch {}
});

// ---------- Cambio de contraseña ----------
const passwordModal = document.getElementById('passwordModal');
document.getElementById('btnPassword').addEventListener('click', () => {
  document.getElementById('pwCurrent').value = '';
  document.getElementById('pwNew').value = '';
  document.getElementById('pwError').textContent = '';
  passwordModal.hidden = false;
  document.getElementById('pwCurrent').focus();
});
document.getElementById('btnCancelPw').addEventListener('click', () => passwordModal.hidden = true);
document.getElementById('btnSavePw').addEventListener('click', async () => {
  const err = document.getElementById('pwError');
  err.textContent = '';
  try {
    const res = await fetch(api('/api/password'), {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({
        current: document.getElementById('pwCurrent').value,
        new: document.getElementById('pwNew').value,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { err.textContent = data.error || 'No se pudo cambiar la contraseña.'; return; }
    passwordModal.hidden = true;
    alert('Contraseña cambiada. Las demás sesiones se han cerrado.');
  } catch { err.textContent = 'Sin conexión con el servidor.'; }
});

// ---------- Herramientas / Mercado ----------
const TREND_COLOR = { up: '#22c55e', down: '#ef4444', flat: '#e5e7eb' };
const TREND_ARROW = { up: '▲', down: '▼', flat: '●' };
const TREND_WORD = { up: 'al alza', down: 'a la baja', flat: 'plana' };
let marketTickers = [];
let marketConfigured = false;
let marketLoaded = false;
let marketDrawn = {}; // símbolo → gráfica ya pintada (no repintar en cada render)
let marketSeries = {}; // símbolo → última serie recibida (repintado sin red)
function resetMarket() {
  // Los tickers son por usuario: al cambiar de sesión no puede quedar
  // rastro del anterior (ni lista, ni gráficas, ni flag de cargado).
  marketTickers = [];
  marketConfigured = false;
  marketLoaded = false;
  marketDrawn = {};
  marketSeries = {};
}
function marketSVG(dates, closes, color) {
  const W = 640, H = 140, padL = 56, padR = 10, padT = 8, padB = 20;
  const n = dates.length;
  let lo = Math.min(...closes), hi = Math.max(...closes);
  if (lo === hi) { lo -= 1; hi += 1; }
  const X = i => n < 2 ? padL : padL + (i * (W - padL - padR)) / (n - 1);
  const Y = v => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);
  const f1 = x => Math.round(x * 10) / 10;
  let s = `<svg class="assets-chart" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" role="img">`;
  for (let g = 0; g <= 3; g++) {
    const v = lo + ((hi - lo) * g) / 3, y = Y(v);
    s += `<line x1="${padL}" y1="${f1(y)}" x2="${W - padR}" y2="${f1(y)}" stroke="var(--line)"/>`;
    s += `<text x="${padL - 6}" y="${f1(y + 3)}" text-anchor="end" font-size="9" fill="var(--muted)">${esc(fmtNum(v))}</text>`;
  }
  let lastM = '';
  dates.forEach((d, i) => {
    const m = d.slice(5, 7);
    if (m !== lastM) {
      lastM = m;
      s += `<text x="${f1(X(i))}" y="${H - 5}" text-anchor="middle" font-size="9" fill="var(--muted)">${MES_S[Number(m) - 1]}</text>`;
    }
  });
  s += `<path d="${closes.map((v, i) => `${i ? 'L' : 'M'}${f1(X(i))},${f1(Y(v))}`).join('')}" fill="none" stroke="${color}" stroke-width="1.5"/>`;
  closes.forEach((v, i) => {
    s += `<circle class="pt" cx="${f1(X(i))}" cy="${f1(Y(v))}" r="2" fill="${color}" data-i="${i}"><title>${esc(NR.fmtDateES(dates[i]))} · ${esc(fmtNum(v))}</title></circle>`;
  });
  return s + '</svg>';
}
function marketWhen(ts) {
  if (!ts) return 'sin datos';
  try {
    const s = new Date(ts * 1000).toLocaleString('es-ES', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    return 'actualizado: ' + s;
  } catch { return ''; }
}
async function marketFetch(path, opts) {
  const res = await fetch(api(path), { headers: authHeaders(), ...(opts || {}) });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) { endSession(); throw new Error('sesión'); }
  if (!res.ok) throw new Error(data.error || 'Error de red.');
  return data;
}
async function loadMarket() {
  if (!currentUser) return;
  try {
    const st = await marketFetch('/api/market/status');
    marketConfigured = !!st.configured;
    paintQuota(st.quota);
    const lt = await marketFetch('/api/market/tickers');
    marketTickers = lt.tickers || [];
    marketLoaded = true;
  } catch { marketTickers = []; }
  renderMarket();
}
function paintQuota(quota) {
  const el = document.getElementById('marketQuota');
  if (!el) return;
  if (!quota) { el.textContent = ''; return; }
  const left = Math.max(0, quota.limit - quota.used);
  el.textContent = `Cuota Alpha Vantage: ${left} de ${quota.limit} peticiones disponibles hoy (conteo aproximado de esta instalación; se renueva a diario).`;
}
async function updateQuota() {
  try {
    const st = await marketFetch('/api/market/status');
    paintQuota(st.quota);
  } catch { /* sin red: no molestar */ }
}
function renderMarket() {
  const setBox = document.getElementById('marketKeySet');
  const formBox = document.getElementById('marketKeyForm');
  if (!setBox) return; // vista aún no montada
  setBox.hidden = !marketConfigured;
  formBox.hidden = marketConfigured;
  const list = document.getElementById('marketList');
  const cards = marketTickers.filter(t => t.last != null);
  const pending = marketTickers.filter(t => t.last == null);
  list.innerHTML = (!marketLoaded ? '<p class="muted">Cargando…</p>'
    : (!marketTickers.length ? '<p class="muted">Sin valores. Añade tu primer ticker arriba (p. ej. AAPL).</p>' : '')) +
    pending.map(t => `<div class="card ticker-card"><div class="ticker-head"><strong>${esc(t.symbol)}</strong><span class="muted small">cargando serie…</span></div><div class="ticker-chart" data-chart="${esc(t.symbol)}"></div></div>`).join('') +
    cards.map(t => {
      const c = TREND_COLOR[t.trend] || TREND_COLOR.flat;
      const pct = (t.change_pct >= 0 ? '+' : '') + fmtNum(t.change_pct) + ' %';
      return `<div class="card ticker-card">
        <div class="ticker-head"><strong><a href="https://es.finance.yahoo.com/quote/${encodeURIComponent(t.symbol)}/" target="_blank" rel="noopener" title="Ver ${esc(t.symbol)} en Yahoo Finanzas">${esc(t.symbol)} ↗</a></strong>
          <span style="color:${c}">${TREND_ARROW[t.trend] || ''} ${pct} (${TREND_WORD[t.trend] || ''})</span>
          <span class="muted small">cierre: ${esc(fmtNum(t.last))} · ${esc(marketWhen(t.cached_at))}${t.stale ? ' · desactualizado' : ''}</span>
          <span class="spacer"></span>
          <button class="btn ghost small" data-refresh="${esc(t.symbol)}" type="button">Actualizar</button>
          <button class="btn ghost small" data-del="${esc(t.symbol)}" type="button">Quitar</button>
        </div>
        <div class="ticker-chart" data-chart="${esc(t.symbol)}"></div>
        <div class="muted small ticker-readout" data-readout="${esc(t.symbol)}"></div>
        <details class="small muted"><summary>Máximos y mínimos del periodo</summary>
          <table class="table"><tbody>
            <tr><td>Precio máximo</td><td class="num">${esc(fmtNum(t.max))}</td></tr>
            <tr><td>Precio mínimo</td><td class="num">${esc(fmtNum(t.min))}</td></tr>
            <tr><td>Variación del periodo</td><td class="num" style="color:${c}"><strong>${pct}</strong></td></tr>
            <tr><td>Puntos de cotización</td><td class="num">${t.points}</td></tr>
          </tbody></table>
        </details>
      </div>`;
    }).join('');
  // Las series viajan aparte y solo con la vista visible: abrir Herramientas
  // no gasta cuota si todo está en caché, y editar asientos no repinta.
  // Las ya descargadas se repintan desde memoria (sin red).
  const toolsVisible = document.getElementById('view-herramientas').classList.contains('active');
  marketTickers.forEach(t => {
    const h = marketSeries[t.symbol];
    if (h && marketDrawn[t.symbol]) paintTickerChart(t.symbol, h);
    else if (toolsVisible && !marketDrawn[t.symbol]) loadTickerChart(t.symbol);
  });
}
function paintTickerChart(symbol, h) {
  const slot = document.querySelector(`[data-chart="${symbol}"]`);
  if (!slot || !h) return;
  const c = TREND_COLOR[h.trend] || TREND_COLOR.flat;
  slot.innerHTML = marketSVG(h.dates, h.closes, c);
  const readout = document.querySelector(`[data-readout="${symbol}"]`);
  const last = h.dates.length - 1;
  const showLast = () => { if (readout) readout.textContent = `${NR.fmtDateES(h.dates[last])} · cierre: ${fmtNum(h.closes[last])}`; };
  showLast();
  slot.querySelectorAll('circle.pt').forEach(pt => {
    const say = () => { if (readout) readout.textContent = `${NR.fmtDateES(h.dates[Number(pt.dataset.i)])} · cierre: ${fmtNum(h.closes[Number(pt.dataset.i)])}`; };
    pt.addEventListener('mouseenter', say);
    pt.addEventListener('click', say);
  });
  const svg = slot.querySelector('svg');
  if (svg) svg.addEventListener('mouseleave', showLast);
}
async function loadTickerChart(symbol, force) {
  const slot = document.querySelector(`[data-chart="${symbol}"]`);
  if (!slot) return;
  try {
    const h = await marketFetch(`/api/market/history?symbol=${encodeURIComponent(symbol)}${force ? '&refresh=1' : ''}`);
    marketDrawn[symbol] = true;
    marketSeries[symbol] = h;
    paintTickerChart(symbol, h);
    // Refresca la cabecera con los stats recién llegados (p. ej. ticker nuevo).
    const i = marketTickers.findIndex(t => t.symbol === symbol);
    if (i >= 0 && marketTickers[i].last == null) {
      marketTickers[i] = { ...marketTickers[i], last: h.last, change_pct: h.change_pct, trend: h.trend, min: h.min, max: h.max, points: h.points, cached_at: h.cached_at, stale: h.stale };
      renderMarket();
    }
  } catch (e) {
    if (String(e.message) !== 'sesión') slot.innerHTML = `<p class="muted small">Sin serie: ${esc(e.message)}</p>`;
  }
}
document.querySelectorAll('.subtab').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('.subtab').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  document.getElementById('sub-mercado').hidden = b.dataset.sub !== 'mercado';
  document.getElementById('sub-prestamos').hidden = b.dataset.sub !== 'prestamos';
  document.getElementById('sub-rendimientos').hidden = b.dataset.sub !== 'rendimientos';
}));
document.querySelector('.tab[data-view="herramientas"]').addEventListener('click', () => {
  if (!marketLoaded) loadMarket();
});
document.getElementById('btnMarketKeySave').addEventListener('click', async () => {
  const err = document.getElementById('marketKeyError');
  err.textContent = '';
  const key = document.getElementById('marketKeyInput').value.trim();
  if (!key) { err.textContent = 'Pega tu clave primero.'; return; }
  try {
    await marketFetch('/api/market/key', { method: 'POST', body: JSON.stringify({ key }) });
    document.getElementById('marketKeyInput').value = '';
    marketConfigured = true;
    renderMarket();
  } catch (e) { err.textContent = e.message; }
});
document.getElementById('btnMarketKeyDel').addEventListener('click', async () => {
  if (!confirm('¿Quitar la clave de Alpha Vantage de este servidor? Los tickers se conservan.')) return;
  try {
    await fetch(api('/api/market/key'), { method: 'DELETE', headers: authHeaders() });
    marketConfigured = false;
    renderMarket();
  } catch {}
});
document.getElementById('btnMarketAdd').addEventListener('click', async () => {
  const err = document.getElementById('marketError');
  err.textContent = '';
  const inp = document.getElementById('marketSymbol');
  const symbol = inp.value.trim().toUpperCase();
  if (!symbol) { err.textContent = 'Escribe un ticker (p. ej. AAPL).'; return; }
  try {
    const data = await marketFetch('/api/market/tickers', { method: 'POST', body: JSON.stringify({ symbol }) });
    inp.value = '';
    marketLoaded = true;
    const h = data.history;
    if (!marketTickers.some(t => t.symbol === h.symbol)) {
      marketTickers.push({ symbol: h.symbol, added_at: Date.now() / 1000, last: h.last, change_pct: h.change_pct, trend: h.trend, min: h.min, max: h.max, points: h.points, cached_at: h.cached_at, stale: h.stale });
    }
    updateQuota();
    renderMarket();
  } catch (e) { err.textContent = e.message; }
});
document.getElementById('marketList').addEventListener('click', async ev => {
  const del = ev.target.dataset.del;
  const ref = ev.target.dataset.refresh;
  if (del) {
    marketTickers = marketTickers.filter(t => t.symbol !== del);
    delete marketDrawn[del];
    delete marketSeries[del];
    renderMarket();
    try { await fetch(api('/api/market/tickers?symbol=' + encodeURIComponent(del)), { method: 'DELETE', headers: authHeaders() }); }
    catch {}
  } else if (ref) {
    const slot = document.querySelector(`[data-chart="${ref}"]`);
    if (slot) slot.innerHTML = '<p class="muted small">Actualizando…</p>';
    delete marketDrawn[ref];
    await loadTickerChart(ref, true);
    updateQuota();
    try {
      const lt = await marketFetch('/api/market/tickers');
      marketTickers = lt.tickers || [];
      renderMarket();
    } catch {}
  }
});
function renderTools() { renderMarket(); }

// ---------- Herramientas / Simuladores (cálculo local, sin persistencia) ----------
// Nada se guarda (ni BD, ni localStorage, ni red): al cambiar de usuario basta
// con devolver los formularios a sus valores iniciales y vaciar resultados.
const SIM_DEFAULTS = { loanAmount: '120000', loanRate: '3', loanYears: '25', yldInitial: '10000', yldMonthly: '200', yldRate: '5', yldYears: '10' };
function resetSimulators() {
  for (const [id, v] of Object.entries(SIM_DEFAULTS)) {
    const el = document.getElementById(id);
    if (el) el.value = v;
  }
  for (const id of ['loanResults', 'yieldResults']) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = '';
  }
  for (const id of ['loanError', 'yieldError']) {
    const el = document.getElementById(id);
    if (el) el.textContent = '';
  }
  for (const id of ['loanChart', 'yieldChart', 'loanDetails', 'yieldDetails']) {
    const el = document.getElementById(id);
    if (el) el.hidden = true;
  }
}
function drawStackedBars(canvasId, rows, cA, cB, legendA, legendB) {
  const cv = document.getElementById(canvasId);
  if (!cv) return;
  const ctx = cv.getContext('2d');
  const W = cv.width, H = cv.height;
  ctx.clearRect(0, 0, W, H);
  const max = Math.max(1, ...rows.map(r => r.a + r.b));
  const n = rows.length, slot = W / n, bw = Math.min(26, slot * 0.55);
  const base = H - 20, step = Math.max(1, Math.ceil(n / 12));
  rows.forEach((r, i) => {
    const x = i * slot + (slot - bw) / 2;
    const ha = (r.a / max) * (H - 60), hb = (r.b / max) * (H - 60);
    ctx.fillStyle = cB; ctx.fillRect(x, base - ha - hb, bw, Math.max(0, hb));
    ctx.fillStyle = cA; ctx.fillRect(x, base - ha, bw, Math.max(0, ha));
    if (i % step === 0) {
      ctx.fillStyle = '#9aa5b4'; ctx.font = '11px system-ui'; ctx.textAlign = 'center';
      ctx.fillText('año ' + r.year, x + bw / 2, H - 6);
    }
  });
  ctx.font = '11px system-ui'; ctx.textAlign = 'left';
  ctx.fillStyle = cA; ctx.fillRect(8, 8, 10, 10);
  ctx.fillStyle = '#9aa5b4'; ctx.fillText(legendA, 22, 17);
  const off = 30 + ctx.measureText(legendA).width;
  ctx.fillStyle = cB; ctx.fillRect(off, 8, 10, 10);
  ctx.fillStyle = '#9aa5b4'; ctx.fillText(legendB, off + 14, 17);
}
document.getElementById('btnLoanCalc').addEventListener('click', () => {
  const err = document.getElementById('loanError');
  err.textContent = '';
  const q = NR.loanQuote(document.getElementById('loanAmount').value,
    document.getElementById('loanRate').value, document.getElementById('loanYears').value);
  const box = document.getElementById('loanResults');
  const show = ok => {
    document.getElementById('loanChart').hidden = !ok;
    document.getElementById('loanDetails').hidden = !ok;
  };
  if (!q) {
    err.textContent = 'Revisa los datos: importe > 0, TIN 0–100 %, plazo entero 1–50 años.';
    box.innerHTML = ''; show(false); return;
  }
  box.innerHTML = `<table class="table"><tbody>
    <tr><td>Cuota mensual</td><td class="num"><strong>${fmt(q.monthly)}</strong></td></tr>
    <tr><td>Total pagado</td><td class="num">${fmt(q.total)}</td></tr>
    <tr><td>Intereses totales</td><td class="num" style="color:var(--red)">${fmt(q.interest)}</td></tr>
    </tbody></table>`;
  drawStackedBars('loanChart', q.schedule.map(s => ({ year: s.year, a: s.capital, b: s.interest })),
    '#22c55e', '#ef4444', 'Capital', 'Intereses');
  document.getElementById('loanTable').innerHTML = `<table class="table"><thead><tr><th>Año</th><th class="num">Capital</th><th class="num">Intereses</th><th class="num">Cuota anual</th></tr></thead><tbody>` +
    q.schedule.map(s => `<tr><td>${s.year}</td><td class="num">${fmtNum(s.capital)}</td><td class="num">${fmtNum(s.interest)}</td><td class="num">${fmtNum(round2(q.monthly * 12))}</td></tr>`).join('') +
    `</tbody></table>`;
  show(true);
});
document.getElementById('btnYieldCalc').addEventListener('click', () => {
  const err = document.getElementById('yieldError');
  err.textContent = '';
  const g = NR.yieldGrowth(document.getElementById('yldInitial').value,
    document.getElementById('yldMonthly').value, document.getElementById('yldRate').value,
    document.getElementById('yldYears').value);
  const box = document.getElementById('yieldResults');
  const show = ok => {
    document.getElementById('yieldChart').hidden = !ok;
    document.getElementById('yieldDetails').hidden = !ok;
  };
  if (!g) {
    err.textContent = 'Revisa los datos: importes ≥ 0, rentabilidad 0–100 %, plazo entero 1–50 años.';
    box.innerHTML = ''; show(false); return;
  }
  box.innerHTML = `<table class="table"><tbody>
    <tr><td>Total aportado</td><td class="num">${fmt(g.invested)}</td></tr>
    <tr><td>Valor final</td><td class="num"><strong>${fmt(g.final)}</strong></td></tr>
    <tr><td>Ganancia</td><td class="num" style="color:${g.gain < 0 ? 'var(--red)' : 'var(--green)'}"><strong>${fmt(g.gain)}</strong></td></tr>
    </tbody></table>`;
  drawStackedBars('yieldChart', g.yearly.map(y => ({ year: y.year, a: y.invested, b: round2(y.value - y.invested) })),
    '#38bdf8', '#22c55e', 'Aportado', 'Ganancia');
  document.getElementById('yieldTable').innerHTML = `<table class="table"><thead><tr><th>Año</th><th class="num">Aportado</th><th class="num">Valor</th><th class="num">Ganancia</th></tr></thead><tbody>` +
    g.yearly.map(y => `<tr><td>${y.year}</td><td class="num">${fmtNum(y.invested)}</td><td class="num">${fmtNum(y.value)}</td><td class="num">${fmtNum(round2(y.value - y.invested))}</td></tr>`).join('') +
    `</tbody></table>`;
  show(true);
});

// ---------- Init ----------
function renderAll() { renderDashboard(); renderDiario(); renderMayor(); renderAccounts(); renderReports(); renderTools(); }
document.addEventListener('keydown', e => { if (e.key === 'Escape') { entryModal.hidden = true; accountModal.hidden = true; passwordModal.hidden = true; resetModal.hidden = true; csvModal.hidden = true; sessionsModal.hidden = true; telemetryModal.hidden = true; consentModal.hidden = true; } });
// Trampa de foco: el Tab no sale del modal abierto (accesibilidad)
document.addEventListener('keydown', e => {
  if (e.key !== 'Tab') return;
  const open = [...document.querySelectorAll('.modal-backdrop')].find(m => !m.hidden);
  if (!open) return;
  const f = [...open.querySelectorAll('button, input, select, a[href]')]
    .filter(el => !el.disabled && el.offsetParent !== null);
  if (!f.length) return;
  const first = f[0], last = f[f.length - 1];
  if (e.shiftKey && document.activeElement === first) { last.focus(); e.preventDefault(); }
  else if (!e.shiftKey && document.activeElement === last) { first.focus(); e.preventDefault(); }
});

async function pingBackend() {
  try { await fetch(api('/api/me')); return true; } // cualquier respuesta = servidor vivo
  catch { return false; }
}
// Guardián anti-carcasa-obsoleta: si el frontal cargado no coincide con el
// servidor (p. ej. SW atascado o paquete a medias), limpia SW+cachés y recarga.
async function ensureFreshShell() {
  try {
    if ('serviceWorker' in navigator && !FROM_FILE) {
      const reg = await navigator.serviceWorker.getRegistration();
      if (reg) reg.update();
    }
    const res = await fetch(api('/api/health'));
    const h = await res.json().catch(() => ({}));
    if (!h.version) return;
    if (h.version === NEVERRED_BUILD) {
      try { sessionStorage.removeItem('nr_fresh'); } catch {}
      return;
    }
    let tried = false;
    try { tried = sessionStorage.getItem('nr_fresh') === '1'; } catch {}
    if (!tried) {
      try { sessionStorage.setItem('nr_fresh', '1'); } catch {}
      if ('serviceWorker' in navigator) {
        const regs = await navigator.serviceWorker.getRegistrations();
        await Promise.all(regs.map(r => r.unregister()));
      }
      if ('caches' in window) {
        const keys = await caches.keys();
        await Promise.all(keys.map(k => caches.delete(k)));
      }
      location.reload();
      await new Promise(() => {}); // no seguir con la carcasa vieja
    }
  } catch {}
}
async function boot() {
  authOverlay.hidden = false;
  setAuthTab('login');
  renderKnown();
  try {
    const r = await fetch(api('/api/health'));
    const h = await r.json().catch(() => ({}));
    if (h.version) document.getElementById('appVersion').textContent = 'v' + h.version;
  } catch {}
  document.getElementById('btnRetryBackend').hidden = true;
  const fileWarn = document.getElementById('authFileWarn');
  fileWarn.hidden = !FROM_FILE;
  if (FROM_FILE) {
    authNote.innerHTML = 'Has abierto <code>index.html</code> como archivo. Lo recomendable es usar ' +
      '<a href="http://127.0.0.1:8000">http://127.0.0.1:8000</a> con el servidor arrancado. Intento conectar de todos modos…';
  } else {
    authNote.textContent = 'Conectando con la base de datos…';
  }
  if (!await pingBackend()) {
    authNote.textContent = FROM_FILE
      ? 'Sin conexión con http://127.0.0.1:8000. Arranca el servidor con: python3 backend/server.py (o abre esa URL si ya lo hiciste).'
      : 'Sin conexión con el servidor. Arráncalo con: python3 backend/server.py y pulsa Reintentar.';
    document.getElementById('btnRetryBackend').hidden = false;
    endSessionKeepOverlay();
    return;
  }
  await ensureFreshShell();
  if (resetToken) {
    // El token ya está en memoria: sácalo de la URL para que no quede en el
    // historial del navegador.
    try { history.replaceState(null, '', location.pathname); } catch {}
    openReset(true); return; // viene del enlace del correo
  }
  if (!sessionToken) {
    authNote.textContent = FROM_FILE
      ? 'Conectado con la base de datos ✓ Regístrate (o mejor: abre http://127.0.0.1:8000).'
      : 'Regístrate con tu correo y una contraseña para empezar.';
    return;
  }
  authNote.textContent = 'Recuperando tu sesión…';
  try {
    const res = await fetch(api('/api/me'), { headers: authHeaders() });
    if (!res.ok) throw new Error('sin sesión');
    const { user } = await res.json();
    currentUser = user;
    if (!FROM_FILE) { // la cookie manda: olvida tokens Bearer antiguos
      sessionToken = null;
      try { localStorage.removeItem('neverred_session'); } catch {}
    }
    await loadUserData();
    enterApp();
  } catch {
    endSession();
    authNote.textContent = 'Regístrate con tu correo y una contraseña para empezar.';
  }
}
/** Muestra el acceso sin borrar una posible sesión guardada (fallo de red, no de credenciales). */
function endSessionKeepOverlay() { authOverlay.hidden = false; }
document.getElementById('btnRetryBackend').addEventListener('click', boot);
boot();

// PWA: carcasa offline (solo en modo servidor; la API siempre necesita red)
if ('serviceWorker' in navigator && !FROM_FILE) {
  const hadController = !!navigator.serviceWorker.controller;
  navigator.serviceWorker.register('sw.js').catch(() => {});
  // Si el SW instala una versión nueva habiendo ya una activa, recarga solo
  let refreshed = false;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (hadController && !refreshed) { refreshed = true; location.reload(); }
  });
}
// Service worker: al salir se anula el registro y se borra la caché
// para que la próxima entrada cargue siempre la última versión.
async function nukeServiceWorker() {
  try {
    if (!('serviceWorker' in navigator) || !('caches' in window)) return;
    const regs = await navigator.serviceWorker.getRegistrations();
    await Promise.all(regs.map(r => r.unregister()));
    const keys = await caches.keys();
    await Promise.all(keys.map(k => caches.delete(k)));
  } catch {}
}

// Latido de pestaña: el servidor apaga todo al cerrar la última (modo .app).
// Cada pestaña tiene id propio (sessionStorage); las demás pestañas del
// navegador no laten y no se ven afectadas.
let tabId = null;
try { tabId = sessionStorage.getItem('neverred_tab'); } catch { tabId = null; }
if (!tabId) { tabId = uid(); try { sessionStorage.setItem('neverred_tab', tabId); } catch {} }
function pingTab() {
  try {
    fetch(api('/api/ping'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tab: tabId }),
      keepalive: true,
    }).catch(() => {});
  } catch {}
}
pingTab();
setInterval(pingTab, 10000);
