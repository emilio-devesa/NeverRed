#!/usr/bin/env python3
"""Tests del backend de NeverRed (solo stdlib, sin dependencias).

Uso desde la raíz del repo:
    python3 backend/tests/test_server.py
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.request
import urllib.error
import gzip
import http.client

# BD temporal ANTES de importar el servidor (lee el entorno al importar)
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["NEVERRED_DB"] = _tmp.name

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402


def call(port, path, method="GET", payload=None, token=None, origin=None, cookie=None):
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if cookie:
        req.add_header("Cookie", cookie)
    if origin:
        req.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode() or "{}")
        except Exception:
            body = {}
        return e.code, dict(e.headers), body


def get_raw(port, path):
    """GET sin interpretar el cuerpo (para estáticos HTML/JS/SVG)."""
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path))
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def mkuser(port, tag):
    st, _, data = call(port, "/api/register", "POST",
                       {"name": tag, "email": "%s@t.local" % tag, "password": "secreta123"})
    assert st == 201, (st, data)
    return data["token"]


VALID_DATA = {
    "accounts": [{"id": "a1", "code": "572", "name": "Banco", "type": "Activo"}],
    "entries": [{"id": "e1", "date": "2026-01-15", "desc": "Apertura",
                 "lines": [{"accountId": "a1", "debit": 100, "credit": 0},
                           {"accountId": "a1", "debit": 0, "credit": 100}]}],
    "seq": 2, "currency": "EUR",
}


class ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.init_db()
        cls.srv = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.srv.server_address[1]
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        os.unlink(os.environ["NEVERRED_DB"])

    def setUp(self):
        server._rate.clear()

    def test_health(self):
        st, _, body = call(self.port, "/api/health")
        self.assertEqual(st, 200)
        self.assertTrue(body["ok"])

    def test_500_framing(self):
        orig = server.Handler.do_GET
        def boom(self):
            raise RuntimeError("fallo de prueba")
        server.Handler.do_GET = boom
        try:
            st, headers, raw = get_raw(self.port, "/api/health")
        finally:
            server.Handler.do_GET = orig
        self.assertEqual(st, 500)
        self.assertEqual(int(headers["Content-Length"]), len(raw))
        self.assertEqual(json.loads(raw.decode())["error"], "Error interno.")

    def test_register_carrera_409(self):
        """ Carrera de registro: dos+ hilos con el mismo email a la vez.
        Nunca 500: el IntegrityError del índice UNIQUE se captura y sale 409
        (o el 429 del rate-limit global de arranque)."""
        # Hash barato: el test valida la lógica de carrera, no PBKDF2.
        hbarato = "pbkdf2$1$ab$cd"
        with unittest.mock.patch.object(server, "hash_password",
                                        lambda pw, salt=None: hbarato):
            email = "race@test.local"
            resultados = []
            exs = []
            lock = threading.Lock()

            def go():
                try:
                    st, _, _ = call(self.port, "/api/register", "POST",
                                    {"email": email, "password": "Abcdefgh1",
                                     "name": "R"})
                except Exception as e:
                    st = -1
                    exs.append(repr(e))
                with lock:
                    resultados.append(st)

            hilos = [threading.Thread(target=go) for _ in range(8)]
            for t in hilos:
                t.start()
            for t in hilos:
                t.join(20)
            self.assertEqual(len(resultados), 8)
            self.assertNotIn(-1, resultados, "excepciones: %r" % exs)
            self.assertNotIn(500, resultados)
            self.assertIn(201, resultados)
            self.assertTrue(all(s in (201, 409, 429) for s in resultados),
                            "códigos inesperados: %r" % resultados)

    def test_cabeceras_privacidad(self):
        # Referrer-Policy y Permissions-Policy en TODO tipo de respuesta:
        # estático HTML, API JSON y preflight OPTIONS (punto único).
        st, h, _ = get_raw(self.port, "/")
        self.assertEqual(st, 200)
        self.assertEqual(h.get("Referrer-Policy"), "no-referrer")
        self.assertIn("camera=()", h.get("Permissions-Policy", ""))
        st, h, _ = call(self.port, "/api/health")
        self.assertEqual(st, 200)
        self.assertEqual(h.get("Referrer-Policy"), "no-referrer")
        st, h, _ = call(self.port, "/api/health", "OPTIONS", origin=None)
        self.assertEqual(st, 204)
        self.assertEqual(h.get("Referrer-Policy"), "no-referrer")

    def test_server_header_sin_python(self):
        # B-12: Server informa la versión de la app sin destapar la de
        # Python del sistema (sys_version vacío, como relay y sink).
        st, h, _ = call(self.port, "/api/health")
        self.assertEqual(st, 200)
        self.assertEqual(h.get("Server"), "NeverRed/" + server.VERSION)

    def test_preflight_max_age(self):
        # El preflight se cachea en el navegador (Access-Control-Max-Age).
        st, h, _ = call(self.port, "/api/data", "OPTIONS",
                        origin="http://127.0.0.1:%d" % self.port)
        self.assertEqual(st, 204)
        self.assertEqual(h.get("Access-Control-Max-Age"), "86400")

    def test_register_login_datos(self):
        tok = mkuser(self.port, "flujo")
        st, _, me = call(self.port, "/api/me", token=tok)
        self.assertEqual(st, 200)
        self.assertEqual(me["user"]["email"], "flujo@t.local")
        st, _, got = call(self.port, "/api/data", token=tok)
        self.assertEqual(st, 200)
        self.assertEqual(got["data"], {})
        st, _, _ = call(self.port, "/api/data", "PUT", VALID_DATA, token=tok)
        self.assertEqual(st, 200)
        st, _, got = call(self.port, "/api/data", token=tok)
        self.assertEqual(got["data"]["entries"][0]["desc"], "Apertura")

    def test_registro_invalido(self):
        st, _, b = call(self.port, "/api/register", "POST",
                        {"name": "x", "email": "mal", "password": "secreta123"})
        self.assertEqual(st, 400)
        st, _, b = call(self.port, "/api/register", "POST",
                        {"name": "x", "email": "corta@t.local", "password": "corta"})
        self.assertEqual(st, 400)
        mkuser(self.port, "dupe")
        st, _, b = call(self.port, "/api/register", "POST",
                        {"name": "x", "email": "dupe@t.local", "password": "secreta123"})
        self.assertEqual(st, 409)
        st, _, b = call(self.port, "/api/login", "POST",
                        {"email": "dupe@t.local", "password": "incorrecta00"})
        self.assertEqual(st, 401)

    def test_rate_limit(self):
        server._rate["127.0.0.1"] = [server.RATE_MAX, int(time.time()) + 600]
        st, _, b = call(self.port, "/api/login", "POST",
                        {"email": "nadie@t.local", "password": "secreta123"})
        self.assertEqual(st, 429)

    def test_aislamiento(self):
        ta, tb = mkuser(self.port, "aisA"), mkuser(self.port, "aisB")
        call(self.port, "/api/data", "PUT", VALID_DATA, token=ta)
        st, _, got = call(self.port, "/api/data", token=tb)
        self.assertEqual(got["data"], {})

    def test_cambio_password(self):
        st, _, d = call(self.port, "/api/register", "POST",
                        {"name": "pw", "email": "pw@t.local", "password": "secreta123"})
        t1 = d["token"]
        st, _, d = call(self.port, "/api/login", "POST",
                        {"email": "pw@t.local", "password": "secreta123"})
        t2 = d["token"]
        st, _, _ = call(self.port, "/api/password", "POST",
                        {"current": "otra00000", "new": "nueva00000"}, token=t1)
        self.assertEqual(st, 403)
        st, _, _ = call(self.port, "/api/password", "POST",
                        {"current": "secreta123", "new": "corta"}, token=t1)
        self.assertEqual(st, 400)
        st, _, _ = call(self.port, "/api/password", "POST",
                        {"current": "secreta123", "new": "nueva00000"}, token=t1)
        self.assertEqual(st, 200)
        st, _, _ = call(self.port, "/api/me", token=t2)   # otra sesión, cerrada
        self.assertEqual(st, 401)
        st, _, _ = call(self.port, "/api/me", token=t1)   # la mía sigue válida
        self.assertEqual(st, 200)
        st, _, _ = call(self.port, "/api/login", "POST",
                        {"email": "pw@t.local", "password": "nueva00000"})
        self.assertEqual(st, 200)

    def test_borrar_cuenta(self):
        tok = mkuser(self.port, "adios")
        st, _, _ = call(self.port, "/api/account", "DELETE",
                        {"current": "incorrecta"}, token=tok)
        self.assertEqual(st, 403)  # sin la contraseña no se borra
        st, _, _ = call(self.port, "/api/account", "DELETE",
                        {"current": "secreta123"}, token=tok)
        self.assertEqual(st, 200)
        st, _, _ = call(self.port, "/api/me", token=tok)
        self.assertEqual(st, 401)
        st, _, _ = call(self.port, "/api/login", "POST",
                        {"email": "adios@t.local", "password": "secreta123"})
        self.assertEqual(st, 401)

    def test_borrar_cuenta_anonimiza_audit(self):
        # Tras "eliminar mi cuenta", audit_log no conserva su estela
        # (user_id a NULL y detail vacío — p. ej. el email del register),
        # conservando el contador de hechos.
        email = "estela@t.local"
        tok = mkuser(self.port, "estela")
        uid = server.db().execute(
            "SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]
        con = server.db()
        init_rows = con.execute(
            "SELECT COUNT(*) FROM audit_log WHERE user_id = ?", (uid,)).fetchone()[0]
        self.assertGreater(init_rows, 0)   # register + demo… ya quedó auditada
        con.close()
        st, _, _ = call(self.port, "/api/account", "DELETE",
                        {"current": "secreta123"}, token=tok)
        self.assertEqual(st, 200)
        con = server.db()
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM audit_log WHERE user_id = ?",
                        (uid,)).fetchone()[0], 0)
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'register'"
                        " AND detail = ?", (email,)).fetchone()[0], 0)
        deletes = con.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action = 'account_delete'"
            " AND user_id IS NULL AND detail = ''").fetchone()[0]
        self.assertGreaterEqual(deletes, 1)
        con.close()

    def test_audit_tope_por_tamano(self):
        # B-8/A-3-9: audit_log no puede engordar sin límite entre purgas por
        # fecha — tras cada inserción solo se conservan las AUDIT_MAX filas
        # más recientes (tope configurable con NEVERRED_AUDIT_MAX).
        limite, anterior = 5, server.AUDIT_MAX
        server.AUDIT_MAX = limite
        try:
            for i in range(12):
                server.audit("login_fail", detail="tope%d" % i)
            con = server.db()
            total = con.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action = 'login_fail'"
                " AND detail LIKE 'tope%'").fetchone()[0]
            primera = con.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action = 'login_fail'"
                " AND detail = 'tope0'").fetchone()[0]
            ultima = con.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action = 'login_fail'"
                " AND detail = 'tope11'").fetchone()[0]
            con.close()
        finally:
            server.AUDIT_MAX = anterior
        self.assertLessEqual(total, limite)   # cabe el tope, no más
        self.assertEqual(primera, 0)          # las viejas quedan fuera
        self.assertEqual(ultima, 1)           # las recientes se conservan

    def test_validacion_datos(self):
        tok = mkuser(self.port, "val")
        bad = json.loads(json.dumps(VALID_DATA))
        bad["entries"][0]["lines"][1]["credit"] = 50  # descuadre
        st, _, b = call(self.port, "/api/data", "PUT", bad, token=tok)
        self.assertEqual(st, 400)
        bad = json.loads(json.dumps(VALID_DATA))
        bad["entries"][0]["lines"][0]["accountId"] = "xxx"
        st, _, b = call(self.port, "/api/data", "PUT", bad, token=tok)
        self.assertEqual(st, 400)
        bad = json.loads(json.dumps(VALID_DATA))
        bad["entries"][0]["date"] = "2026-13-99"
        st, _, b = call(self.port, "/api/data", "PUT", bad, token=tok)
        self.assertEqual(st, 400)
        st, _, _ = call(self.port, "/api/data", "PUT", VALID_DATA, token=tok)
        self.assertEqual(st, 200)

    def test_cors(self):
        # Origen propio (mismo host y puerto): se refleja.
        own = "http://127.0.0.1:%d" % self.port
        _, h, _ = call(self.port, "/api/health", origin=own)
        self.assertEqual(h.get("Access-Control-Allow-Origin"), own)
        self.assertEqual(h.get("Vary"), "Origin")
        # Otro puerto del mismo host: ya NO se permite (antes sí).
        _, h, _ = call(self.port, "/api/health", origin="http://127.0.0.1:9999")
        self.assertIsNone(h.get("Access-Control-Allow-Origin"))
        _, h, _ = call(self.port, "/api/health", origin="http://localhost:%d" % self.port)
        self.assertIsNone(h.get("Access-Control-Allow-Origin"))
        # Modo archivo local: respuesta literal "null" (nunca "*").
        _, h, _ = call(self.port, "/api/health", origin="null")
        self.assertEqual(h.get("Access-Control-Allow-Origin"), "null")
        _, h, _ = call(self.port, "/api/health", origin="https://maligno.example")
        self.assertIsNone(h.get("Access-Control-Allow-Origin"))

    def test_estaticos_lista_blanca(self):
        # Lo que la app necesita se sirve…
        for path, ctype in (("/", "text/html"), ("/app.js", "javascript"),
                            ("/styles.css", "text/css"), ("/sw.js", "javascript"),
                            ("/lib/contabilidad.js", "javascript"),
                            ("/assets/avatars/a.svg", "image/svg+xml"),
                            ("/manifest.webmanifest", "manifest+json")):
            st, h, body = get_raw(self.port, path)
            self.assertEqual(st, 200, path)
            self.assertIn(ctype, h.get("Content-Type", ""), path)
            self.assertGreater(len(body), 0, path)
        # …y lo que no está en la lista blanca no se expone.
        for path in ("/REPORT.MD", "/README.md", "/Dockerfile", "/.dockerignore",
                     "/packaging/linux/build.sh", "/frontend/tests/market.test.js",
                     "/docs/NeverRed-Inicio.jpeg"):
            st, _, _ = get_raw(self.port, path)
            self.assertEqual(st, 404, path)
        # Salidas del árbol y backend: prohibido explícito.
        for path in ("/backend/server.py", "/../../etc/passwd", "/.git/config"):
            st, _, _ = get_raw(self.port, path)
            self.assertIn(st, (403, 404), path)
        # Archivo inexistente con extensión: 404 real, no index.html con 200.
        st, h, body = get_raw(self.port, "/no-existe.js")
        self.assertEqual(st, 404)
        self.assertNotIn(b"<!doctype html", body.lower())

    def test_ids_peligrosos_rechazados(self):
        tok = mkuser(self.port, "ids")
        malo = json.loads(json.dumps(VALID_DATA))
        malo["accounts"][0]["id"] = "\" onmouseover=\"alert(1)"
        st, _, _ = call(self.port, "/api/data", "PUT", malo, token=tok)
        self.assertEqual(st, 400)
        malo = json.loads(json.dumps(VALID_DATA))
        malo["entries"][0]["id"] = "e1'><script>alert(1)</script>"
        st, _, _ = call(self.port, "/api/data", "PUT", malo, token=tok)
        self.assertEqual(st, 400)
        # Los IDs normales (uid() del frontal) siguen valiendo.
        ok = json.loads(json.dumps(VALID_DATA))
        ok["accounts"][0]["id"] = "a-1_b.2:c"
        ok["entries"][0]["lines"][0]["accountId"] = "a-1_b.2:c"
        ok["entries"][0]["lines"][1]["accountId"] = "a-1_b.2:c"
        st, _, _ = call(self.port, "/api/data", "PUT", ok, token=tok)
        self.assertEqual(st, 200)

    def test_sesiones_y_auditoria(self):
        import server as srv
        import sqlite3
        st, _, d = call(self.port, "/api/register", "POST",
                        {"name": "ses", "email": "ses@t.local", "password": "secreta123"})
        t1 = d["token"]
        st, _, d = call(self.port, "/api/login", "POST",
                        {"email": "ses@t.local", "password": "secreta123"})
        t2 = d["token"]
        st, _, got = call(self.port, "/api/sessions", token=t1)
        self.assertEqual(st, 200)
        self.assertEqual(len(got["sessions"]), 2)
        cur = [s for s in got["sessions"] if s["current"]]
        self.assertEqual(len(cur), 1)
        st, _, done = call(self.port, "/api/sessions/rotate", "POST", token=t1)
        self.assertEqual(st, 200)
        self.assertEqual(done["closed"], 1)
        st, _, _ = call(self.port, "/api/me", token=t2)
        self.assertEqual(st, 401)
        st, _, _ = call(self.port, "/api/me", token=t1)
        self.assertEqual(st, 200)
        st, _, me = call(self.port, "/api/me", token=t1)
        self.assertEqual(st, 200)
        con = sqlite3.connect(srv.DB_PATH)
        acts = [r[0] for r in con.execute(
            "SELECT action FROM audit_log WHERE user_id = ? ORDER BY id",
            (me["user"]["id"],))]
        con.close()
        for a in ("register", "login", "sessions_rotate"):
            self.assertIn(a, acts)

    def test_validacion_extra(self):
        tok = mkuser(self.port, "extra")
        bad = json.loads(json.dumps(VALID_DATA))
        bad["budgets"] = {"a1": -5}
        st, _, _ = call(self.port, "/api/data", "PUT", bad, token=tok)
        self.assertEqual(st, 400)
        bad = json.loads(json.dumps(VALID_DATA))
        bad["recurring"] = [{"desc": "x", "day": 99, "lines": bad["entries"][0]["lines"]}]
        st, _, _ = call(self.port, "/api/data", "PUT", bad, token=tok)
        self.assertEqual(st, 400)
        good = json.loads(json.dumps(VALID_DATA))
        good["budgets"] = {"a1": 200}
        good["recurring"] = [{"id": "r1", "desc": "Alquiler",
                              "day": 5, "lastRun": "2026-09",
                              "lines": good["entries"][0]["lines"]}]
        st, _, _ = call(self.port, "/api/data", "PUT", good, token=tok)
        self.assertEqual(st, 200)
        st, _, got = call(self.port, "/api/data", token=tok)
        self.assertEqual(got["data"]["budgets"], {"a1": 200})
        self.assertEqual(len(got["data"]["recurring"]), 1)

    def test_etag_y_conflicto(self):
        tok = mkuser(self.port, "etag")
        st, h, _ = call(self.port, "/api/data", token=tok)
        etag = h.get("ETag")
        self.assertTrue(etag)
        # PUT sin If-Match funciona y rota el etag
        st, h2, _ = call(self.port, "/api/data", "PUT", VALID_DATA, token=tok)
        self.assertEqual(st, 200)
        self.assertNotEqual(h2.get("ETag"), etag)
        # PUT con etag viejo = 409 con los datos del servidor
        req = urllib.request.Request(
            "http://127.0.0.1:%d/api/data" % self.port, method="PUT",
            data=json.dumps(VALID_DATA).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + tok, "If-Match": etag})
        try:
            urllib.request.urlopen(req)
            self.fail("debió dar 409")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 409)
            body = json.loads(e.read().decode())
            self.assertIn("entries", body["server"])
        # con el etag nuevo sí entra
        req.add_header("If-Match", h2["ETag"])
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 200)
        # Validador débil exacto: If-None-Match con prefijo W/ real casado con
        # _etag_matches (el lstrip suelto anterior habría dejado p. ej.
        # "WoW/…" pasar). Sobre estáticos.
        etag = None
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/app.js")
        r = conn.getresponse()
        r.read()
        etag = r.headers.get("ETag")
        self.assertTrue(etag)
        conn.request("GET", "/app.js", headers={"If-None-Match": 'W/"' + etag.strip('"') + '"'})
        r = conn.getresponse()
        r.read()
        self.assertEqual(r.status, 304)
        # Y un tag sin correspondencia con el prefijo NO casaba (robustez):
        conn.request("GET", "/app.js", headers={"If-None-Match": 'WoW/"' + etag.strip('"') + '"'})
        r = conn.getresponse()
        r.read()
        self.assertEqual(r.status, 200)
        conn.close()

    def test_sesion_amortizada(self):
        tok = mkuser(self.port, "amortiza")
        con = server.db()

        def last_seen():
            return con.execute("SELECT last_seen FROM sessions WHERE token = ?",
                               (tok,)).fetchone()[0]

        t0 = last_seen()
        time.sleep(1.1)
        st, _, _ = call(self.port, "/api/me", token=tok)
        self.assertEqual(st, 200)
        # Menos de 5 min: no se reescribe last_seen.
        self.assertEqual(last_seen(), t0)
        # Pasados 5 min, la siguiente petición sí lo actualiza y mantiene viva
        # la sesión (la comprobación de caducidad se hace en lectura).
        con.execute("UPDATE sessions SET last_seen = ? WHERE token = ?",
                    (int(time.time()) - 400, tok))
        con.commit()
        st, _, _ = call(self.port, "/api/me", token=tok)
        self.assertEqual(st, 200)
        self.assertGreater(last_seen(), int(time.time()) - 10)
        con.close()

    def test_foreign_keys_activas(self):
        con = server.db()
        self.assertEqual(con.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        with self.assertRaises(server.sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO sessions (token, user_id, created_at, expires_at, last_seen)"
                " VALUES ('huerfana', 999999, 0, 0, 0)")
        con.close()

    def test_tope_sesiones(self):
        mkuser(self.port, "tope")
        toks = []
        for _ in range(12):
            server._rate.clear()  # el rate-limit no es lo que se prueba aquí
            st, _, d = call(self.port, "/api/login", "POST",
                            {"email": "tope@t.local", "password": "secreta123"})
            toks.append(d["token"])
        st, _, got = call(self.port, "/api/sessions", token=toks[-1])
        self.assertLessEqual(len(got["sessions"]), 10)

    def test_demo_sin_registro(self):
        st, _, d1 = call(self.port, "/api/demo", "POST")
        self.assertEqual(st, 200)
        self.assertEqual(d1["user"]["email"], "demo@neverred.local")
        st, _, got = call(self.port, "/api/data", token=d1["token"])
        self.assertGreater(len(got["data"]["entries"]), 20)
        # idempotente: segunda llamada restablece y sigue entrando
        st, _, d2 = call(self.port, "/api/demo", "POST")
        self.assertEqual(st, 200)
        self.assertEqual(d2["user"]["id"], d1["user"]["id"])
        st, _, _ = call(self.port, "/api/login", "POST",
                        {"email": "demo@neverred.local", "password": "DemoNeverRed2026"})
        self.assertEqual(st, 200)

    def test_remember_me(self):
        st, h, _ = call(self.port, "/api/register", "POST",
                        {"name": "rm", "email": "rm@t.local", "password": "secreta123",
                         "remember": False})
        self.assertEqual(st, 201)
        self.assertNotIn("Max-Age", h.get("Set-Cookie", ""))  # cookie de sesión
        st, h, _ = call(self.port, "/api/login", "POST",
                        {"email": "rm@t.local", "password": "secreta123"})
        self.assertEqual(st, 200)
        self.assertIn("Max-Age=2592000", h.get("Set-Cookie", ""))  # 30 días

    def test_ping_y_poda(self):
        # El mapa de pestañas solo se usa con autoapagado; el test lo activa.
        with unittest.mock.patch.object(server, "QUIT_WHEN_IDLE", True):
            with server._tabs_lock:
                server._tabs.clear()
            st, _, _ = call(self.port, "/api/ping", "POST", {"tab": "abc"})
            self.assertEqual(st, 200)
            st, _, _ = call(self.port, "/api/ping", "POST", {})
            self.assertEqual(st, 400)
            self.assertGreaterEqual(server.prune_tabs(), 1)
            self.assertEqual(server.prune_tabs(now=time.time() + 3600), 0)

    def test_ping_sin_autopagado_no_acumula(self):
        # Sin autoapagado (Docker), los latidos no deben ocupar memoria.
        with unittest.mock.patch.object(server, "QUIT_WHEN_IDLE", False):
            with server._tabs_lock:
                server._tabs.clear()
            for i in range(5):
                call(self.port, "/api/ping", "POST", {"tab": "t%d" % i})
            self.assertEqual(server.prune_tabs(), 0)

    def test_mapa_rate_acotado(self):
        server._rate.clear()
        try:
            with unittest.mock.patch.object(server, "RATE_MAX_KEYS", 2):
                self.assertFalse(server.rate_limited("ip-a"))
                self.assertFalse(server.rate_limited("ip-b"))
                # Tabla llena y la clave es nueva: falla cerrado.
                self.assertTrue(server.rate_limited("ip-c"))
                self.assertNotIn("ip-c", server._rate)
                # Una clave ya registrada sigue su curso normal.
                self.assertFalse(server.rate_limited("ip-a"))
        finally:
            server._rate.clear()
            server.rate_reset("ip-a")

    def test_logout(self):
        tok = mkuser(self.port, "salir")
        st, _, _ = call(self.port, "/api/logout", "POST", token=tok)
        self.assertEqual(st, 200)
        st, _, _ = call(self.port, "/api/me", token=tok)
        self.assertEqual(st, 401)

    def test_cookie_httponly(self):
        st, headers, _ = call(self.port, "/api/register", "POST",
                              {"name": "ck", "email": "ck@t.local", "password": "secreta123"})
        self.assertEqual(st, 201)
        set_cookie = headers.get("Set-Cookie", "")
        self.assertIn("nr_session=", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Lax", set_cookie)
        cookie = set_cookie.split(";")[0]
        st, _, me = call(self.port, "/api/me", cookie=cookie)  # sin Bearer
        self.assertEqual(st, 200)
        self.assertEqual(me["user"]["email"], "ck@t.local")
        st, _, _ = call(self.port, "/api/logout", "POST", cookie=cookie)
        self.assertEqual(st, 200)
        st, _, _ = call(self.port, "/api/me", cookie=cookie)
        self.assertEqual(st, 401)

    def test_reset_password(self):
        links = []
        orig = server._send_reset_email
        server._send_reset_email = lambda to, link: links.append((to, link))
        try:
            # No filtra si el correo existe
            st, _, _ = call(self.port, "/api/reset-request", "POST",
                            {"email": "nadie@t.local"})
            self.assertEqual(st, 200)
            self.assertEqual(links, [])
            st, _, _ = call(self.port, "/api/reset-request", "POST",
                            {"email": "rst@t.local"})
            # usuario aún no existe: tampoco hay enlace
            self.assertEqual(st, 200)
            tok = mkuser(self.port, "rst")
            st, _, _ = call(self.port, "/api/reset-request", "POST",
                            {"email": "rst@t.local"})
            self.assertEqual(st, 200)
            self.assertEqual(len(links), 1)
            token = links[0][1].split("reset=")[1]
            st, _, _ = call(self.port, "/api/reset-confirm", "POST",
                            {"token": token, "new": "corta"})
            self.assertEqual(st, 400)
            st, _, _ = call(self.port, "/api/reset-confirm", "POST",
                            {"token": "invalido", "new": "nueva00000"})
            self.assertEqual(st, 400)
            st, _, _ = call(self.port, "/api/reset-confirm", "POST",
                            {"token": token, "new": "nueva00000"})
            self.assertEqual(st, 200)
            # el enlace es de un solo uso y la sesión anterior murió
            st, _, _ = call(self.port, "/api/me", token=tok)
            self.assertEqual(st, 401)
            st, _, _ = call(self.port, "/api/reset-confirm", "POST",
                            {"token": token, "new": "otra00000"})
            self.assertEqual(st, 400)
            st, _, _ = call(self.port, "/api/login", "POST",
                            {"email": "rst@t.local", "password": "nueva00000"})
            self.assertEqual(st, 200)
        finally:
            server._send_reset_email = orig

    def test_telemetria_optin_y_allowlist(self):
        tok = mkuser(self.port, "tm")
        st, _, d = call(self.port, "/api/telemetry-consent", token=tok)
        self.assertEqual(st, 200)
        self.assertEqual(d["enabled"], False)
        self.assertEqual(d["asked"], False)
        good = {"events": [{"event": "vista_diario"},
                           {"event": "asiento_creado", "props": {"n_lineas": 4}}]}
        st, _, _ = call(self.port, "/api/telemetry", "POST", good, token=tok)
        self.assertEqual(st, 403)  # sin opt-in no se guarda nada
        st, _, d = call(self.port, "/api/telemetry-consent", "PUT",
                        {"enabled": True}, token=tok)
        self.assertEqual(st, 200)
        self.assertEqual(d["enabled"], True)
        st, _, d = call(self.port, "/api/telemetry-consent", token=tok)
        self.assertEqual(d["asked"], True)
        st, _, d = call(self.port, "/api/telemetry", "POST", good, token=tok)
        self.assertEqual(st, 200)
        self.assertEqual(d["received"], 2)
        # allowlist: evento desconocido, prop desconocida y prop con texto
        for bad in ({"events": [{"event": "importe_euros"}]},
                    {"events": [{"event": "vista_diario", "props": {"x": 1}}]},
                    {"events": [{"event": "asiento_creado",
                                 "props": {"n_lineas": "muchas"}}]},
                    {"events": [{"event": "vista_diario"}] * 101}):
            st, _, _ = call(self.port, "/api/telemetry", "POST", bad, token=tok)
            self.assertEqual(st, 400)
        st, _, d = call(self.port, "/api/telemetry-summary", token=tok)
        self.assertEqual(st, 200)
        got = {r["event"]: r["count"] for r in d["summary"]}
        self.assertEqual(got, {"vista_diario": 1, "asiento_creado": 1})
        # revocar borra lo acumulado
        st, _, d = call(self.port, "/api/telemetry-consent", "PUT",
                        {"enabled": False}, token=tok)
        self.assertEqual(st, 200)
        st, _, d = call(self.port, "/api/telemetry-summary", token=tok)
        self.assertEqual(d["summary"], [])
        # y borrar la cuenta no deja restos
        st, _, _ = call(self.port, "/api/account", "DELETE",
                        {"current": "secreta123"}, token=tok)
        self.assertEqual(st, 200)
        import sqlite3
        con = sqlite3.connect(os.environ["NEVERRED_DB"])
        n = con.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
        m = con.execute("SELECT COUNT(*) FROM telemetry_consent").fetchone()[0]
        con.close()
        self.assertEqual((n, m), (0, 0))


    def test_estaticos_gzip_etag_304(self):
        app_js = os.path.join(server.BASE_DIR, "app.js")
        with open(app_js, "rb") as f:
            raw = f.read()
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            c.request("GET", "/app.js", headers={"Accept-Encoding": "gzip"})
            r = c.getresponse()
            body = r.read()
            self.assertEqual(r.status, 200)
            self.assertEqual(r.version, 11)
            self.assertFalse(r.will_close)
            self.assertEqual(r.getheader("Content-Encoding"), "gzip")
            self.assertIn("Accept-Encoding", r.getheader("Vary") or "")
            self.assertEqual(r.getheader("Cache-Control"), "no-cache")
            self.assertEqual(gzip.decompress(body), raw)
            etag = r.getheader("ETag")
            lm = r.getheader("Last-Modified")
            self.assertTrue(etag and lm)

            # If-None-Match: 304 sin cuerpo, reutilizando la conexión.
            c.request("GET", "/app.js", headers={"If-None-Match": etag})
            r = c.getresponse()
            self.assertEqual(r.status, 304)
            self.assertEqual(r.read(), b"")
            self.assertEqual(r.getheader("ETag"), etag)
            self.assertFalse(r.will_close)

            # If-Modified-Since como alternativa.
            c.request("GET", "/app.js", headers={"If-Modified-Since": lm})
            r = c.getresponse()
            self.assertEqual(r.status, 304)
            self.assertEqual(r.read(), b"")

            # q=0 no debe comprimir.
            c.request("GET", "/app.js", headers={"Accept-Encoding": "gzip;q=0"})
            r = c.getresponse()
            self.assertEqual(r.status, 200)
            self.assertIsNone(r.getheader("Content-Encoding"))
            self.assertEqual(r.read(), raw)
        finally:
            c.close()

    def test_head_http11_y_mime(self):
        styles = os.path.join(server.BASE_DIR, "styles.css")
        with open(styles, "rb") as f:
            raw = f.read()
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            c.request("HEAD", "/api/health")
            r = c.getresponse()
            self.assertEqual(r.status, 200)
            self.assertEqual(r.read(), b"")
            cl = int(r.getheader("Content-Length") or 0)
            self.assertGreater(cl, 0)

            c.request("HEAD", "/styles.css")
            r = c.getresponse()
            self.assertEqual(r.status, 200)
            self.assertEqual(r.read(), b"")
            self.assertEqual(int(r.getheader("Content-Length") or 0), len(raw))

            c.request("HEAD", "/no-existe.js")
            r = c.getresponse()
            self.assertEqual(r.status, 404)
            self.assertEqual(r.read(), b"")

            c.request("HEAD", "/app.js")
            r = c.getresponse()
            r.read()
            etag = r.getheader("ETag")
            self.assertTrue(etag)
            c.request("HEAD", "/app.js", headers={"If-None-Match": etag})
            r = c.getresponse()
            self.assertEqual(r.status, 304)
            self.assertEqual(r.read(), b"")
        finally:
            c.close()
        self.assertEqual(server.Handler.protocol_version, "HTTP/1.1")
        self.assertEqual(server.Handler.timeout, 15)
        self.assertTrue(server.Server.daemon_threads)
        for ext, ctype in ((".jpg", "image/jpeg"), (".jpeg", "image/jpeg"),
                           (".webp", "image/webp"), (".woff2", "font/woff2")):
            self.assertEqual(server.MIME[ext], ctype)

    def test_metricas_latencia(self):
        seen = []
        with unittest.mock.patch.object(server, "METRICS", True), \
                unittest.mock.patch.object(
                    server, "log_metric",
                    side_effect=lambda *args: seen.append(args)):
            st, _, _ = call(self.port, "/api/health")
            self.assertEqual(st, 200)
            time.sleep(0.05)
        self.assertTrue(any(a[0] == "GET" and a[1] == "/api/health" and a[2] == 200
                            and a[3] >= 0 for a in seen), seen)

    def test_keep_alive_no_filtra_cookie_ni_etag(self):
        mkuser(self.port, "keep")
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            c.request("POST", "/api/login",
                      json.dumps({"email": "keep@t.local", "password": "secreta123"}),
                      {"Content-Type": "application/json"})
            r = c.getresponse()
            data = json.loads(r.read())
            tok = data["token"]
            self.assertTrue(r.getheader("Set-Cookie"))
            self.assertFalse(r.will_close)

            # La cookie de login no debe repetirse en la siguiente respuesta.
            c.request("GET", "/api/health")
            r = c.getresponse()
            r.read()
            self.assertIsNone(r.getheader("Set-Cookie"))

            # El ETag del PUT tampoco debe filtrarse.
            c.request("PUT", "/api/data", json.dumps(VALID_DATA),
                      {"Content-Type": "application/json",
                       "Authorization": "Bearer " + tok})
            r = c.getresponse()
            r.read()
            self.assertEqual(r.status, 200)
            self.assertTrue(r.getheader("ETag"))

            c.request("GET", "/api/health")
            r = c.getresponse()
            r.read()
            self.assertIsNone(r.getheader("ETag"))
            self.assertIsNone(r.getheader("Set-Cookie"))
        finally:
            c.close()


    def test_http11_matriz_codigos(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            # 401 sin cuerpo de petición
            c.request("GET", "/api/data")
            r = c.getresponse()
            self.assertEqual(r.status, 401)
            self.assertGreater(int(r.getheader("Content-Length") or 0), 0)
            self.assertIn("error", json.loads(r.read()))
            self.assertFalse(r.will_close)

            # 404 de API y de estático
            c.request("GET", "/api/no-existe")
            r = c.getresponse()
            self.assertEqual(r.status, 404)
            self.assertGreater(int(r.getheader("Content-Length") or 0), 0)
            r.read()

            # 204 de preflight: sin cuerpo ni Content-Length.
            c.request("OPTIONS", "/api/data", headers={
                "Origin": "http://127.0.0.1:%d" % self.port,
                "Access-Control-Request-Method": "PUT",
            })
            r = c.getresponse()
            self.assertEqual(r.status, 204)
            self.assertIsNone(r.getheader("Content-Length"))
            self.assertEqual(r.read(), b"")
            self.assertFalse(r.will_close)

            # 200 y reutilización
            c.request("GET", "/api/health")
            r = c.getresponse()
            self.assertEqual(r.status, 200)
            r.read()

            # 409 con ETag
            tok = mkuser(self.port, "matriz")
            c.request("PUT", "/api/data", json.dumps(VALID_DATA),
                      {"Content-Type": "application/json",
                       "Authorization": "Bearer " + tok})
            r = c.getresponse()
            self.assertEqual(r.status, 200)
            r.read()
            self.assertTrue(r.getheader("ETag"))
            c.request("PUT", "/api/data", json.dumps(VALID_DATA),
                      {"Content-Type": "application/json",
                       "Authorization": "Bearer " + tok,
                       "If-Match": '"no-coincide"'})
            r = c.getresponse()
            self.assertEqual(r.status, 409)
            self.assertGreater(int(r.getheader("Content-Length") or 0), 0)
            self.assertTrue(r.getheader("ETag"))
            self.assertIn("error", json.loads(r.read()))
            self.assertFalse(r.will_close)

            # 429 con cuerpo: se drena para no romper la conexión.
            server._rate["127.0.0.1"] = [server.RATE_MAX, int(time.time()) + 600]
            c.request("POST", "/api/login",
                      json.dumps({"email": "nadie@t.local", "password": "secreta123"}),
                      {"Content-Type": "application/json"})
            r = c.getresponse()
            self.assertEqual(r.status, 429)
            self.assertGreater(int(r.getheader("Content-Length") or 0), 0)
            self.assertIn("error", json.loads(r.read()))
            self.assertFalse(r.will_close)

            c.request("GET", "/api/health")
            r = c.getresponse()
            self.assertEqual(r.status, 200)
            r.read()
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
