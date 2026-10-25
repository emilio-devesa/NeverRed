#!/usr/bin/env python3
"""NeverRed — relay de telemetría independiente (punto de entrada del funnel).

Recibe SOLO `POST /api/telemetry-ingest` y `POST /api/telemetry-enroll` y
los reenvía tal cual al monitor local (`telemetry_sink.py`), conservando
la Authorization para que el monitor valide bearer o firma Ed25519.
Nada más se expone: el dashboard y `/api/sink-identity` (aprobar/vetar)
quedan en el monitor, que solo escucha en loopback.

Se usa cuando el funnel público de Tailscale sirve telemetría sin tener
la app de NeverRed abierta (el relay de `server.py` vive dentro de la
app). Mismo contrato de entrada que el relay de la app: cuerpo ≤ 1 MB
JSON válido, rate-limit por IP (falla cerrado) y respuesta passthrough.

Uso:
    python3 backend/telemetry_relay.py --host 127.0.0.1 --port 8082
    #  --sink http://127.0.0.1:8140  (por defecto; entradas de config/ docs)

Rate-limit: NEVERRED_RELAY_RATE_MAX (defecto 60) intentos por
NEVERRED_RELAY_RATE_WINDOW (defecto 600 s) por IP cliente (X-Forwarded-For
del funnel, con la IP directa como reserva).
"""
import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import telemetry_forward as _fwd

RATE_MAX = int(os.environ.get("NEVERRED_RELAY_RATE_MAX", "60"))
RATE_WINDOW = int(os.environ.get("NEVERRED_RELAY_RATE_WINDOW", "600"))
RATE_MAX_KEYS = int(os.environ.get("NEVERRED_RELAY_RATE_KEYS", "10000"))
RATE_MAX_BODY = 1_000_000
# Prefijo del funnel (p. ej. "/telemetry" si se sirve bajo ese camino del
# proxy de Tailscale): se elimina del camino antes de enrutar.
PREFIX = os.environ.get("NEVERRED_RELAY_PREFIX", "").rstrip("/")

_rate = {}          # ip -> [intentos, fin_ventana_ts]
_rate_lock = threading.Lock()
_rate_ops = 0


def _prune(now):
    global _rate_ops
    if len(_rate) < 128:
        return
    for k in [k for k, v in _rate.items() if v[1] <= now]:
        _rate.pop(k, None)


def rate_limited(ip):
    """Ventana deslizante por IP; tabla llena = fallo cerrado."""
    global _rate_ops
    now = int(time.time())
    with _rate_lock:
        _rate_ops += 1
        if _rate_ops >= 256:
            _rate_ops = 0
            _prune(now)
        count, reset = _rate.get(ip, (0, now + RATE_WINDOW))
        if now > reset:
            count, reset = 0, now + RATE_WINDOW
        if ip not in _rate and len(_rate) >= RATE_MAX_KEYS:
            return True
        count += 1
        _rate[ip] = [count, reset]
        return count > RATE_MAX


def client_ip(args):
    """IP de origen: X-Forwarded-For del funnel si viene (la primera),
    si no la IP remota. El endpoint es público a través del funnel y este
    header lo da el proxy de Tailscale; sin funnel es la IP directa."""
    xff = (args.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return xff or args.client_address[0]


class RelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "NeverRedRelay"
    sys_version = ""

    # ---------- infra ----------
    def _cors(self):
        # Paridad con la app: solo Origin null (modo archivo local de las
        # pestañas de otros equipos); dominios web no obtienen cabeceras.
        if (self.headers.get("Origin") or "") == "null":
            self.send_header("Access-Control-Allow-Origin", "null")
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers",
                             "Content-Type, Authorization")

    def _send(self, code, resp, headers=()):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(resp)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in headers:
            self.send_header(k, v)
        self._cors()
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, fmt, *args):  # silencio: logs vía_Threaded stdout
        pass

    def log(self, code, path, why=""):
        ts = time.strftime("%H:%M:%S")
        ip = client_ip(self)
        print("[%s] %s %s %s %s" % (ts, ip, code, path, why), flush=True)

    def _is_health(self, path):
        return path in ("/", "/health")

    def _route(self, path):
        """('health'|'ingest'|'enroll'|None) con el prefijo del funnel eliminado."""
        p = path
        if PREFIX and (p == PREFIX or p.startswith(PREFIX + "/")):
            p = p[len(PREFIX):] or "/"
        if self._is_health(p):
            return "health"
        if p in ("/api/telemetry-ingest", "/api/telemetry-enroll"):
            return "ingest" if p.endswith("ingest") else "enroll"
        return None

    def do_GET(self):
        if self._route(urlparse(self.path).path) == "health":
            return self._send(200, b'{"ok": true, "service": "telemetry-relay"}')
        return self._send(404, b'{"error": "Ruta no encontrada."}')

    def do_POST(self):
        path = urlparse(self.path).path
        route = self._route(path)
        if route == "health":
            return self._send(405, b'{"error": "Metodo no permitido."}')
        if route is None:
            # Nada más sale por el funnel: el monitor queda interno.
            self.log(404, path, "ruta no retransmitida")
            return self._send(404, b'{"error": "Ruta no encontrada."}')
        if rate_limited(client_ip(self)):
            self.log(429, path, "rate-limit")
            return self._send(429,
                              b'{"error": "Demasiados intentos. Espera unos minutos."}')
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > RATE_MAX_BODY:
            return self._send(400, b'{"error": "Cuerpo invalido."}')
        try:
            raw = self.rfile.read(length)
        except Exception:
            return self._send(400, b'{"error": "Cuerpo invalido."}')
        try:
            json.loads(raw.decode("utf-8"))
        except Exception:
            return self._send(400, b'{"error": "Cuerpo JSON invalido."}')
        code, resp = _fwd.relay_ingest(raw, self.headers.get("Authorization") or "",
                                       self.server.sink_local, route)
        if code is None or code == 502:
            self.log(502, path, "monitor no disponible; se reintentara")
            return self._send(502,
                              b'{"error": "Receptor no disponible; se reintentara."}')
        self.log(code, path)
        self._send(code, resp)


def _method_not_allowed(self):
    return self._send(405, b'{"error": "Metodo no permitido."}')


for _m in ("do_PUT", "do_DELETE", "do_PATCH", "do_HEAD", "do_OPTIONS"):
    setattr(RelayHandler, _m, _method_not_allowed)


class RelayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, sink_local):
        super().__init__(addr, handler)
        self.sink_local = sink_local


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8082)
    ap.add_argument("--sink", default="http://127.0.0.1:8140",
                    help="URL del monitor local (telemetry_sink.py)")
    args = ap.parse_args()
    srv = RelayServer((args.host, args.port), RelayHandler,
                      args.sink.rstrip("/"))
    print("[relay] telemetria: %s -> %s (rate %s/%ss por IP)"
          % ("http://%s:%s" % (args.host, args.port), args.sink.rstrip("/"),
             RATE_MAX, RATE_WINDOW), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
