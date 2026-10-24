#!/usr/bin/env python3
"""Tests del emparejamiento asimétrico de telemetría (solo stdlib + ssh-keygen).

Uso desde la raíz del repo:
    python3 backend/tests/test_telemetry_pairing.py
"""
import json
import os
import shutil
import sqlite3
import http.client
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
os.environ["NEVERRED_SINK_TOKEN"] = "tok-legado-test"
os.environ["NEVERRED_TELEMETRY_SINK"] = "http://127.0.0.1:8161"
os.environ.pop("NEVERRED_TELEMETRY_TOKEN", None)
os.environ["NEVERRED_RATE_MAX"] = "1000"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402
import telemetry  # noqa: E402
import telemetry_sink as sink  # noqa: E402
import telemetry_forward as fwd  # noqa: E402
import telemetry_common as common  # noqa: E402

HAVE_SSH = shutil.which("ssh-keygen") is not None


def call(port, path, payload=None, raw=None):
    data = raw if raw is not None else (
        json.dumps(payload).encode() if payload is not None else None)
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path),
        method="POST" if data is not None else "GET", data=data,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode() or "{}")
        except Exception:
            body = {}
        return e.code, body


def signed_body(iid, priv, aggs, stats=None):
    stats = stats if stats is not None else {"total_sends": 1, "total_fails": 0}
    canon = common.canonical_envelope(iid, "t", "macOS", stats, aggs)
    sig = fwd.sign_envelope(priv, canon)
    return {"install_id": iid, "version": "t", "platform": "macOS",
            "client_stats": stats, "aggregates": aggs, "signature": sig}


def identity(port, iid, action):
    """Aprobar/vetar como el CLI documentado: 1) token CSRF de
    /api/sink-summary, 2) POST /api/sink-identity con él."""
    st, summ = call(port, "/api/sink-summary")
    assert st == 200 and summ.get("csrf"), "no se pudo obtener token CSRF: %s" % summ
    return call(port, "/api/sink-identity",
                {"install_id": iid, "action": action, "csrf": summ["csrf"]})


@unittest.skipUnless(HAVE_SSH, "sin ssh-keygen")
class PairingCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.init_db()
        sink.init_db()
        from http.server import ThreadingHTTPServer
        cls.port = 8165
        cls.srv = ThreadingHTTPServer(("127.0.0.1", cls.port), sink.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        time.sleep(0.3)
        cls.keydir = tempfile.mkdtemp(prefix="nr-keys-")
        cls.priv, cls.pub = fwd.ensure_keypair(cls.keydir)
        assert cls.priv and cls.pub, "no se pudo generar el par"
        cls.iid = "p" * 32

    def agg(self, event="vista_diario", date="2026-09-29", count=2):
        # Con hora: el firmante canónico debe coincidir con lo que el sink
        # valida (build_aggregates siempre incluye hora).
        return [{"event": event, "date": date, "hour": "12", "count": count}]

    def test_01_keypair(self):
        st = os.stat(self.priv)
        self.assertEqual(oct(st.st_mode & 0o777), "0o600")
        self.assertIsNone(common.validate_pubkey(self.pub))
        priv2, pub2 = fwd.ensure_keypair(self.keydir)
        self.assertEqual((priv2, pub2), (self.priv, self.pub))  # idempotente

    def test_02_enroll_pending(self):
        st, body = call(self.port, "/api/telemetry-enroll",
                         {"install_id": self.iid, "pubkey": self.pub})
        self.assertEqual((st, body.get("enroll")), (200, "pending"))

    def test_03_ingest_pending_no_almacena(self):
        st, body = call(self.port, "/api/telemetry-ingest",
                         signed_body(self.iid, self.priv, self.agg()))
        self.assertEqual((st, body.get("enroll")), (403, "pending"))
        con = sqlite3.connect(_sink.name)
        n = con.execute("SELECT COUNT(*) FROM sink_batches WHERE install_id = ?",
                        (self.iid,)).fetchone()[0]
        con.close()
        self.assertEqual(n, 0)

    def test_04_approve_e_ingest(self):
        st, body = identity(self.port, self.iid, "approve")
        self.assertEqual((st, body.get("enroll")), (200, "approved"))
        st, body = call(self.port, "/api/telemetry-ingest",
                         signed_body(self.iid, self.priv, self.agg()))
        self.assertEqual(st, 200)
        self.assertEqual(body.get("stored"), 1)

    def test_05_tamper_falla(self):
        aggs = self.agg(count=2)
        payload = signed_body(self.iid, self.priv, aggs)
        payload["aggregates"][0]["count"] = 999  # manipulado tras firmar
        st, body = call(self.port, "/api/telemetry-ingest", payload)
        self.assertEqual(st, 403)
        self.assertIn("Firma", body.get("error", ""))

    def test_06_desconocido_requiere_enroll(self):
        other = tempfile.mkdtemp(prefix="nr-keys2-")
        priv2, _ = fwd.ensure_keypair(other)
        st, body = call(self.port, "/api/telemetry-ingest",
                         signed_body("q" * 32, priv2, self.agg()))
        self.assertEqual((st, body.get("enroll")), (403, "required"))

    def test_07_cambio_pubkey_requiere_reaprobar(self):
        other = tempfile.mkdtemp(prefix="nr-keys3-")
        _, pub3 = fwd.ensure_keypair(other)
        st, body = call(self.port, "/api/telemetry-enroll",
                         {"install_id": self.iid, "pubkey": pub3})
        self.assertEqual(body.get("enroll"), "pending")

    def test_08_reject_bloquea(self):
        st, body = identity(self.port, self.iid, "reject")
        self.assertEqual(body.get("enroll"), "rejected")
        st, body = call(self.port, "/api/telemetry-ingest",
                         signed_body(self.iid, self.priv, self.agg()))
        self.assertEqual((st, body.get("enroll")), (403, "rejected"))

    def test_09_legado_sigue_valiendo(self):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/api/telemetry-ingest" % self.port,
            data=json.dumps({"install_id": "legado11", "version": "t",
                             "aggregates": self.agg()}).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer tok-legado-test"})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 200)


@unittest.skipUnless(HAVE_SSH, "sin ssh-keygen")
class ForwardPairingCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.init_db()
        sink.init_db()
        # Puerto 8161 (el de NEVERRED_TELEMETRY_SINK): PairingCase lo reusa
        # después; ambos sirven el mismo Handler y BD (mismo proceso).
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 8161), sink.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        time.sleep(0.3)

    def test_ida_completa(self):
        con = sqlite3.connect(_tmp.name)
        con.execute("INSERT INTO telemetry_events (ts, user_id, event, props)"
                    " VALUES (?,?,?,?)", (int(time.time()), 1, "vista_diario", "{}"))
        con.commit()
        con.close()
        # 1) sin aprobar: enrola en pendiente, no envía, no avanza watermark
        st = telemetry.forward_once(force=True)
        self.assertTrue(st["configured"])
        self.assertEqual(st["enroll"], "pending")
        self.assertFalse(st["sent"])
        # 2) el coordinador aprueba (viaja el install_id real de este equipo)
        iid = telemetry.get_install_id()
        st2, body = identity(8161, iid, "approve")
        self.assertEqual(body.get("enroll"), "approved")
        # 3) reintento: envía firmado y avanza
        st = telemetry.forward_once(force=True)
        self.assertTrue(st["sent"])
        self.assertEqual(st["enroll"], "approved")
        con = sqlite3.connect(_sink.name)
        n = con.execute("SELECT COUNT(*) FROM sink_batches WHERE install_id = ?",
                        (iid,)).fetchone()[0]
        con.close()
        self.assertGreater(n, 0)


@unittest.skipUnless(HAVE_SSH, "sin ssh-keygen")
class EnrollRelayCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.init_db()
        sink.init_db()
        os.environ["NEVERRED_SINK_LOCAL"] = "http://127.0.0.1:8163"
        from http.server import ThreadingHTTPServer
        cls.sink_srv = ThreadingHTTPServer(("127.0.0.1", 8163), sink.Handler)
        threading.Thread(target=cls.sink_srv.serve_forever, daemon=True).start()
        cls.app_srv = ThreadingHTTPServer(("127.0.0.1", 8164), server.Handler)
        threading.Thread(target=cls.app_srv.serve_forever, daemon=True).start()
        time.sleep(0.3)

    def test_enroll_por_relay(self):
        kd = tempfile.mkdtemp(prefix="nr-keys4-")
        _, pub = fwd.ensure_keypair(kd)
        st, body = call(8164, "/api/telemetry-enroll",
                         {"install_id": "r" * 32, "pubkey": pub})
        self.assertEqual((st, body.get("enroll")), (200, "pending"))


@unittest.skipUnless(HAVE_SSH, "sin ssh-keygen")
class DashboardCSRFCase(unittest.TestCase):
    """Defensas del panel local: token CSRF, Origin/Referer y Host."""
    @classmethod
    def setUpClass(cls):
        server.init_db()
        sink.init_db()
        from http.server import ThreadingHTTPServer
        cls.port = 8167
        cls.srv = ThreadingHTTPServer(("127.0.0.1", cls.port), sink.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        time.sleep(0.3)

    def _post(self, path, body, headers=None):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path), method="POST",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers)

    def test_01_resumen_da_token(self):
        st, summ = call(self.port, "/api/sink-summary")
        self.assertEqual(st, 200)
        self.assertTrue(summ.get("csrf"))

    def test_02_token_valido_aprueba_y_identities(self):
        kd = tempfile.mkdtemp(prefix="nr-csrf-")
        _, pub = fwd.ensure_keypair(kd)
        iid = "c" * 32
        st, _ = call(self.port, "/api/telemetry-enroll",
                     {"install_id": iid, "pubkey": pub})
        self.assertEqual(st, 200)
        st, body = identity(self.port, iid, "approve")
        self.assertEqual((st, body.get("enroll")), (200, "approved"))
        # y con la misma página (domacen token distinto) se puede expulsar:
        st, body = identity(self.port, iid, "revoke")
        self.assertEqual((st, body.get("enroll")), (200, "rejected"))

    def test_03_sin_token_rechazado(self):
        st, _ = self._post("/api/sink-identity",
                           {"install_id": "z" * 32, "action": "approve"})
        self.assertEqual(st, 403)

    def test_04_token_falso_rechazado(self):
        st, body = self._post("/api/sink-identity",
                              {"install_id": "z" * 32, "action": "approve",
                               "csrf": "no-es-mi-token"})
        self.assertEqual(st, 403)

    def test_05_origin_ajeno_rechazado(self):
        st, _ = self._post("/api/sink-identity",
                           {"install_id": "z" * 32, "action": "approve"},
                           headers={"Origin": "http://evil.example",
                                    "Referer": "http://evil.example/"})
        self.assertEqual(st, 403)

    def test_06_rebinding_host_rechazado(self):
        # El DNS-rebinding pide por el dominio del atacante (Host ajeno):
        # ni el GET del dashboard ni el POST de acciones han de salir.
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/", headers={"Host": "pagina-del-atacante.example"})
        r = conn.getresponse(); r.read()
        self.assertEqual(r.status, 403)
        conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/sink-identity",
                     body=json.dumps({"install_id": "z" * 32,
                                      "action": "approve"}).encode(),
                     headers={"Host": "pagina-del-atacante.example",
                              "Content-Type": "application/json"})
        r = conn.getresponse(); j = json.loads(r.read().decode())
        self.assertEqual(r.status, 403)
        conn.close()
        self.assertIn("cruzada", j.get("error", ""))

    def test_07_host_loopback_aceptado(self):
        # Una identidad pendiente hace que el panel dibuje formularios.
        kd = tempfile.mkdtemp(prefix="nr-csrf2-")
        _, pub = fwd.ensure_keypair(kd)
        iid = "d" * 32
        st, _ = call(self.port, "/api/telemetry-enroll",
                     {"install_id": iid, "pubkey": pub})
        self.assertEqual(st, 200)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/", headers={"Host": "127.0.0.1:%d" % self.port})
        r = conn.getresponse(); texto = r.read().decode()
        self.assertEqual(r.status, 200)
        conn.close()
        self.assertIn("name=csrf", texto)   # los formularios llevan token
        self.assertEqual(r.headers.get("Referrer-Policy"), "no-referrer")
        self.assertIn("camera=()", r.headers.get("Permissions-Policy", ""))

    def test_08_origen_localhost_ok(self):
        # Formulario legítimo del panel: Origin del propio sink con puerto.
        st, summ = call(self.port, "/api/sink-summary")
        st, _ = self._post("/api/sink-identity",
                           {"install_id": "z" * 32, "action": "approve",
                            "csrf": summ["csrf"]},
                           headers={"Origin": "http://127.0.0.1:%d" % self.port})
        self.assertNotIn(st, (403,))  # 404 = instalación desconocida (token OK)
        self.assertEqual(st, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
