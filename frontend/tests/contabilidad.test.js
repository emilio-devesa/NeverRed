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
  it('entriesOfMonth filtra por mes', () => {
    const es = [{ date: '2026-09-05' }, { date: '2026-10-02' }, { date: '2026-10-20' }];
    assert.deepEqual(NR.entriesOfMonth(es, '2026-10'), [{ date: '2026-10-02' }, { date: '2026-10-20' }]);
    assert.deepEqual(NR.entriesOfMonth(es, '2026-08'), []);
    assert.deepEqual(NR.entriesOfMonth([], '2026-10'), []);
  });
  it('el PyG mensual cuadra con sus asientos', () => {
    const es = [
      { date: '2026-09-05', lines: [line('comida', 20, 0), line('banco', 0, 20)] },
      { date: '2026-10-02', lines: [line('banco', 100, 0), line('sueldo', 0, 100)] },
    ];
    const tt = NR.typeTotals(ACC, NR.entriesOfMonth(es, '2026-10'));
    assert.equal(tt.Ingreso - tt.Gasto, 100);
    const tt2 = NR.typeTotals(ACC, NR.entriesOfMonth(es, '2026-09'));
    assert.equal(tt2.Ingreso - tt2.Gasto, -20);
  });
});

describe('balanceSeries', () => {
  const ACCS = [
    { id: 'caja', name: 'Caja', type: 'Activo', archived: false },
    { id: 'banco', name: 'Banco', type: 'Activo', archived: false },
    { id: 'tarjeta', name: 'Tarjeta', type: 'Pasivo', archived: false },
    { id: 'vieja', name: 'Vieja', type: 'Activo', archived: true },
  ];
  const ES = [
    { id: 'e1', date: '2026-09-28', desc: 'A', lines: [line('caja', 1000, 0), line('banco', 0, 1000)] },
    { id: 'e2', date: '2026-10-02', desc: 'B', lines: [line('banco', 500, 0), line('caja', 0, 500)] },
    { id: 'e3', date: '2026-10-03', desc: 'C', lines: [line('comida', 0, 0), line('tarjeta', 0, 0)] },
    { id: 'e4', date: '2026-10-04', desc: 'D', lines: [line('comida', 40, 0), line('tarjeta', 0, 40)] },
  ];
  it('ventana, arrastre, relleno y totalActivoMenosPasivo', () => {
    const r = NR.balanceSeries(ACCS, ES, 5, '2026-10-04');
    assert.deepEqual(r.dates, ['2026-09-30', '2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04']);
    assert.equal(r.series.length, 2); // archivadas fuera
    assert.deepEqual(r.series[0].points, [1000, 1000, 500, 500, 500]);
    assert.deepEqual(r.series[1].points, [-1000, -1000, -500, -500, -500]);
    // total = activos − pasivos: (1000−1000)=0 … (500−500)−40=−40
    assert.deepEqual(r.total, [0, 0, 0, 0, -40]);
  });
  it('ignora fechas inválidas y futuras', () => {
    const es = ES.concat([{ id: 'x', date: 'mañana', desc: 'X', lines: [line('caja', 999, 0), line('banco', 0, 999)] }]);
    const r = NR.balanceSeries(ACCS, es, 2, '2026-10-04');
    assert.deepEqual(r.series[0].points, [500, 500]);
  });
  it('grupo Pasivo: series de pasivos y total suma de pasivos', () => {
    const r = NR.balanceSeries(ACCS, ES, 5, '2026-10-04', 'Pasivo');
    assert.equal(r.series.length, 1);
    assert.equal(r.series[0].name, 'Tarjeta');
    assert.deepEqual(r.series[0].points, [0, 0, 0, 0, 40]);
    assert.deepEqual(r.total, [0, 0, 0, 0, 40]);
  });
  it('grupo por defecto sigue siendo Activo con patrimonio', () => {
    const r = NR.balanceSeries(ACCS, ES, 5, '2026-10-04');
    assert.equal(r.series.length, 2);
    assert.deepEqual(r.total, [0, 0, 0, 0, -40]);
  });
});

describe('mayorMovements', () => {
  const es = [
    { id: 'e1', date: '2026-09-05', desc: 'A', lines: [line('banco', 1000, 0), line('capital', 0, 1000)] },
    { id: 'e2', date: '2026-09-20', desc: 'B', lines: [line('comida', 60, 0), line('banco', 0, 60)] },
    { id: 'e3', date: '2026-10-02', desc: 'C', lines: [line('banco', 500, 0), line('sueldo', 0, 500)] },
  ];
  it('global: saldo corrido completo', () => {
    const r = NR.mayorMovements(es, 'banco', 'Activo', null);
    assert.equal(r.inicial, 0);
    assert.equal(r.movs.length, 3);
    assert.deepEqual(r.movs.map(m => m.run), [1000, 940, 1440]);
    assert.equal(r.final, 1440);
  });
  it('mensual: arrastra lo anterior y corta lo posterior', () => {
    const r = NR.mayorMovements(es, 'banco', 'Activo', '2026-10');
    assert.equal(r.inicial, 940);
    assert.equal(r.movs.length, 1);
    assert.equal(r.movs[0].run, 1440);
    assert.equal(r.final, 1440);
  });
  it('mensual sin movimientos: conserva el arrastre', () => {
    const r = NR.mayorMovements(es, 'banco', 'Activo', '2026-08');
    assert.deepEqual(r.movs, []);
    assert.equal(r.inicial, 0);
    assert.equal(r.final, 0);
    const r2 = NR.mayorMovements(es, 'comida', 'Gasto', '2026-10');
    assert.deepEqual(r2.movs, []);
    assert.equal(r2.inicial, 60);
    assert.equal(r2.final, 60);
  });
  it('respeta la naturaleza acreedora', () => {
    const r = NR.mayorMovements(es, 'capital', 'Patrimonio', null);
    assert.deepEqual(r.movs.map(m => m.run), [1000]);
  });
});

describe('archivadas en balanceSeries', () => {
  const accs = [
    { id: 'banco', type: 'Activo' },
    { id: 'vieja', type: 'Activo', archived: true },
  ];
  const es = [{ date: '2026-01-05', lines: [line('banco', 1000, 0)] },
    { date: '2026-01-06', lines: [line('vieja', 500, 0), line('banco', 0, 500)] }];
  it('archivar oculta de selectores pero sigue contando en series y total', () => {
    const d = NR.balanceSeries(accs, es, 10, '2026-01-10', 'Activo');
    const ids = d.series.map(s => s.accountId);
    assert.ok(ids.includes('vieja'), 'la archivada tiene serie');
    const last = d.total.length - 1;
    assert.equal(d.total[last], 1000, 'total cuenta ambas (500 + 500)');
  });
});

describe('paginate', () => {
  it('corta y cuenta el resto', () => {
    const r = NR.paginate([1, 2, 3, 4, 5], 2);
    assert.deepEqual(r.page, [1, 2]);
    assert.equal(r.rest, 3);
  });
  it('sin resto cuando cabe todo', () => {
    const r = NR.paginate([1, 2], 50);
    assert.deepEqual(r.page, [1, 2]);
    assert.equal(r.rest, 0);
  });
});
