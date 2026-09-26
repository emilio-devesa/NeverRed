#!/usr/bin/env python3
"""Crea el usuario de demostración de NeverRed con ~6 meses de contabilidad."""
import calendar
import json
import random
import urllib.request

BASE = 'http://127.0.0.1:8000'
EMAIL, PASSWORD, NAME = 'demo@neverred.local', 'DemoNeverRed2026', 'Demo'
random.seed(7)

ACC = [
    ('a570', '570', 'Caja · Efectivo', 'Activo'),
    ('a572', '572', 'Banco cuenta principal', 'Activo'),
    ('a573', '573', 'Ahorros', 'Activo'),
    ('a520', '520', 'Tarjeta de crédito', 'Pasivo'),
    ('a101', '101', 'Capital inicial', 'Patrimonio'),
    ('a700', '700', 'Sueldos y salarios', 'Ingreso'),
    ('a701', '701', 'Ingresos extra', 'Ingreso'),
    ('a600', '600', 'Alquiler / Vivienda', 'Gasto'),
    ('a601', '601', 'Comida y supermercado', 'Gasto'),
    ('a602', '602', 'Transporte', 'Gasto'),
    ('a603', '603', 'Ocio y suscripciones', 'Gasto'),
    ('a604', '604', 'Salud', 'Gasto'),
    ('a605', '605', 'Suministros (luz, agua, internet)', 'Gasto'),
]
accounts = [{'id': i, 'code': c, 'name': n, 'type': t, 'archived': False}
            for i, c, n, t in ACC]

entries, n = [], [1]


def add(date, desc, pairs):
    """pairs: [(accountId, debit, credit)]"""
    d = round(sum(p[1] for p in pairs), 2)
    h = round(sum(p[2] for p in pairs), 2)
    assert d == h and d > 0, (desc, d, h)
    entries.append({'id': 'e%03d' % n[0], 'n': n[0], 'date': date, 'desc': desc,
                    'lines': [{'accountId': a, 'debit': db, 'credit': cr}
                              for a, db, cr in pairs]})
    n[0] += 1


def api(path, token=None, method='GET', payload=None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Content-Type': 'application/json'})
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


# 1) Registro (o login si ya existe)
st, data = api('/api/register', method='POST', payload={'name': NAME, 'email': EMAIL, 'password': PASSWORD})
if st == 409:
    st, data = api('/api/login', method='POST', payload={'email': EMAIL, 'password': PASSWORD})
assert st in (200, 201), data
token = data['token']
print('Usuario: %s (id %s)' % (data['user']['email'], data['user']['id']))

# 2) Asiento de apertura (1 abr 2026)
add('2026-04-01', 'Asiento de apertura',
    [('a570', 800, 0), ('a572', 3500, 0), ('a101', 0, 4300)])

# 3) Seis meses de vida: abr–sep 2026
supers = ['Mercadona semanal', 'Compra grande Carrefour', 'Frutería del barrio', 'Supermercado DIA']
ocios = [('Netflix + Spotify', 15.99), ('Cena con amigos', 42.5), ('Cine', 19.0),
         ('Concierto', 55.0), ('Libro', 22.9), ('Escapada fin de semana', 120.0)]
for m in range(4, 10):
    last = calendar.monthrange(2026, m)[1]
    top = min(last, 24) if m == 9 else last
    P = lambda d: '2026-%02d-%02d' % (m, min(d, top))  # noqa: E731
    add(P(1), 'Nómina del mes', [('a572', 2450, 0), ('a700', 0, 2450)])
    add(P(5), 'Alquiler piso', [('a600', 850, 0), ('a572', 0, 850)])
    add(P(3), 'Abono transporte', [('a602', 40, 0), ('a572', 0, 40)])
    imp_sum = round(random.uniform(82, 112), 2)
    add(P(12), 'Suministros (luz, agua, internet)', [('a605', imp_sum, 0), ('a572', 0, imp_sum)])
    for i in range(3):
        imp_sup = round(random.uniform(45, 130), 2)
        add(P(random.randint(4, top)), random.choice(supers),
            [('a601', imp_sup, 0), ('a520', 0, imp_sup)])
    desc, imp = random.choice(ocios)
    add(P(random.randint(6, top)), desc, [('a603', imp, 0), ('a572', 0, imp)])
    if m % 2 == 0:
        add(P(random.randint(8, top)), 'Suscripción gimnasio',
            [('a603', 35, 0), ('a572', 0, 35)])
    add(P(20), 'Transferencia a ahorros', [('a573', 200, 0), ('a572', 0, 200)])
    # pago de la tarjeta a fin de mes
    deuda = round(sum(l['debit'] for e in entries
                      for l in e['lines'] if l['accountId'] == 'a520'
                      and e['date'].startswith('2026-%02d' % m)) -
                  sum(l['credit'] for e in entries
                      for l in e['lines'] if l['accountId'] == 'a520'
                      and e['date'].startswith('2026-%02d' % m)), 2)
    # la deuda es crédito - débito en una cuenta de pasivo
    deuda = round(sum(l['credit'] - l['debit'] for e in entries
                      for l in e['lines'] if l['accountId'] == 'a520'
                      and e['date'].startswith('2026-%02d' % m)), 2)
    if deuda > 0:
        add(P(last), 'Pago tarjeta de crédito', [('a520', deuda, 0), ('a572', 0, deuda)])

# 4) Extras puntuales
add('2026-05-18', 'Venta bicicleta de segunda mano', [('a572', 150, 0), ('a701', 0, 150)])
add('2026-06-14', 'Farmacia', [('a604', 22.5, 0), ('a572', 0, 22.5)])
add('2026-06-20', 'Retirada cajero', [('a570', 120, 0), ('a572', 0, 120)])
add('2026-07-09', 'Cena en efectivo', [('a601', 38, 0), ('a570', 0, 38)])
add('2026-07-22', 'Freelance diseño web', [('a572', 320, 0), ('a701', 0, 320)])
entries.sort(key=lambda e: (e['date'], e['n']))

# 5) Guardar en la BD del usuario demo
payload = {'accounts': accounts, 'entries': entries, 'seq': n[0], 'currency': 'EUR'}
st, _ = api('/api/data', token=token, method='PUT', payload=payload)
assert st == 200, st

# 6) Verificación contable
st, got = api('/api/data', token=token)
ev = got['data']['entries']
assert all(round(sum(l['debit'] for l in e['lines']), 2) ==
           round(sum(l['credit'] for l in e['lines']), 2) > 0 for e in ev)
tot, bal = {}, {}
for a in accounts:
    tot[a['id']] = [0.0, 0.0]
for e in ev:
    for l in e['lines']:
        tot[l['accountId']][0] += l['debit']
        tot[l['accountId']][1] += l['credit']
for a in accounts:
    d, h = tot[a['id']]
    bal[a['type']] = bal.get(a['type'], 0) + (d - h if a['type'] in ('Activo', 'Gasto') else h - d)
ecu = round(bal.get('Activo', 0) - bal.get('Pasivo', 0) - bal.get('Patrimonio', 0) -
            bal.get('Ingreso', 0) + bal.get('Gasto', 0), 2)
print('Asientos: %d | Cuentas: %d' % (len(ev), len(accounts)))
print('Ecuación (debe ser 0):', ecu)
print('Patrimonio neto: %.2f € | Resultado: %.2f €' %
      (bal['Activo'] - bal['Pasivo'], bal.get('Ingreso', 0) - bal.get('Gasto', 0)))
