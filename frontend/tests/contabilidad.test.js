// Tests de la lógica contable pura (lib/contabilidad.js). Sin dependencias:
//   node --test frontend/tests/
const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const NR = require('../../lib/contabilidad.js');

const ACC = [
  { id: 'banco', type: 'Activo' },
  { id: 'tarjeta', type: 'Pasivo' },
  { id: 'capital', type: 'Patrimonio' },
  { id: 'sueldo', type: 'Ingreso' },
  { id: 'comida', type: 'Gasto' },
];
const line = (accountId, debit, credit) => ({ accountId, debit, credit });

describe('round2', () => {
  it('redondea a 2 decimales y tolera texto', () => {
    assert.equal(NR.round2(10.126), 10.13);
    assert.equal(NR.round2('7.5'), 7.5);
    assert.equal(NR.round2(null), 0);
  });
});

describe('entryTotal', () => {
  it('suma debe/haber y detecta el cuadre', () => {
    const t = NR.entryTotal({ lines: [line('banco', 100, 0), line('sueldo', 0, 100)] });
    assert.deepEqual(t, { d: 100, h: 100, balanced: true });
  });
  it('marca descuadres e importes vacíos', () => {
    assert.equal(NR.entryTotal({ lines: [line('banco', 100, 0)] }).balanced, false);
    assert.equal(NR.entryTotal({ lines: [] }).balanced, false);
  });
});

describe('saldos por naturaleza', () => {
  const entries = [
    { lines: [line('banco', 1000, 0), line('capital', 0, 1000)] }, // apertura
    { lines: [line('comida', 60, 0), line('banco', 0, 60)] },       // gasto
    { lines: [line('banco', 2000, 0), line('sueldo', 0, 2000)] },   // ingreso
  ];
  const t = NR.totalsByAccount(ACC, entries);
  it('activo y gasto restan (debe - haber)', () => {
    assert.equal(NR.balanceOf(ACC[0], t), 2940);
    assert.equal(NR.balanceOf(ACC[4], t), 60);
  });
  it('pasivo, patrimonio e ingreso suman (haber - debe)', () => {
    assert.equal(NR.balanceOf(ACC[2], t), 1000);
    assert.equal(NR.balanceOf(ACC[3], t), 2000);
  });
});

describe('ecuación fundamental', () => {
  it('cuadra con un ciclo completo', () => {
    const entries = [
      { lines: [line('banco', 3500, 0), line('capital', 0, 3500)] },
      { lines: [line('banco', 2450, 0), line('sueldo', 0, 2450)] },
      { lines: [line('comida', 300, 0), line('tarjeta', 0, 300)] },
      { lines: [line('tarjeta', 300, 0), line('banco', 0, 300)] },
    ];
    const tt = NR.typeTotals(ACC, entries);
    assert.deepEqual(tt, { Activo: 5650, Pasivo: 0, Patrimonio: 3500, Ingreso: 2450, Gasto: 300 });
    assert.equal(NR.isBooksBalanced(ACC, entries), true);
  });
  it('detecta el descuadre', () => {
    const entries = [{ lines: [line('banco', 100, 0), line('sueldo', 0, 90)] }];
    assert.equal(NR.isBooksBalanced(ACC, entries), false);
  });
});

describe('navegación por meses', () => {
  it('monthKey extrae YYYY-MM', () => {
    assert.equal(NR.monthKey('2026-10-05'), '2026-10');
    assert.equal(NR.monthKey('2026-10'), '2026-10');
    assert.equal(NR.monthKey(''), '');
  });
  it('addMonths avanza, retrocede y cruza de año', () => {
    assert.equal(NR.addMonths('2026-10', 1), '2026-11');
    assert.equal(NR.addMonths('2026-10', -1), '2026-09');
    assert.equal(NR.addMonths('2026-12', 1), '2027-01');
    assert.equal(NR.addMonths('2026-01', -1), '2025-12');
    assert.equal(NR.addMonths('2026-10', 0), '2026-10');
    assert.equal(NR.addMonths('2026-10', -10), '2025-12');
  });
  it('monthStart/monthEnd acotan el mes (incluido febrero bisiesto)', () => {
    assert.equal(NR.monthStart('2026-10'), '2026-10-01');
    assert.equal(NR.monthEnd('2026-10'), '2026-10-31');
    assert.equal(NR.monthEnd('2026-02'), '2026-02-28');
    assert.equal(NR.monthEnd('2024-02'), '2024-02-29');
  });
  it('monthLabel habla español', () => {
    assert.equal(NR.monthLabel('2026-10'), 'Octubre de 2026');
    assert.equal(NR.monthLabel('2026-01'), 'Enero de 2026');
  });
  it('minMonth encuentra el mes más antiguo con asientos', () => {
    assert.equal(NR.minMonth([]), null);
    assert.equal(NR.minMonth([
      { date: '2026-10-05' }, { date: '2026-04-12' }, { date: '2026-07-01' },
    ]), '2026-04');
  });
  it('currentMonth tiene formato YYYY-MM', () => {
    assert.match(NR.currentMonth(), /^\d{4}-\d{2}$/);
  });
});
