#!/usr/bin/env python3
"""Integración HTTP de los flujos que usa el frontal.

Arranca el servidor real sobre una BD temporal y recorre el camino crítico
login → guardar → recargar → conflicto 409, además de la persistencia entre
logout y login. No usa red externa ni dependencias.

Uso desde la raíz del repo:
    python3 backend/tests/test_integration.py
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["NEVERRED_DB"] = _tmp.name
os.environ["NEVERRED_RATE_MAX"] = "1000"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402


DATA = {
    "accounts": [{"id": "a1", "code": "572", "name": "Banco", "type": "Activo"}],
    "entries": [{"id": "e1", "date": "2026-01-15", "desc": "Apertura",
                 "lines": [{"accountId": "a1", "debit": 100, "credit": 0},
                           {"accountId": "a1", "debit": 0, "credit": 100}]}],
    "seq": 2, "currency": "EUR",
}


def call(port, path, method="GET", payload=None, token=None, headers=None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), method=method, data=body,
        headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read().decode() or "{}"
            return r.status, dict(r.headers), json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {}
        return e.code, dict(e.headers), parsed


class IntegrationCase(unittest.TestCase):
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

    def _register(self, tag):
        st, _, body = call(self.port, "/api/register", "POST",
                           {"name": tag, "email": "%s@integration.local" % tag,
                            "password": "secreta123"})
        self.assertEqual(st, 201, body)
        return body["token"]

    def test_login_guardar_recargar_409(self):
        tok = self._register("flujo")
        st, headers, body = call(self.port, "/api/data", token=tok)
        self.assertEqual((st, body["data"]), (200, {}))
        etag0 = headers["ETag"]

        st, headers, body = call(
            self.port, "/api/data", "PUT", DATA, token=tok,
            headers={"If-Match": etag0})
        self.assertEqual(st, 200, body)
        etag1 = headers["ETag"]
        self.assertNotEqual(etag0, etag1)

        # "Recargar": el frontal vuelve a leer datos y ETag.
        st, headers, body = call(self.port, "/api/data", token=tok)
        self.assertEqual(st, 200)
        self.assertEqual(headers["ETag"], etag1)
        self.assertEqual(body["data"]["entries"][0]["desc"], "Apertura")

        # Otra pestaña guarda con el ETag viejo: conflicto, sin pisar nada.
        st, headers, body = call(
            self.port, "/api/data", "PUT", DATA, token=tok,
            headers={"If-Match": etag0})
        self.assertEqual(st, 409, body)
        self.assertEqual(headers["ETag"], etag1)
        self.assertEqual(body["etag"], etag1)
        self.assertEqual(body["server"]["entries"][0]["desc"], "Apertura")

        # Confirmación explícita: sin If-Match se sobrescribe.
        st, _, body = call(self.port, "/api/data", "PUT", DATA, token=tok)
        self.assertEqual(st, 200, body)

    def test_datos_sobreviven_logout_y_login(self):
        tok = self._register("persistencia")
        st, _, _ = call(self.port, "/api/data", "PUT", DATA, token=tok)
        self.assertEqual(st, 200)
        st, _, _ = call(self.port, "/api/logout", "POST", {}, token=tok)
        self.assertEqual(st, 200)
        st, _, _ = call(self.port, "/api/data", token=tok)
        self.assertEqual(st, 401)

        st, _, body = call(self.port, "/api/login", "POST",
                           {"email": "persistencia@integration.local",
                            "password": "secreta123"})
        self.assertEqual(st, 200, body)
        st, _, body = call(self.port, "/api/data", token=body["token"])
        self.assertEqual(st, 200)
        self.assertEqual(body["data"]["entries"][0]["desc"], "Apertura")


if __name__ == "__main__":
    unittest.main(verbosity=2)
