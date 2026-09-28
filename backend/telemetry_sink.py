#!/usr/bin/env python3
"""NeverRed — receptor de telemetría (el lado "forward").

Recibe SOLO agregados (instalación, día, evento, conteo) de las apps beta
con opt-in, con bearer token compartido. Upsert idempotente: los reintentos
con backoff no duplican nada.

Uso en tu Mac (dentro de tu red Tailscale para la prueba con dos equipos):
    NEVERRED_SINK_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
    NEVERRED_SINK_TOKEN=$NEVERRED_SINK_TOKEN NEVERRED_SINK_DB=~/telemetry-sink.db \
        python3 backend/telemetry_sink.py --host 0.0.0.0 --port 8140

En cada beta, configura:
    NEVERRED_TELEMETRY_SINK=http://<tu-ip-tailscale>:8140
    NEVERRED_TELEMETRY_TOKEN=<el mismo token>

Panel: http://localhost:8140/ (conteos por instalación, día y evento).
"""
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from server import TELEMETRY_EVENTS  # noqa: E402 — mismo allowlist que el store

TOKEN = os.environ.get("NEVERRED_SINK_TOKEN", "")
DB_PATH = os.environ.get("NEVERRED_SINK_DB",
                         os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "telemetry-sink.db"))


def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    os.umask(0o077)
    con = db()
    con.execute(
        """CREATE TABLE IF NOT EXISTS sink_batches (
               install_id TEXT NOT NULL,
               day TEXT NOT NULL,
               event TEXT NOT NULL,
               count INTEGER NOT NULL,
               version TEXT NOT NULL DEFAULT '',
               updated_at INTEGER NOT NULL,
               PRIMARY KEY (install_id, day, event))""")
    con.commit()
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass
    con.close()


def valid_payload(body):
    """None = válido; str = error."""
    if not isinstance(body, dict):
        return "Cuerpo JSON inválido."
    iid = body.get("install_id")
    if not isinstance(iid, str) or not 8 <= len(iid) <= 128:
        return "install_id inválido."
    aggs = body.get("aggregates")
    if not isinstance(aggs, list) or not aggs or len(aggs) > 500:
        return "Lote inválido."
    import re
    for a in aggs:
        if not isinstance(a, dict):
            return "Agregado inválido."
        if a.get("event") not in TELEMETRY_EVENTS:
            return "Evento no catalogado: %r." % (a.get("event"),)
        if not isinstance(a.get("date"), str) or \
                not re.match(r"^\d{4}-\d{2}-\d{2}$", a["date"]):
            return "Fecha inválida."
        if not isinstance(a.get("count"), int) or isinstance(a.get("count"), bool) \
                or not 0 <= a["count"] <= 1_000_000:
            return "Conteo inválido."
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "NeverRedSink/1"

    def log_message(self, fmt, *args):
        print("[sink] " + fmt % args)

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        auth = self.headers.get("Authorization") or ""
        if not auth.startswith("Bearer "):
            return False
        return hmac.compare_digest(auth[len("Bearer "):].strip(), TOKEN)

    def do_POST(self):
        if urlparse(self.path).path != "/api/telemetry-ingest":
            return self._json(404, {"error": "Ruta no encontrada."})
        if not TOKEN or not self._authorized():
            return self._json(401, {"error": "No autorizado."})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 1_000_000:
            return self._json(400, {"error": "Cuerpo inválido."})
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json(400, {"error": "Cuerpo JSON inválido."})
        err = valid_payload(body)
        if err:
            return self._json(400, {"error": err})
        now = int(time.time())
        version = str(body.get("version") or "")[:16]
        con = db()
        for a in body["aggregates"]:
            con.execute(
                "INSERT INTO sink_batches (install_id, day, event, count, version,"
                " updated_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(install_id, day, event) DO UPDATE SET count=excluded.count,"
                " version=excluded.version, updated_at=excluded.updated_at",
                (body["install_id"], a["date"], a["event"], a["count"], version, now))
        con.commit()
        con.close()
        self._json(200, {"ok": True, "stored": len(body["aggregates"])})

    def do_GET(self):
        path = urlparse(self.path).path
        con = db()
        if path == "/api/sink-summary":
            rows = con.execute(
                "SELECT install_id, day, event, count, version FROM sink_batches "
                "ORDER BY day DESC, install_id, event").fetchall()
            con.close()
            return self._json(200, {"batches": [dict(r) for r in rows]})
        if path != "/":
            con.close()
            return self._json(404, {"error": "Ruta no encontrada."})
        rows = con.execute(
            "SELECT substr(install_id, 1, 8) AS inst, day, event, SUM(count) AS n,"
            " COUNT(DISTINCT install_id) AS betas FROM sink_batches "
            "GROUP BY inst, day, event ORDER BY day DESC, n DESC").fetchall()
        con.close()
        cells = "".join(
            "<tr><td>…%s</td><td>%s</td><td>%s</td><td>%d</td><td>%d</td></tr>"
            % (r["inst"], r["day"], r["event"], r["n"], r["betas"]) for r in rows)
        html = ("<!doctype html><html lang=es><head><meta charset=utf-8>"
                "<title>NeverRed · Telemetría beta</title></head><body>"
                "<h1>Telemetría beta (solo agregados)</h1>"
                "<table border=1><tr><th>Instalación</th><th>Día</th><th>Evento</th>"
                "<th>Conteo</th><th>Betas</th></tr>%s</table></body></html>"
                % (cells or "<tr><td colspan=5>Sin datos todavía.</td></tr>"))
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def main():
    import argparse
    if not TOKEN:
        raise SystemExit("Define NEVERRED_SINK_TOKEN (bearer compartido con las betas).")
    ap = argparse.ArgumentParser(description="Receptor de telemetría NeverRed.")
    ap.add_argument("--host", default=os.environ.get("SINK_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("SINK_PORT", "8140")))
    args = ap.parse_args()
    init_db()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("Sink en http://%s:%d  (BD: %s)" % (args.host, args.port, DB_PATH))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nApagando sink.")


if __name__ == "__main__":
    main()
