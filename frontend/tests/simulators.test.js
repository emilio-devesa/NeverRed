// Tests de los simuladores (lib/contabilidad.js). Sin dependencias:
//   node --test frontend/tests/
const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const NR = require('../../lib/contabilidad.js');

describe('loanQuote', () => {
  it('cuota francesa conocida: 120.000 € al 3 % a 25 años ≈ 569,05 €', () => {
    const q = NR.loanQuote(120000, 3, 25);
    assert.ok(Math.abs(q.monthly - 569.05) < 0.05, 'cuota=' + q.monthly);
    assert.equal(q.schedule.length, 25);
    const cap = q.schedule.reduce((s, y) => s + y.capital, 0);
    assert.ok(Math.abs(cap - 120000) < 1, 'capital amortizado=' + cap);
    assert.ok(Math.abs(q.total - (120000 + q.interest)) < 1);
  });
  it('sin intereses: cuota = principal / meses', () => {
    const q = NR.loanQuote(12000, 0, 1);
    assert.equal(q.monthly, 1000);
    assert.equal(q.interest, 0);
  });
  it('rechaza entradas inválidas', () => {
    assert.equal(NR.loanQuote(0, 3, 25), null);
    assert.equal(NR.loanQuote(-5, 3, 25), null);
    assert.equal(NR.loanQuote(1000, 101, 5), null);
    assert.equal(NR.loanQuote(1000, 3, 0), null);
    assert.equal(NR.loanQuote(1000, 3, 2.5), null);
    assert.equal(NR.loanQuote(1000, 3, 51), null);
    assert.equal(NR.loanQuote('hola', 3, 5), null);
  });
});

describe('yieldGrowth', () => {
  it('10.000 € + 200 €/mes al 5 % durante 10 años', () => {
    const g = NR.yieldGrowth(10000, 200, 5, 10);
    assert.equal(g.invested, 34000);
    assert.ok(g.final > g.invested, 'hay ganancia');
    assert.equal(g.gain, NR.round2(g.final - g.invested));
    assert.equal(g.yearly.length, 10);
    // ≈47.500 € con capitalización mensual
    assert.ok(Math.abs(g.final - 47526) < 1500, 'final=' + g.final);
  });
  it('sin rentabilidad: final = aportado', () => {
    const g = NR.yieldGrowth(5000, 100, 0, 3);
    assert.equal(g.final, 8600);
    assert.equal(g.gain, 0);
  });
  it('rechaza entradas inválidas', () => {
    assert.equal(NR.yieldGrowth(-1, 100, 5, 10), null);
    assert.equal(NR.yieldGrowth(1000, -100, 5, 10), null);
    assert.equal(NR.yieldGrowth(1000, 100, 101, 10), null);
    assert.equal(NR.yieldGrowth(1000, 100, 5, 0), null);
    assert.equal(NR.yieldGrowth(1000, 100, 5, 51), null);
  });
});
