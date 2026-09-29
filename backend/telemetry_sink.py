#!/usr/bin/env python3
"""NeverRed — receptor de telemetría (el lado "forward").

Recibe SOLO agregados (instalación, día, hora, evento, conteo, versión,
plataforma) de las apps beta con opt-in, con bearer token compartido.
Upsert idempotente: los reintentos con backoff no duplican nada.

Uso en tu Mac:
    NEVERRED_SINK_TOKEN=<secreto> NEVERRED_SINK_DB=~/telemetry-sink.db \
        python3 backend/telemetry_sink.py --host 127.0.0.1 --port 8140

En cada beta, configura:
    NEVERRED_TELEMETRY_SINK=https://<tu-url> NEVERRED_TELEMETRY_TOKEN=<secreto>

Panel: http://localhost:8140/ (totales, picos, tartas y medias de reintentos).
"""
import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import telemetry_common as _tc  # noqa: E402 — contrato App/Monitor
TELEMETRY_EVENTS = _tc.TELEMETRY_EVENTS

TOKEN = os.environ.get("NEVERRED_SINK_TOKEN", "")
DB_PATH = os.environ.get("NEVERRED_SINK_DB",
                         os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "telemetry-sink.db"))
PLATFORMS = ("macOS", "Linux", "Windows", "desconocida")


def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    os.umask(0o077)
    con = db()
    cols = [r[1] for r in con.execute("PRAGMA table_info(sink_batches)")]
    if not cols:
        con.execute(
            """CREATE TABLE sink_batches (
                   install_id TEXT NOT NULL,
                   day TEXT NOT NULL,
                   hour TEXT NOT NULL DEFAULT '00',
                   event TEXT NOT NULL,
                   count INTEGER NOT NULL,
                   version TEXT NOT NULL DEFAULT '',
                   platform TEXT NOT NULL DEFAULT 'desconocida',
                   updated_at INTEGER NOT NULL,
                   PRIMARY KEY (install_id, day, hour, event))""")
    else:
        # Migración v2: grano hora + plataforma (datos viejos: hora 00).
        for col, ddl in (("hour", "TEXT NOT NULL DEFAULT '00'"),
                         ("platform", "TEXT NOT NULL DEFAULT 'desconocida'")):
            if col not in cols:
                con.execute("ALTER TABLE sink_batches ADD COLUMN %s %s" % (col, ddl))
        con.execute(
            """CREATE TABLE IF NOT EXISTS sink_batches_new (
                   install_id TEXT NOT NULL,
                   day TEXT NOT NULL,
                   hour TEXT NOT NULL DEFAULT '00',
                   event TEXT NOT NULL,
                   count INTEGER NOT NULL,
                   version TEXT NOT NULL DEFAULT '',
                   platform TEXT NOT NULL DEFAULT 'desconocida',
                   updated_at INTEGER NOT NULL,
                   PRIMARY KEY (install_id, day, hour, event))""")
        con.execute(
            "INSERT OR IGNORE INTO sink_batches_new SELECT install_id, day, '00',"
            " event, count, version, platform, updated_at FROM sink_batches")
        con.execute("DROP TABLE sink_batches")
        con.execute("ALTER TABLE sink_batches_new RENAME TO sink_batches")
    con.execute(
        """CREATE TABLE IF NOT EXISTS sink_stats (
               install_id TEXT PRIMARY KEY,
               total_sends INTEGER NOT NULL DEFAULT 0,
               total_fails INTEGER NOT NULL DEFAULT 0,
               version TEXT NOT NULL DEFAULT '',
               updated_at INTEGER NOT NULL)""")
    con.commit()
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass
    con.close()


# valid_payload vive en telemetry_common (contrato App/Monitor).


PALETTE = ["#4f9cf9", "#22c55e", "#f59e0b", "#ef4444", "#a78bfa", "#14b8a6",
           "#f472b6", "#84cc16", "#f97316", "#06b6d4", "#e879f9", "#94a3b8"]


def pie_svg(pairs, size=180):
    """Tarta SVG sin dependencias: [(etiqueta, valor)]."""
    total = sum(v for _, v in pairs) or 1
    cx = cy = r = size // 2
    out = ["<svg width=%d height=%d viewBox='0 0 %d %d'>" % (size, size, size, size)]
    angle = -90.0
    for i, (label, v) in enumerate(pairs):
        frac = v / total
        a0, a1 = math.radians(angle), math.radians(angle + frac * 360)
        x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
        x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
        large = 1 if frac > 0.5 else 0
        color = PALETTE[i % len(PALETTE)]
        if frac >= 1:
            out.append("<circle cx=%d cy=%d r=%d fill='%s'><title>%s: %d (%.1f%%)</title></circle>"
                       % (cx, cy, r, color, label, v, frac * 100))
        elif frac > 0:
            out.append("<path d='M%d,%d L%.1f,%.1f A%d,%d 0 %d 1 %.1f,%.1f Z' fill='%s'>"
                       "<title>%s: %d (%.1f%%)</title></path>"
                       % (cx, cy, x0, y0, r, r, large, x1, y1, color, label, v, frac * 100))
        angle += frac * 360
    out.append("</svg>")
    legend = "".join(
        "<li><span style='display:inline-block;width:12px;height:12px;background:%s'></span> "
        "%s: <strong>%d</strong> (%.1f%%)</li>"
        % (PALETTE[i % len(PALETTE)], lab, v, 100 * v / total)
        for i, (lab, v) in enumerate(pairs))
    return "\n".join(out) + "<ul>%s</ul>" % legend


class Handler(BaseHTTPRequestHandler):
    server_version = "NeverRedSink/2"

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
        err = _tc.validate_payload(body)
        if err:
            return self._json(400, {"error": err})
        now = int(time.time())
        version = str(body.get("version") or "")[:16]
        platform = str(body.get("platform") or "")
        platform = platform if platform in PLATFORMS else "desconocida"
        con = db()
        for a in body["aggregates"]:
            con.execute(
                "INSERT INTO sink_batches (install_id, day, hour, event, count,"
                " version, platform, updated_at) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(install_id, day, hour, event) DO UPDATE SET"
                " count=excluded.count, version=excluded.version,"
                " platform=excluded.platform, updated_at=excluded.updated_at",
                (body["install_id"], a["date"], a["hour"], a["event"], a["count"],
                 version, platform, now))
        stats = body.get("client_stats", {})
        con.execute(
            "INSERT INTO sink_stats (install_id, total_sends, total_fails, version,"
            " updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(install_id) DO UPDATE SET"
            " total_sends=excluded.total_sends, total_fails=excluded.total_fails,"
            " version=excluded.version, updated_at=excluded.updated_at",
            (body["install_id"], stats.get("total_sends", 0),
             stats.get("total_fails", 0), version, now))
        con.commit()
        con.close()
        self._json(200, {"ok": True, "stored": len(body["aggregates"])})

    def _dashboard(self):
        con = db()
        users = con.execute("SELECT COUNT(DISTINCT install_id) FROM sink_batches").fetchone()[0]
        peak_day = con.execute(
            "SELECT day, COUNT(DISTINCT install_id) FROM sink_batches "
            "GROUP BY day ORDER BY 2 DESC LIMIT 1").fetchone()
        peak_hour = con.execute(
            "SELECT day, hour, COUNT(DISTINCT install_id) FROM sink_batches "
            "GROUP BY day, hour ORDER BY 3 DESC LIMIT 1").fetchone()
        by_event = con.execute(
            "SELECT event, SUM(count) FROM sink_batches GROUP BY event ORDER BY 2 DESC").fetchall()
        by_version = con.execute(
            "SELECT version, SUM(count) FROM sink_batches GROUP BY version ORDER BY 2 DESC").fetchall()
        by_platform = con.execute(
            "SELECT platform, SUM(count) FROM sink_batches GROUP BY platform ORDER BY 2 DESC").fetchall()
        stats = con.execute("SELECT total_sends, total_fails FROM sink_stats").fetchall()
        con.close()
        sends = sum(r[0] for r in stats)
        fails = sum(r[1] for r in stats)
        mean = (fails / sends) if sends else 0
        now_s = time.strftime("%d/%m/%Y %H:%M")

        def section(title, pairs):
            if not pairs:
                return "<section><h2>%s</h2><p class=muted>Sin datos todavía.</p></section>" % title
            top = pairs[0][1] or 1
            rows = "".join(
                "<div class=row><span class=name>%s</span>"
                "<span class=bar><i style='width:%.1f%%;background:%s'></i></span>"
                "<span class=num>%d</span><span class=pct muted>%.1f%%</span></div>"
                % (lab, 100 * v / top, PALETTE[i % len(PALETTE)], v,
                   100 * v / (sum(v for _, v in pairs) or 1))
                for i, (lab, v) in enumerate(pairs))
            return ("<section><h2>%s</h2><div class=cols><div>%s</div>"
                    "<div class=ranking>%s</div></div></section>"
                    % (title, pie_svg(pairs, 200), rows))

        html = ("""<!doctype html><html lang=es><head><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1'>
<title>NeverRed · Telemetría beta</title>
<style>
:root{--bg:#0e1116;--panel:#171c24;--panel2:#1f2631;--text:#eef2f7;
--muted:#9aa5b4;--line:#2a3340;--green:#22c55e;--red:#ef4444;--accent:#38bdf8;--radius:14px}
*{box-sizing:border-box}body{background:var(--bg);color:var(--text);
font-family:system-ui,-apple-system,sans-serif;max-width:960px;margin:auto;padding:1.5em}
header{display:flex;align-items:baseline;gap:1em;flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:1em;margin-bottom:1.5em}
header h1{margin:0;font-size:1.4em}header p{margin:0}
.muted{color:var(--muted)}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:1em}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:1em}
.card h3{margin:0 0 .2em;font-size:1.8em;color:var(--accent)}
.card.warn h3{color:var(--red)}.card.ok h3{color:var(--green)}
.card p{margin:0;color:var(--muted);font-size:.85em}
section{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:1.2em;margin-top:1.2em}
section h2{margin:0 0 1em;font-size:1.05em}
.cols{display:flex;gap:2em;flex-wrap:wrap;align-items:flex-start}
.cols ul{list-style:none;padding:0;margin:.6em 0;font-size:.85em}
.cols li{margin:.25em 0;color:var(--muted)}.cols li strong{color:var(--text)}
.ranking{flex:1;min-width:260px}
.row{display:grid;grid-template-columns:170px 1fr 52px 56px;gap:.6em;align-items:center;padding:.28em 0;font-size:.88em}
.row .name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row .num{text-align:right;font-variant-numeric:tabular-nums}
.row .pct{text-align:right;color:var(--muted)}
.bar{display:block;height:8px;background:var(--panel2);border-radius:4px;overflow:hidden}
.bar i{display:block;height:100%%;border-radius:4px}
footer{margin-top:2em;color:var(--muted);font-size:.8em}
</style></head><body>
<header><h1>📊 Telemetría beta</h1>
<p class=muted>Solo agregados · actualizado %s</p></header>
<div class=cards>
<div class=card><h3>%d</h3><p>instalaciones totales</p></div>
<div class=card><h3>%d</h3><p>pico en un día%s</p></div>
<div class=card><h3>%d</h3><p>pico en una hora%s</p></div>
<div class=card><h3>%.2f</h3><p>fallos medios por envío · %d envíos, %d fallos</p></div>
</div>
%s%s%s
<footer>NeverRed · la telemetría beta solo contiene conteos por (instalación, día, hora, evento). Sin importes, textos ni identificadores.</footer>
</body></html>""" % (
            now_s, users,
            peak_day[1] if peak_day else 0,
            " · %s" % peak_day[0] if peak_day else "",
            peak_hour[2] if peak_hour else 0,
            " · %s %sh" % (peak_hour[0], peak_hour[1]) if peak_hour else "",
            mean, sends, fails,
            section("Funciones más empleadas", [(r[0], r[1]) for r in by_event]),
            section("Versiones", [(r[0] or "?", r[1]) for r in by_version]),
            section("Plataformas", [(r[0], r[1]) for r in by_platform])))
        return html.encode("utf-8")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/sink-summary":
            con = db()
            rows = con.execute(
                "SELECT install_id, day, hour, event, count, version, platform"
                " FROM sink_batches ORDER BY day DESC, hour DESC, install_id, event").fetchall()
            stats = con.execute("SELECT * FROM sink_stats").fetchall()
            con.close()
            return self._json(200, {"batches": [dict(r) for r in rows],
                                    "stats": [dict(r) for r in stats]})
        if path != "/":
            return self._json(404, {"error": "Ruta no encontrada."})
        body = self._dashboard()
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
