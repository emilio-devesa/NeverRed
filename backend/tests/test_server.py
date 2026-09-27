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
import urllib.request
import urllib.error

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
        cls.srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
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
        st, _, _ = call(self.port, "/api/account", "DELETE", token=tok)
        self.assertEqual(st, 200)
        st, _, _ = call(self.port, "/api/me", token=tok)
        self.assertEqual(st, 401)
        st, _, _ = call(self.port, "/api/login", "POST",
                        {"email": "adios@t.local", "password": "secreta123"})
        self.assertEqual(st, 401)

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
        _, h, _ = call(self.port, "/api/health", origin="null")
        self.assertEqual(h.get("Access-Control-Allow-Origin"), "*")
        _, h, _ = call(self.port, "/api/health", origin="http://127.0.0.1:9999")
        self.assertEqual(h.get("Access-Control-Allow-Origin"), "http://127.0.0.1:9999")
        _, h, _ = call(self.port, "/api/health", origin="https://maligno.example")
        self.assertIsNone(h.get("Access-Control-Allow-Origin"))

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

    def test_ping_y_poda(self):
        st, _, _ = call(self.port, "/api/ping", "POST", {"tab": "abc"})
        self.assertEqual(st, 200)
        st, _, _ = call(self.port, "/api/ping", "POST", {})
        self.assertEqual(st, 400)
        self.assertGreaterEqual(server.prune_tabs(), 1)
        self.assertEqual(server.prune_tabs(now=time.time() + 3600), 0)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
