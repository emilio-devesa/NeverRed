// Tests del service worker sin navegador: se evalúa sw.js en un contexto vm
// con una Cache API mínima. No usa dependencias.
const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, '../../sw.js'), 'utf8');
const CACHE = (SRC.match(/const CACHE = '([^']+)'/) || [])[1];
const BASE = 'http://neverred.local/';

function response(opts = {}) {
  return {
    ok: opts.ok !== false,
    headers: {
      get(name) {
        return name.toLowerCase() === 'cache-control' ? (opts.cacheControl || null) : null;
      },
    },
    clone() { return this; },
  };
}

function makeEnv() {
  const store = new Map();
  const abs = u => (String(u).startsWith('http') ? String(u) : new URL(String(u), BASE).href);
  const cache = {
    async addAll(urls) { urls.forEach(u => store.set(abs(u), response())); },
    async put(req, res) { store.set(abs(req.url || req), res); },
    async keys() { return [...store.keys()].map(url => ({ url })); },
    async delete(key) { store.delete(abs(key.url || key)); },
    async match(req) { return store.get(abs(req.url || req)); },
  };
  const caches = {
    async open() { return cache; },
    async keys() { return [CACHE]; },
    async delete() {},
    async match(req) { return store.get(abs(req.url || req)); },
  };
  const listeners = {};
  const self = {
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    async skipWaiting() {},
    clients: { async claim() {} },
  };
  const sandbox = { self, caches, URL, console, Promise, setTimeout };
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox);
  return { store, listeners, sandbox, abs };
}

async function install(env) {
  let done;
  env.listeners.install[0]({ waitUntil: p => { done = p; } });
  await done;
  await new Promise(r => setTimeout(r, 5));
}

async function fetchThrough(env, url, res) {
  env.sandbox.fetch = async () => res;
  let done;
  const request = { url: env.abs(url), method: 'GET' };
  env.listeners.fetch[0]({ request, respondWith: p => { done = p; } });
  const returned = await done;
  await new Promise(r => setTimeout(r, 5));
  return returned;
}

describe('service worker', () => {
  it('cachea estáticos normales', async () => {
    const env = makeEnv();
    await install(env);
    const r = response();
    assert.equal(await fetchThrough(env, '/assets/foto.png', r), r);
    assert.ok(env.store.has(env.abs('/assets/foto.png')));
  });

  it('no cachea respuestas no-store', async () => {
    const env = makeEnv();
    await install(env);
    await fetchThrough(env, '/assets/privado.png', response({ cacheControl: 'no-store' }));
    assert.equal(env.store.has(env.abs('/assets/privado.png')), false);
  });

  it('no cachea errores', async () => {
    const env = makeEnv();
    await install(env);
    await fetchThrough(env, '/assets/roto.png', response({ ok: false }));
    assert.equal(env.store.has(env.abs('/assets/roto.png')), false);
  });

  it('acota la caché y conserva la carcasa', async () => {
    const env = makeEnv();
    await install(env);
    const shell = ['/', '/index.html', '/styles.css', '/app.js', '/lib/contabilidad.js',
      '/icon.svg', '/manifest.webmanifest'];
    for (let i = 0; i < 45; i++) {
      await fetchThrough(env, `/assets/foto-${i}.png`, response());
    }
    assert.equal(env.store.size, 40);
    for (const s of shell) assert.ok(env.store.has(env.abs(s)), 'falta ' + s);
  });
});
