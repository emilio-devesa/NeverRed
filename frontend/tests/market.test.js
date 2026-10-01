// Tests de los helpers de Mercado (lib/contabilidad.js). Sin dependencias:
//   node --test frontend/tests/
const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const NR = require('../../lib/contabilidad.js');

describe('marketTrend', () => {
  it('verde al subir más del 0,1 %', () => {
    assert.equal(NR.marketTrend(100, 100.2), 'up');
  });
  it('rojo al bajar más del 0,1 %', () => {
    assert.equal(NR.marketTrend(100, 99.8), 'down');
  });
  it('blanco dentro de ±0,1 % (plana)', () => {
    assert.equal(NR.marketTrend(100, 100.05), 'flat');
    assert.equal(NR.marketTrend(100, 100), 'flat');
    assert.equal(NR.marketTrend(100, 99.95), 'flat');
  });
  it('plana sin primer dato', () => {
    assert.equal(NR.marketTrend(0, 50), 'flat');
  });
});

describe('marketStats', () => {
  it('calcula min/max/variación/tendencia', () => {
    const s = NR.marketStats([100, 120, 90, 110]);
    assert.equal(s.first, 100);
    assert.equal(s.last, 110);
    assert.equal(s.min, 90);
    assert.equal(s.max, 120);
    assert.equal(s.changePct, 10);
    assert.equal(s.trend, 'up');
    assert.equal(s.points, 4);
  });
  it('null con menos de 2 puntos o datos rotos', () => {
    assert.equal(NR.marketStats([100]), null);
    assert.equal(NR.marketStats([]), null);
    assert.equal(NR.marketStats([100, NaN]), null);
    assert.equal(NR.marketStats('hola'), null);
  });
});
