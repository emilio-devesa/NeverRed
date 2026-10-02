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
    """Payload TIME_SERIES_DAILY_ADJUSTED con n sesiones terminando hoy."""
    today = datetime.date.today()
    ts = {}
    for i in range(n):
        d = (today - datetime.timedelta(days=i)).isoformat()
        c = start_price + i * 0.1
        bar = {"1. open": str(c), "2. high": str(c + 1),
               "3. low": str(c - 1), "4. close": "%.4f" % c,
               "5. adjusted close": "%.4f" % (c * 0.99),
               "6. volume": "1000", "7. dividend amount": "0.0000",
               "8. split coefficient": "1.0"}
        if i == 30:
            bar["7. dividend amount"] = "0.5000"
        if i == 100:
            bar["8. split coefficient"] = "2.0"
        ts[d] = bar
    return {"Meta Data": {"2. Symbol": "TEST"}, "Time Series (Daily)": ts}


class ParseCase(unittest.TestCase):
    def test_ventana_180_dias(self):
        data, err = server.parse_av_daily(fixture_series(250))
        self.assertIsNone(err)
        cutoff = (datetime.date.today() - datetime.timedelta(days=180)).isoformat()
        self.assertTrue(all(d >= cutoff for d in data["dates"]))
        self.assertEqual(len(data["dates"]), len(data["closes"]))
        self.assertGreater(len(data["dates"]), 100)
        # Cierre ajustado y eventos del periodo.
        self.assertLess(data["closes"][-1], 200)  # ajustado (< crudo)
        kinds = {(e["type"], e["date"]) for e in data["events"]}
        today = datetime.date.today()
        self.assertIn(("dividend", (today - datetime.timedelta(days=30)).isoformat()),
                      kinds)
        self.assertIn(("split", (today - datetime.timedelta(days=100)).isoformat()),
                      kinds)

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

        def fake_av(symbol, key):
            cls.calls.append((symbol,))
            assert key, "la llamada saliente necesita clave del servidor"
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

    def test_clave_por_usuario(self):
        tokA = mkuser(self.port, "mktk1")
        tokB = mkuser(self.port, "mktk2")
        st, _ = call(self.port, "/api/market/key", "POST", {"key": "corta"}, token=tokA)
        self.assertEqual(st, 400)
        saved = os.environ.pop("NEVERRED_ALPHA_VANTAGE_KEY")
        try:
            # A configura la suya; B sigue sin clave.
            st, body = call(self.port, "/api/market/key", "POST",
                            {"key": "ClaveDeA12345678"}, token=tokA)
            self.assertEqual(st, 200)
            _, sA = call(self.port, "/api/market/status", token=tokA)
            _, sB = call(self.port, "/api/market/status", token=tokB)
            self.assertTrue(sA["configured"])
            self.assertFalse(sB["configured"])
            # B no puede añadir sin clave; A sí.
            server._market_last_call = 0
            st, _ = call(self.port, "/api/market/tickers", "POST",
                         {"symbol": "KEYA"}, token=tokB)
            self.assertEqual(st, 400)
            server._market_last_call = 0
            st, body = call(self.port, "/api/market/tickers", "POST",
                            {"symbol": "KEYA"}, token=tokA)
            self.assertEqual(st, 200, body)
            # A la borra y se queda sin clave (sin defecto de instalación).
            st, _ = call(self.port, "/api/market/key", "DELETE", token=tokA)
            self.assertEqual(st, 200)
            _, sA = call(self.port, "/api/market/status", token=tokA)
            self.assertFalse(sA["configured"])
        finally:
            os.environ["NEVERRED_ALPHA_VANTAGE_KEY"] = saved

    def test_borrado_cuenta_limpia_mercado(self):
        tok = mkuser(self.port, "mkdel")
        server._market_last_call = 0
        st, _ = call(self.port, "/api/market/tickers", "POST",
                     {"symbol": "BYE"}, token=tok)
        self.assertEqual(st, 200)
        st, _ = call(self.port, "/api/market/key", "POST",
                     {"key": "ClaveBye12345678"}, token=tok)
        self.assertEqual(st, 200)
        con = server.db()
        uid = con.execute("SELECT id FROM users WHERE email = ?",
                          ("mkdel@m.local",)).fetchone()["id"]
        con.close()
        st, _ = call(self.port, "/api/account", "DELETE",
                     {"current": "secreta123"}, token=tok)
        self.assertEqual(st, 200)
        con = server.db()
        try:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM market_tickers WHERE user_id = ?",
                (uid,)).fetchone()[0], 0)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM market_keys WHERE user_id = ?",
                (uid,)).fetchone()[0], 0)
        finally:
            con.close()

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

    def test_ajustado_una_llamada_con_eventos(self):
        # Serie ajustada en compact: 1 sola llamada, con eventos.
        tok = mkuser(self.port, "mkt6")
        calls = type(self).calls
        calls.clear()
        server._market_last_call = 0
        st, body = call(self.port, "/api/market/tickers", "POST",
                        {"symbol": "ADJUS"}, token=tok)
        self.assertEqual(st, 200, body)
        self.assertEqual(len(calls), 1)
        h = body["history"]
        self.assertTrue(any(e["type"] == "dividend" for e in h["events"]))
        self.assertTrue(any(e["type"] == "split" for e in h["events"]))
        st, body = call(self.port, "/api/market/tickers", token=tok)
        self.assertEqual(body["tickers"][0]["events"], h["events"])

    def test_usuarios_aislados(self):
        # Los tickers de A jamás aparecen en la lista de B.
        tokA = mkuser(self.port, "mkta")
        tokB = mkuser(self.port, "mktb")
        st, _ = call(self.port, "/api/market/tickers", "POST",
                     {"symbol": "AISLA"}, token=tokA)
        self.assertEqual(st, 200)
        st, body = call(self.port, "/api/market/tickers", token=tokB)
        self.assertEqual(st, 200)
        self.assertEqual(body["tickers"], [])
        st, body = call(self.port, "/api/market/status", token=tokB)
        self.assertEqual(body["tickers"], 0)
        st, body = call(self.port, "/api/market/status", token=tokA)
        self.assertEqual(body["tickers"], 1)

    def test_cuota_contador(self):
        # Autosuficiente: resetea el contador y gasta 2 llamadas.
        server._market_set_meta(server._av_today_key(), "0")
        tok = mkuser(self.port, "mktq")
        for sym in ("QUOTA", "QUOTB"):
            server._market_last_call = 0  # el guard de cuota no pinta aquí
            st, body = call(self.port, "/api/market/tickers", "POST",
                            {"symbol": sym}, token=tok)
            self.assertEqual(st, 200, body)
        st, body = call(self.port, "/api/market/status", token=tok)
        q = body["quota"]
        self.assertEqual((q["used"], q["limit"]), (2, 25))

    def test_demo_limpia_mercado(self):
        # La demo es efímera: entrar restablece tickers y clave.
        tok = mkuser(self.port, "mkdemo")
        server._market_last_call = 0
        st, _ = call(self.port, "/api/market/tickers", "POST",
                     {"symbol": "DEMOT"}, token=tok)
        self.assertEqual(st, 200)
        st, _ = call(self.port, "/api/market/key", "POST",
                     {"key": "ClaveDemo1234567"}, token=tok)
        self.assertEqual(st, 200)
        con = server.db()
        uid = con.execute("SELECT id FROM users WHERE email = ?",
                          ("mkdemo@m.local",)).fetchone()["id"]
        con.execute("UPDATE users SET email = ? WHERE id = ?",
                    (server.DEMO_EMAIL, uid))
        con.commit()
        con.close()
        st, _ = call(self.port, "/api/demo", "POST")
        self.assertEqual(st, 200)
        con = server.db()
        try:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM market_tickers WHERE user_id = ?",
                (uid,)).fetchone()[0], 0)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM market_keys WHERE user_id = ?",
                (uid,)).fetchone()[0], 0)
        finally:
            con.close()

    def test_export_log(self):
        tok = mkuser(self.port, "mkexp")
        st, body = call(self.port, "/api/me", token=tok)
        self.assertEqual(st, 200)
        first = body["last_export"]
        self.assertGreater(first, 0)  # al registrar empieza el reloj
        st, _ = call(self.port, "/api/export-log", "POST", {}, token=tok)
        self.assertEqual(st, 200)
        st, body = call(self.port, "/api/me", token=tok)
        self.assertGreaterEqual(body["last_export"], first)

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
