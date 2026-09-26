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

  return { TYPES, DEBIT_NATURE, round2, entryTotal, totalsByAccount, balanceOf, typeTotals, isBooksBalanced };
});
