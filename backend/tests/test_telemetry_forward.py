#!/usr/bin/env python3
"""Tests del forward de telemetría (solo stdlib).

Uso desde la raíz del repo:
    python3 backend/tests/test_telemetry_forward.py
"""
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
_sink = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_sink.close()
os.environ["NEVERRED_DB"] = _tmp.name
os.environ["NEVERRED_SINK_DB"] = _sink.name
os.environ["NEVERRED_SINK_TOKEN"] = "tok-secreto-test"
os.environ["NEVERRED_RATE_MAX"] = "1000"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402
import telemetry  # noqa: E402
import telemetry_forward  # noqa: E402
import telemetry_sink as sink  # noqa: E402


def sink_call(port, path, payload=None, token="tok-secreto-test"):
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), method="POST" if payload is not None else "GET",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode() or "{}")
        except Exception:
            body = {}
        return e.code, body


class ForwardCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.init_db()
        sink.init_db()
        from http.server import ThreadingHTTPServer
        cls.port = 8151
        cls.srv = ThreadingHTTPServer(("127.0.0.1", cls.port), sink.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        time.sleep(0.3)

    def test_backoff(self):
        self.assertEqual(telemetry.backoff_delay(0), 60)
        self.assertEqual(telemetry.backoff_delay(1), 120)
        self.assertEqual(telemetry.backoff_delay(10), 3600)  # tope

    def test_forward_loop_pasa_key_dir(self):
        calls = []

        class Cfg:
            sink = "http://127.0.0.1:1"

        class Con:
            def close(self):
                pass

        def fake_forward(con, iid, version, cfg, key_dir, force=False):
            calls.append(key_dir)
            raise KeyboardInterrupt  # corta el bucle infinito sin simular un fallo

        orig_sleep, orig_forward = telemetry_forward.time.sleep, telemetry_forward.forward_once
        telemetry_forward.time.sleep = lambda _: None
        telemetry_forward.forward_once = fake_forward
        try:
            with self.assertRaises(KeyboardInterrupt):
                telemetry_forward.forward_loop(
                    lambda: Con(), lambda: "iid", "2.6.8", lambda: Cfg(),
                    lambda: "/tmp/keydir")
        finally:
            telemetry_forward.time.sleep = orig_sleep
            telemetry_forward.forward_once = orig_forward
        self.assertEqual(calls, ["/tmp/keydir"])

    def test_sink_auth_y_allowlist(self):
        st, _ = sink_call(self.port, "/api/telemetry-ingest",
                          {"install_id": "x" * 16, "aggregates": []}, token="malo")
        self.assertEqual(st, 401)
        st, _ = sink_call(self.port, "/api/telemetry-ingest",
                          {"install_id": "x" * 16,
                           "aggregates": [{"event": "euros", "date": "2026-01-01",
                                           "hour": "10", "count": 1}]})
        self.assertEqual(st, 400)
        st, _ = sink_call(self.port, "/api/telemetry-ingest",
                          {"install_id": "x" * 16,
                           "aggregates": [{"event": "vista_diario",
                                           "date": "2026-01-01",
                                           "hour": "99", "count": 1}]})
        self.assertEqual(st, 400)

    def test_ida_y_vuelta_idempotente(self):
        telemetry.TELEMETRY_SINK = "http://127.0.0.1:%d" % self.port
        telemetry.TELEMETRY_TOKEN = "tok-secreto-test"
        try:
            con = sqlite3.connect(_tmp.name)
            now = int(time.time())
            con.execute("INSERT INTO telemetry_consent (user_id, enabled) VALUES (1,1)"
                        " ON CONFLICT(user_id) DO UPDATE SET enabled=1")
            con.executemany(
                "INSERT INTO telemetry_events (ts, user_id, event, props)"
                " VALUES (?,?,?,?)",
                [(now, 1, "vista_diario", "{}"), (now, 1, "vista_diario", "{}"),
                 (now, 1, "asiento_creado", '{"n_lineas": 2}')])
            con.execute("UPDATE telemetry_forward SET watermark = 0, fails = 0,"
                        " next_retry = 0 WHERE id = 1")
            con.commit()
            con.close()
            st = telemetry.forward_once(force=True)
            self.assertEqual(st.get("pending"), 0)
            st, body = sink_call(self.port, "/api/sink-summary")
            got = {(b["event"], b["count"]) for b in body["batches"]}
            self.assertIn(("vista_diario", 2), got)
            self.assertIn(("asiento_creado", 1), got)
            # hora, plataforma y stats viajan con el lote
            b0 = body["batches"][0]
            self.assertIn("hour", b0)
            self.assertEqual(b0["platform"], telemetry.TELEMETRY_PLATFORM)
            self.assertTrue(any(s["total_sends"] >= 1 for s in body["stats"]))
            # reintento: idempotente, sin duplicados
            con = sqlite3.connect(_tmp.name)
            con.execute("UPDATE telemetry_forward SET watermark = 0 WHERE id = 1")
            con.commit()
            con.close()
            telemetry.forward_once(force=True)
            st, body = sink_call(self.port, "/api/sink-summary")
            mine = [b for b in body["batches"]
                    if b["install_id"] == telemetry.get_install_id()]
            self.assertEqual(sum(b["count"] for b in mine), 3)
            # token malo con trabajo pendiente: falla y programa backoff
            con = sqlite3.connect(_tmp.name)
            con.execute("UPDATE telemetry_forward SET watermark = 0 WHERE id = 1")
            con.commit()
            con.close()
            telemetry.TELEMETRY_TOKEN = "otro"
            st = telemetry.forward_once(force=True)
            self.assertGreater(st["fails"], 0)
            self.assertGreater(st["next_retry_in"], 0)
        finally:
            telemetry.TELEMETRY_SINK = ""
            telemetry.TELEMETRY_TOKEN = ""

    def test_sin_receptor(self):
        telemetry.TELEMETRY_SINK = ""
        try:
            st = telemetry.forward_once(force=True)
            self.assertEqual(st["configured"], False)
        finally:
            pass

    def test_dashboard(self):
        sink_call(self.port, "/api/telemetry-ingest",
                  {"install_id": "d" * 16, "version": "t", "platform": "macOS",
                   "aggregates": [{"event": "vista_diario", "date": "2026-09-29",
                                   "hour": "10", "count": 3}]})
        import urllib.request
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/" % self.port) as r:
            html = r.read().decode()
        for needle in ("instalaciones totales", "pico en un día", "pico en una hora",
                       "fallos medios por envío", "Funciones más empleadas",
                       "Versiones", "Plataformas", "<svg"):
            self.assertIn(needle, html)

    def test_dashboard_escapa_html_y_atributos(self):
        self.assertEqual(sink._esc("a'b\"c<&"),
                         "a&#x27;b&quot;c&lt;&amp;")
        iid = "x' onmouseover='alert(1)"
        version = '<img src=x onerror=alert(1)>'
        con = sink.db()
        con.execute(
            "INSERT OR REPLACE INTO sink_batches (install_id, day, hour, event, count,"
            " version, platform, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (iid, "2026-10-20", "10", "vista_diario", 1, version, "macOS",
             int(time.time())))
        con.execute(
            "INSERT OR REPLACE INTO sink_identities (install_id, pubkey, status,"
            " first_seen, updated_at) VALUES (?,?,?,?,?)",
            (iid, "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest", "pending",
             int(time.time()), int(time.time())))
        con.commit()
        con.close()
        import urllib.request
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/" % self.port) as r:
            html = r.read().decode()
        self.assertNotIn(version, html)
        self.assertNotIn(iid, html)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
        self.assertIn("x&#x27; onmouseover=&#x27;alert(1)", html)


class RelayCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.init_db()
        sink.init_db()
        os.environ["NEVERRED_SINK_LOCAL"] = "http://127.0.0.1:8152"
        from http.server import ThreadingHTTPServer
        cls.sink_srv = ThreadingHTTPServer(("127.0.0.1", 8152), sink.Handler)
        threading.Thread(target=cls.sink_srv.serve_forever, daemon=True).start()
        cls.app_srv = ThreadingHTTPServer(("127.0.0.1", 8153), server.Handler)
        threading.Thread(target=cls.app_srv.serve_forever, daemon=True).start()
        time.sleep(0.3)

    def _relay(self, payload, token="tok-secreto-test"):
        req = urllib.request.Request(
            "http://127.0.0.1:8153/api/telemetry-ingest",
            data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode() or "{}")
            except Exception:
                body = {}
            return e.code, body

    def test_relay_ok_y_auth(self):
        st, body = self._relay({"install_id": "r" * 16, "version": "t",
                                "aggregates": [{"event": "vista_diario",
                                                "date": "2026-09-29", "count": 2}]})
        self.assertEqual(st, 200)
        self.assertEqual(body.get("stored"), 1)
        st, _ = self._relay({"install_id": "r" * 16, "version": "t",
                             "aggregates": [{"event": "vista_diario",
                                             "date": "2026-09-29", "count": 2}]},
                            token="malo")
        self.assertEqual(st, 401)  # el sink manda; el relay no abre nada
        st, _ = self._relay({"install_id": "r" * 16, "version": "t",
                             "aggregates": [{"event": "euros",
                                             "date": "2026-09-29", "count": 1}]})
        self.assertEqual(st, 400)

    def test_relay_sink_caido(self):
        os.environ["NEVERRED_SINK_LOCAL"] = "http://127.0.0.1:8199"
        try:
            st, body = self._relay({"install_id": "r" * 16, "version": "t",
                                    "aggregates": [{"event": "vista_diario",
                                                    "date": "2026-09-29",
                                                    "count": 1}]})
            self.assertEqual(st, 502)  # la beta reintentará con backoff
        finally:
            os.environ["NEVERRED_SINK_LOCAL"] = "http://127.0.0.1:8152"


if __name__ == "__main__":
    unittest.main(verbosity=2)
