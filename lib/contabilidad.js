/* NeverRed — lógica contable pura (sin DOM).
 * Se comparte entre el navegador (window.NR) y los tests de node (module.exports).
 * Todas las funciones son puras: reciben cuentas y asientos explícitos.
 */
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) module.exports = factory();
  else root.NR = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const TYPES = ['Activo', 'Pasivo', 'Patrimonio', 'Ingreso', 'Gasto'];
  // Naturaleza deudora: suben por el Debe. El resto es acreedora (sube por el Haber).
  const DEBIT_NATURE = new Set(['Activo', 'Gasto']);

  function round2(n) { return Math.round((Number(n) || 0) * 100) / 100; }

  function entryTotal(e) {
    const d = round2(e.lines.reduce((s, l) => s + (Number(l.debit) || 0), 0));
    const h = round2(e.lines.reduce((s, l) => s + (Number(l.credit) || 0), 0));
    return { d, h, balanced: d === h && d > 0 };
  }

  /** Totales por cuenta: {id: {debit, credit}} */
  function totalsByAccount(accounts, entries) {
    const t = {};
    for (const a of accounts) t[a.id] = { debit: 0, credit: 0 };
    for (const e of entries) for (const l of e.lines) {
      if (!t[l.accountId]) t[l.accountId] = { debit: 0, credit: 0 };
      t[l.accountId].debit = round2(t[l.accountId].debit + (Number(l.debit) || 0));
      t[l.accountId].credit = round2(t[l.accountId].credit + (Number(l.credit) || 0));
    }
    return t;
  }

  /** Saldo con signo según naturaleza (+ = saldo normal). */
  function balanceOf(acc, t) {
    const d = t[acc.id] ? t[acc.id].debit : 0;
    const h = t[acc.id] ? t[acc.id].credit : 0;
    return DEBIT_NATURE.has(acc.type) ? round2(d - h) : round2(h - d);
  }

  function typeTotals(accounts, entries) {
    const t = totalsByAccount(accounts, entries);
    const out = { Activo: 0, Pasivo: 0, Patrimonio: 0, Ingreso: 0, Gasto: 0 };
    for (const a of accounts) out[a.type] = round2(out[a.type] + balanceOf(a, t));
    return out;
  }

  /** Activo = Pasivo + Patrimonio + (Ingreso - Gasto) */
  function isBooksBalanced(accounts, entries) {
    const tt = typeTotals(accounts, entries);
    return round2(tt.Activo - tt.Pasivo - tt.Patrimonio - tt.Ingreso + tt.Gasto) === 0;
  }

  /* Navegación por meses (Diario, PyG mensual). Todo puro y determinista:
   * el mes es 'YYYY-MM' y las fechas de asiento 'YYYY-MM-DD'. Sin Intl
   * para no depender del ICU del entorno. */
  const MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];

  /** 'YYYY-MM-DD' (o 'YYYY-MM') -> 'YYYY-MM'. */
  function monthKey(dateStr) { return String(dateStr || '').slice(0, 7); }

  /** Desplaza un mes N posiciones (negativo = atrás). */
  function addMonths(ym, n) {
    const y = Number(String(ym).slice(0, 4));
    const m = Number(String(ym).slice(5, 7));
    if (!y || !m || m < 1 || m > 12) return ym;
    const total = (y * 12 + (m - 1)) + n;
    const ry = Math.floor(total / 12);
    const rm = (total % 12) + 1;
    return ry + '-' + String(rm).padStart(2, '0');
  }

  function monthStart(ym) { return ym + '-01'; }

  function monthEnd(ym) {
    const y = Number(String(ym).slice(0, 4));
    const m = Number(String(ym).slice(5, 7));
    if (!y || !m || m < 1 || m > 12) return ym + '-01';
    return ym + '-' + String(new Date(y, m, 0).getDate()).padStart(2, '0');
  }

  function currentMonth() {
    const n = new Date();
    return n.getFullYear() + '-' + String(n.getMonth() + 1).padStart(2, '0');
  }

  /** '2026-10' -> 'Octubre de 2026'. */
  function monthLabel(ym) {
    const y = String(ym).slice(0, 4);
    const m = Number(String(ym).slice(5, 7));
    if (!MESES[m - 1]) return String(ym);
    return MESES[m - 1].charAt(0).toUpperCase() + MESES[m - 1].slice(1) + ' de ' + y;
  }

  /** Mes más antiguo con asientos, o null si no hay. */
  function minMonth(entries) {
    let min = null;
    for (const e of entries || []) {
      const k = monthKey(e.date);
      if (/^\d{4}-\d{2}$/.test(k) && (min === null || k < min)) min = k;
    }
    return min;
  }

  /** 'YYYY-MM-DD' -> 'DD/MM/YYYY'. */
  function fmtDateES(iso) {
    const s = String(iso || '');
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
    return m ? `${m[3]}/${m[2]}/${m[1]}` : s;
  }

  /** Etiqueta del modo rango: ambos, solo desde o solo hasta. */
  function rangeLabel(from, to) {
    if (from && to) return `${fmtDateES(from)} - ${fmtDateES(to)}`;
    if (from) return `Desde ${fmtDateES(from)}`;
    if (to) return `Hasta ${fmtDateES(to)}`;
    return '';
  }

  /** Asientos del mes 'YYYY-MM'. */
  function entriesOfMonth(entries, ym) {
    return (entries || []).filter(e => monthKey(e.date) === ym);
  }

  /** Mayor de una cuenta con saldo corrido. ym=null → histórico completo.
   *  En modo mensual, lo anterior al mes suma al saldo inicial (arrastre)
   *  y lo posterior no cuenta (el final es el saldo a fin de mes).
   *  Devuelve {inicial, movs: [{date, desc, d, h, run}], final}. Puro. */
  function mayorMovements(entries, accountId, type, ym) {
    const start = ym ? monthStart(ym) : null;
    const end = ym ? monthEnd(ym) : null;
    const list = [...(entries || [])].sort((a, b) =>
      String(a.date).localeCompare(String(b.date)) ||
      String(a.id).localeCompare(String(b.id)));
    let run = 0, inicial = 0, started = false;
    const movs = [];
    for (const e of list) {
      for (const l of e.lines || []) {
        if (l.accountId !== accountId) continue;
        const d = Number(l.debit) || 0, h = Number(l.credit) || 0;
        const delta = DEBIT_NATURE.has(type) ? round2(d - h) : round2(h - d);
        if (start && e.date < start) { run = round2(run + delta); continue; }
        if (end && e.date > end) continue;
        if (!started) { inicial = run; started = true; }
        run = round2(run + delta);
        movs.push({ date: e.date, desc: e.desc, d, h, run });
      }
    }
    if (!started) inicial = run; // mes sin movimientos: el inicial es todo el arrastre
    return { inicial, movs, final: run };
  }

  /** Serie diaria de saldos para los gráficos de evolución (ventana de días
   *  hasta hoy, con arrastre previo y relleno). Devuelve
   *  {dates: [iso], series: [{accountId, name, points: [n]}], total: [n]},
   *  donde series cubre las cuentas del grupo ('Activo' por defecto, o
   *  'Pasivo') y total = activos − pasivos (patrimonio) para activos o la
   *  suma de pasivos para pasivos. Puro. */
  function balanceSeries(accounts, entries, days, todayStr, group) {
    days = days || 180;
    group = group === 'Pasivo' ? 'Pasivo' : 'Activo';
    const today = /^\d{4}-\d{2}-\d{2}$/.test(todayStr || '')
      ? todayStr
      : new Date().toISOString().slice(0, 10);
    const [ty, tm, td] = today.split('-').map(Number);
    const base = Date.UTC(ty, tm - 1, td);
    const dates = [];
    for (let i = days - 1; i >= 0; i--) {
      dates.push(new Date(base - i * 86400000).toISOString().slice(0, 10));
    }
    const accs = (accounts || []).filter(a => !a.archived);
    const series = accs.filter(a => a.type === group)
      .map(a => ({ accountId: a.id, name: a.name, points: [] }));
    const at = {};
    accs.forEach(a => { at[a.id] = { debit: 0, credit: 0, type: a.type }; });
    const evts = [...(entries || [])]
      .filter(e => /^\d{4}-\d{2}-\d{2}$/.test(e.date || ''))
      .sort((a, b) => String(a.date).localeCompare(String(b.date)));
    const signed = st =>
      (DEBIT_NATURE.has(st.type) ? st.debit - st.credit : st.credit - st.debit);
    let p = 0;
    const total = [];
    for (const d of dates) {
      while (p < evts.length && evts[p].date <= d) {
        for (const l of evts[p].lines || []) {
          const st = at[l.accountId];
          if (!st) continue;
          st.debit = round2(st.debit + (Number(l.debit) || 0));
          st.credit = round2(st.credit + (Number(l.credit) || 0));
        }
        p++;
      }
      let tA = 0, tP = 0;
      for (const a of accs) {
        const b = round2(signed(at[a.id]));
        if (a.type === 'Activo') tA = round2(tA + b);
        if (a.type === 'Pasivo') tP = round2(tP + b);
      }
      total.push(group === 'Pasivo' ? tP : round2(tA - tP));
      series.forEach(s => {
        const a = accs.find(x => x.id === s.accountId);
        s.points.push(round2(signed(at[s.accountId])));
      });
    }
    return { dates, series, total };
  }

  // Tendencia de una serie de cierres: 'up' si sube más del 0,1 %,
  // 'down' si baja más del 0,1 %, 'flat' en otro caso.
  function marketTrend(first, last) {
    if (!first) return 'flat';
    const pct = (last - first) / first * 100;
    return pct > 0.1 ? 'up' : (pct < -0.1 ? 'down' : 'flat');
  }
  // Estadísticas de una serie de cierres (máx/mín/variación). null si <2 puntos.
  function marketStats(closes) {
    if (!Array.isArray(closes) || closes.length < 2) return null;
    const first = closes[0], last = closes[closes.length - 1];
    let lo = Infinity, hi = -Infinity;
    for (const v of closes) {
      if (typeof v !== 'number' || !isFinite(v)) return null;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    const pct = first ? Math.round((last - first) / first * 10000) / 100 : 0;
    return { first, last, min: lo, max: hi, changePct: pct,
             trend: marketTrend(first, last), points: closes.length };
  }

  // --- Simuladores (Herramientas): cálculo puro, sin persistencia ---
  // Préstamo francés: TIN anual %, plazo en años. null si entradas inválidas.
  function loanQuote(principal, annualRatePct, years) {
    principal = Number(principal); annualRatePct = Number(annualRatePct);
    years = Number(years);
    if (!(principal > 0) || principal > 1e9) return null;
    if (!(annualRatePct >= 0) || annualRatePct > 100) return null;
    if (!(years >= 1) || years > 50 || Math.round(years) !== years) return null;
    const n = years * 12, r = annualRatePct / 100 / 12;
    const monthly = r === 0 ? principal / n
      : principal * r / (1 - Math.pow(1 + r, -n));
    let balance = principal, totalInterest = 0;
    const schedule = [];
    for (let y = 1; y <= years; y++) {
      let capY = 0, intY = 0;
      for (let m = 0; m < 12; m++) {
        const interest = balance * r;
        const amort = Math.min(monthly - interest, balance);
        balance = round2(balance - amort);
        intY = round2(intY + interest);
        capY = round2(capY + amort);
        if (balance <= 0) { balance = 0; break; }
      }
      totalInterest = round2(totalInterest + intY);
      schedule.push({ year: y, capital: capY, interest: intY });
      if (balance <= 0) break;
    }
    return { monthly: round2(monthly), total: round2(monthly * n),
             interest: totalInterest, schedule };
  }
  // Rendimiento compuesto: aportación mensual, TIN anual %, años. null si inválido.
  function yieldGrowth(initial, monthly, annualRatePct, years) {
    initial = Number(initial); monthly = Number(monthly);
    annualRatePct = Number(annualRatePct); years = Number(years);
    if (!(initial >= 0) || initial > 1e9) return null;
    if (!(monthly >= 0) || monthly > 1e7) return null;
    if (!(annualRatePct >= 0) || annualRatePct > 100) return null;
    if (!(years >= 1) || years > 50 || Math.round(years) !== years) return null;
    const r = annualRatePct / 100 / 12;
    let value = initial, invested = initial;
    const yearly = [];
    for (let y = 1; y <= years; y++) {
      for (let m = 0; m < 12; m++) {
        value = value * (1 + r) + monthly;
        invested += monthly;
      }
      value = round2(value);
      yearly.push({ year: y, invested: round2(invested), value });
    }
    return { invested: round2(invested), final: value,
             gain: round2(value - invested), yearly };
  }

  return { TYPES, DEBIT_NATURE, round2, entryTotal, totalsByAccount, balanceOf, typeTotals, isBooksBalanced,
    monthKey, addMonths, monthStart, monthEnd, currentMonth, monthLabel, minMonth, fmtDateES, rangeLabel, entriesOfMonth,
    mayorMovements, balanceSeries, marketTrend, marketStats, loanQuote, yieldGrowth };
});
