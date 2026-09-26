/* NeverRed — contabilidad personal por partida doble. Sin dependencias. */
'use strict';

const LS_KEY = 'neverred_v1';
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

function uid() { return Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4); }
function load() { try { const raw = localStorage.getItem(LS_KEY); return raw ? JSON.parse(raw) : null; } catch { return null; } }
/** Clave de caché local por usuario (la fuente de verdad es la BD vía API). */
function userKey() { return LS_KEY + ':' + (currentUser ? currentUser.id : 'local'); }
function save() {
  try { localStorage.setItem(userKey(), JSON.stringify(state)); } catch {}
  queueSync();
}
/** Sube los datos a la base de datos (con anti-rebote para no saturar la API). */
function queueSync() {
  if (!sessionToken && !currentUser) return;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(syncToServer, 800);
}
async function syncToServer() {
  if (!sessionToken && !currentUser) return null;
  try {
    const res = await fetch(api('/api/data'), {
      method: 'PUT',
      headers: authHeaders(),
      body: JSON.stringify({ accounts: state.accounts, entries: state.entries, seq: state.seq, currency: state.user.currency, budgets: state.budgets || {}, recurring: state.recurring || [] }),
    });
    if (res.status === 401) { endSession(); return false; }
    return res.ok;
  } catch { return null; /* sin conexión: queda la copia local y se reintenta luego */ }
}
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
}

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
      <button class="btn ghost small" data-del="${e.id}">Eliminar</button>
    </div></article>`;
}
function renderDiario() {
  const q = (document.getElementById('searchDiario').value || '').toLowerCase();
  const from = document.getElementById('filterFrom').value, to = document.getElementById('filterTo').value;
  let list = [...state.entries].sort((a, b) => b.date.localeCompare(a.date) || b.id.localeCompare(a.id));
  if (from) list = list.filter(e => e.date >= from);
  if (to) list = list.filter(e => e.date <= to);
  if (q) list = list.filter(e => e.desc.toLowerCase().includes(q) ||
    e.lines.some(l => (accById(l.accountId)?.name || '').toLowerCase().includes(q)));
  document.getElementById('diarioList').innerHTML = list.length ? list.map(entryCard).join('')
    : '<p class="muted">Sin resultados. Prueba con otro filtro o crea un asiento nuevo.</p>';
}
function dupeEntry(id) {
  const e = state.entries.find(x => x.id === id);
  if (!e) return;
  openEntryModal({
    desc: e.desc + ' (copia)',
    lines: e.lines.map(l => ({ accountId: l.accountId, debit: l.debit, credit: l.credit })),
  });
}
document.getElementById('diarioList').addEventListener('click', ev => {
  const ed = ev.target.dataset.edit, del = ev.target.dataset.del, dupe = ev.target.dataset.dupe;
  if (ed) openEntryModal(null, ed);
  if (dupe) dupeEntry(dupe);
  if (del && confirm('¿Eliminar este asiento?')) {
    state.entries = state.entries.filter(e => e.id !== del); save(); renderAll();
  }
});
document.getElementById('recentList').addEventListener('click', ev => {
  const ed = ev.target.dataset.edit, dupe = ev.target.dataset.dupe;
  if (ed) { switchTab('diario'); openEntryModal(null, ed); }
  if (dupe) dupeEntry(dupe);
});
['searchDiario', 'filterFrom', 'filterTo'].forEach(id => document.getElementById(id).addEventListener('input', renderDiario));
function switchTab(name) {
  document.querySelector(`.tab[data-view="${name}"]`).click();
}

// ---------- Mayor ----------
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
  const rows = [];
  let run = 0;
  const entries = [...state.entries].sort((a, b) => a.date.localeCompare(b.date));
  for (const e of entries) for (const l of e.lines) {
    if (l.accountId !== acc.id) continue;
    const d = Number(l.debit) || 0, h = Number(l.credit) || 0;
    run = round2(run + (DEBIT_NATURE.has(acc.type) ? d - h : h - d));
    if (q && !e.desc.toLowerCase().includes(q) && !e.date.includes(q)) continue;
    rows.push(`<tr><td>${esc(e.date)}</td><td>${esc(e.desc)}</td><td>${esc(acc.name)}</td>
      <td class="num">${d ? fmtNum(d) : ''}</td><td class="num">${h ? fmtNum(h) : ''}</td><td class="num"><strong>${fmt(run)}</strong></td></tr>`);
  }
  document.getElementById('mayorName').textContent = `${acc.code} · ${acc.name}`;
  document.getElementById('mayorBalance').textContent = fmt(run);
  document.getElementById('mayorNature').textContent = `${acc.type} · ${DEBIT_NATURE.has(acc.type) ? 'deudora' : 'acreedora'}`;
  document.getElementById('mayorTable').querySelector('tbody').innerHTML =
    rows.join('') || '<tr><td colspan="6" class="muted">Sin movimientos en esta cuenta.</td></tr>';
}
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
function renderReports() {
  renderBudgets();
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
  const tt = typeTotals();
  const res = round2(tt.Ingreso - tt.Gasto);
  document.getElementById('pnlBox').innerHTML = `
    <table class="table"><tbody>
    <tr><td>Total ingresos</td><td class="num">${fmt(tt.Ingreso)}</td></tr>
    <tr><td>Total gastos</td><td class="num">${fmt(tt.Gasto)}</td></tr>
    <tr><td><strong>Resultado</strong></td><td class="num"><strong style="color:${res < 0 ? 'var(--red)' : 'var(--green)'}">${fmt(res)}</strong></td></tr>
    </tbody></table>
    <p class="muted small">${res >= 0 ? '🟢 Ganas más de lo que gastas. Sigue así.' : '🔴 Gastas más de lo que ingresas: revisa tus gastos.'}</p>`;
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
  save(); entryModal.hidden = true; renderAll();
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
  state = { user: { name: '', currency: 'EUR' }, accounts: BASE_ACCOUNTS.map(a => ({ id: uid(), ...a, archived: false })), entries: [], seq: 1 };
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
const API_BASE = location.protocol === 'file:' ? 'http://127.0.0.1:8000' : '';
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
      : 'No hay conexión con el servidor. Ejecuta: python3 backend/server.py';
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
  try {
    if (FROM_FILE) localStorage.setItem('neverred_session', token);
    else localStorage.removeItem('neverred_session');
  } catch {}
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
  sessionToken = null; currentUser = null;
  clearTimeout(saveTimer);
  state = freshState(''); // que el siguiente usuario no vea ni herede nada del anterior
  try { localStorage.removeItem('neverred_session'); } catch {}
  authOverlay.hidden = false;
}
/** Descarga los datos del usuario desde la BD (o migra la copia local antigua). */
async function loadUserData() {
  let server = null;
  try {
    const res = await fetch(api('/api/data'), { headers: authHeaders() });
    if (res.status === 401) { endSession(); return; }
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
  authOverlay.hidden = true;
  document.getElementById('userEmail').textContent = currentUser.email;
  state.user.name = currentUser.name;
  switchTab('inicio'); // al entrar siempre se muestra la vista general
  renderAll();
  if (!state.entries.length) {
    document.getElementById('wName').value = currentUser.name === 'contable' ? '' : currentUser.name;
    welcomeModal.hidden = false;
  }
}

// ---------- Eliminar cuenta ----------
document.getElementById('btnDeleteAccount').addEventListener('click', async () => {
  if (!confirm('¿Eliminar tu cuenta y TODA tu contabilidad? Esta acción no se puede deshacer.')) return;
  if (!confirm('Última confirmación: se borrarán tu usuario y todos tus datos del servidor.')) return;
  try {
    await fetch(api('/api/account'), {
      method: 'DELETE',
      headers: authHeaders(),
    });
  } catch {}
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

// ---------- Cambio de contraseña ----------
const passwordModal = document.getElementById('passwordModal');
document.getElementById('btnPassword').addEventListener('click', () => {
  document.getElementById('pwCurrent').value = '';
  document.getElementById('pwNew').value = '';
  document.getElementById('pwError').textContent = '';
  passwordModal.hidden = false;
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

// ---------- Init ----------
function renderAll() { renderDashboard(); renderDiario(); renderMayor(); renderAccounts(); renderReports(); }
document.addEventListener('keydown', e => { if (e.key === 'Escape') { entryModal.hidden = true; accountModal.hidden = true; passwordModal.hidden = true; resetModal.hidden = true; } });

async function pingBackend() {
  try { await fetch(api('/api/me')); return true; } // cualquier respuesta = servidor vivo
  catch { return false; }
}
async function boot() {
  authOverlay.hidden = false;
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
  if (resetToken) { openReset(true); return; } // viene del enlace del correo
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
