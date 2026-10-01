#!/usr/bin/env python3
"""Tests de Mercado (Herramientas): parser Alpha Vantage + /api/market/*.

Sin red: la llamada saliente se sustituye por un fixture. Sin cuota.

Uso desde la raíz del repo:
    python3 backend/tests/test_market.py
"""
import datetime
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["NEVERRED_DB"] = _tmp.name
os.environ["NEVERRED_RATE_MAX"] = "1000"
os.environ["NEVERRED_ALPHA_VANTAGE_KEY"] = "TESTKEY123456789"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402


def call(port, path, method="GET", payload=None, token=None):
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode() or "{}")
        except Exception:
            body = {}
        return e.code, body


def mkuser(port, tag):
    st, data = call(port, "/api/register", "POST",
                    {"name": tag, "email": "%s@m.local" % tag, "password": "secreta123"})
    assert st == 201, (st, data)
    return data["token"]


def fixture_series(n=250, start_price=100.0):
    """Payload TIME_SERIES_DAILY con n sesiones terminando hoy."""
    today = datetime.date.today()
    ts = {}
    for i in range(n):
        d = (today - datetime.timedelta(days=i)).isoformat()
        c = start_price + i * 0.1
        ts[d] = {"1. open": str(c), "2. high": str(c + 1),
                 "3. low": str(c - 1), "4. close": "%.4f" % c,
                 "5. volume": "1000"}
    return {"Meta Data": {"2. Symbol": "TEST"}, "Time Series (Daily)": ts}


class ParseCase(unittest.TestCase):
    def test_ventana_180_dias(self):
        data, err = server.parse_av_daily(fixture_series(250))
        self.assertIsNone(err)
        cutoff = (datetime.date.today() - datetime.timedelta(days=180)).isoformat()
        self.assertTrue(all(d >= cutoff for d in data["dates"]))
        self.assertEqual(len(data["dates"]), len(data["closes"]))
        self.assertGreater(len(data["dates"]), 100)

    def test_simbolo_invalido(self):
        _, err = server.parse_av_daily({"Error Message": "Invalid API call."})
        self.assertEqual(err, "invalid")

    def test_cuota(self):
        _, err = server.parse_av_daily(
            {"Information": "Thank you... 25 requests per day..."})
        self.assertEqual(err, "rate")
        _, err = server.parse_av_daily({"Note": "Thank you..."})
        self.assertEqual(err, "rate")
        _, err = server.parse_av_daily(None)
        self.assertEqual(err, "rate")

    def test_pocos_datos_usa_todo(self):
        old = (datetime.date.today() - datetime.timedelta(days=400)).isoformat()
        ts = {d: {"4. close": "10.0"} for d in
              [(datetime.date.fromisoformat(old) - datetime.timedelta(days=i)).isoformat()
               for i in range(60)]}
        data, err = server.parse_av_daily({"Time Series (Daily)": ts})
        self.assertIsNone(err)
        self.assertEqual(len(data["dates"]), 60)

    def test_normaliza(self):
        self.assertEqual(server.normalize_symbol(" aapl "), "AAPL")
        self.assertEqual(server.normalize_symbol("brk.b"), "BRK.B")


class MarketApiCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.init_db()
        cls.srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.srv.server_address[1]
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()
        time.sleep(0.2)
        cls._orig_av = server._av_get
        cls.calls = []
        cls.payload = fixture_series()

        def fake_av(symbol, key, full):
            cls.calls.append((symbol, full))
            assert key == "TESTKEY123456789", "la clave nunca debe viajar al frontal"
            return cls.payload

        server._av_get = fake_av

    @classmethod
    def tearDownClass(cls):
        server._av_get = cls._orig_av
        cls.srv.shutdown()
        cls.srv.server_close()
        os.unlink(os.environ["NEVERRED_DB"])

    def setUp(self):
        server._rate.clear()
        server._market_last_call = 0
        type(self).calls.clear()

    def test_sin_sesion(self):
        st, _ = call(self.port, "/api/market/status")
        self.assertEqual(st, 401)

    def test_status_no_filtra_clave(self):
        tok = mkuser(self.port, "mkt1")
        st, body = call(self.port, "/api/market/status", token=tok)
        self.assertEqual(st, 200)
        self.assertTrue(body["configured"])
        self.assertNotIn("TESTKEY", json.dumps(body))

    def test_clave_formato_y_borrado(self):
        tok = mkuser(self.port, "mkt2")
        st, _ = call(self.port, "/api/market/key", "POST", {"key": "corta"}, token=tok)
        self.assertEqual(st, 400)
        saved = os.environ.pop("NEVERRED_ALPHA_VANTAGE_KEY")
        try:
            st, body = call(self.port, "/api/market/key", "POST",
                            {"key": "MiClaveValida16"}, token=tok)
            self.assertEqual(st, 200)
            self.assertTrue(body["configured"])
            st, body = call(self.port, "/api/market/status", token=tok)
            self.assertTrue(body["configured"])
            st, body = call(self.port, "/api/market/tickers", "DELETE",
                            token=tok)
            # DELETE sin símbolo no rompe
            self.assertEqual(st, 200)
            st, _ = call(self.port, "/api/market/key", "DELETE", token=tok)
            self.assertEqual(st, 200)
            st, body = call(self.port, "/api/market/status", token=tok)
            self.assertFalse(body["configured"])
        finally:
            os.environ["NEVERRED_ALPHA_VANTAGE_KEY"] = saved
            server.clear_market_key()

    def test_add_lista_historial_borrado(self):
        tok = mkuser(self.port, "mkt3")
        st, body = call(self.port, "/api/market/tickers", "POST",
                        {"symbol": "aapl"}, token=tok)
        self.assertEqual(st, 200, body)
        h = body["history"]
        self.assertEqual(h["symbol"], "AAPL")
        self.assertIn(h["trend"], ("up", "down", "flat"))
        self.assertGreaterEqual(h["min"], 0)
        self.assertLessEqual(h["min"], h["max"])
        self.assertEqual(len(h["dates"]), len(h["closes"]))
        self.assertFalse(h["stale"])
        # La lista no gasta cuota: sirvió de caché
        st, body = call(self.port, "/api/market/tickers", token=tok)
        self.assertEqual(st, 200)
        self.assertEqual(len(body["tickers"]), 1)
        self.assertEqual(body["tickers"][0]["symbol"], "AAPL")
        self.assertIsNotNone(body["tickers"][0]["last"])
        n_calls = len(type(self).calls)
        st, body = call(self.port, "/api/market/history?symbol=AAPL", token=tok)
        self.assertEqual(st, 200)
        self.assertEqual(len(type(self).calls), n_calls)  # caché fresca: 0 llamadas
        # Refresh forzado con guard activo → sirve caducada marcada stale
        server._market_last_call = int(time.time())
        st, body = call(self.port, "/api/market/history?symbol=AAPL&refresh=1",
                        token=tok)
        self.assertEqual(st, 200)
        self.assertTrue(body["stale"])
        # Borrar
        st, _ = call(self.port, "/api/market/tickers?symbol=AAPL", "DELETE",
                     token=tok)
        self.assertEqual(st, 200)
        st, body = call(self.port, "/api/market/tickers", token=tok)
        self.assertEqual(body["tickers"], [])

    def test_ticker_inexistente(self):
        tok = mkuser(self.port, "mkt4")
        cls_payload = type(self).payload
        type(self).payload = {"Error Message": "Invalid API call."}
        try:
            st, body = call(self.port, "/api/market/tickers", "POST",
                            {"symbol": "ZZZZ"}, token=tok)
            self.assertEqual(st, 404, body)
        finally:
            type(self).payload = cls_payload

    def test_sin_clave(self):
        tok = mkuser(self.port, "mkt5")
        saved = os.environ.pop("NEVERRED_ALPHA_VANTAGE_KEY")
        try:
            st, body = call(self.port, "/api/market/tickers", "POST",
                            {"symbol": "AAPL"}, token=tok)
            self.assertEqual(st, 400)
        finally:
            os.environ["NEVERRED_ALPHA_VANTAGE_KEY"] = saved


if __name__ == "__main__":
    unittest.main(verbosity=1)
