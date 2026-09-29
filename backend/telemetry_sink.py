#!/usr/bin/env python3
"""NeverRed — receptor de telemetría (el lado "forward").

Recibe SOLO agregados (instalación, día, hora, evento, conteo, versión,
plataforma) de las apps beta con opt-in, con bearer token compartido.
Upsert idempotente: los reintentos con backoff no duplican nada.

Uso en tu Mac (emparejamiento; el bearer legacy es opcional):
    python3 backend/telemetry_sink.py --host 127.0.0.1 --port 8140
    # + NEVERRED_SINK_TOKEN=<secreto> si aún usas betas con token.

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
import shutil
import sqlite3
import subprocess
import sys
import tempfile
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
    con.execute(
        """CREATE TABLE IF NOT EXISTS sink_identities (
               install_id TEXT PRIMARY KEY,
               pubkey TEXT NOT NULL DEFAULT '',
               status TEXT NOT NULL DEFAULT 'pending',
               first_seen INTEGER NOT NULL DEFAULT 0,
               updated_at INTEGER NOT NULL DEFAULT 0)""")
    con.commit()
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass
    con.close()


# valid_payload vive en telemetry_common (contrato App/Monitor).


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _norm_platform(platform):
    return platform if platform in PLATFORMS else "desconocida"


def verify_envelope(pubkey, install_id, canonical, signature):
    """True si la firma Ed25519 verifica (ssh-keygen -Y)."""
    if not shutil.which("ssh-keygen"):
        return False
    tmpd = tempfile.mkdtemp(prefix="nr-verify-")
    try:
        sigpath = os.path.join(tmpd, "s.sig")
        allowpath = os.path.join(tmpd, "allow")
        with open(sigpath, "w", encoding="utf-8") as f:
            f.write(signature if signature.endswith("\n") else signature + "\n")
        with open(allowpath, "w", encoding="utf-8") as f:
            f.write("%s %s\n" % (install_id, pubkey.strip()))
        r = subprocess.run(
            ["ssh-keygen", "-Y", "verify", "-f", allowpath, "-I", install_id,
             "-n", _tc.SIGN_NAMESPACE, "-s", sigpath],
            input=canonical, capture_output=True, timeout=20)
        return r.returncode == 0
    except Exception:
        return False
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def _store_aggregates(con, body, now):
    """Guarda un lote validado. Devuelve nº de grupos."""
    version = str(body.get("version") or "")[:16]
    platform = _norm_platform(body.get("platform") or "")
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
    return len(body["aggregates"])


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
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 1_000_000:
            return self._json(400, {"error": "Cuerpo inválido."})
        ctype = self.headers.get("Content-Type") or ""
        try:
            raw = self.rfile.read(length).decode("utf-8")
        except Exception:
            return self._json(400, {"error": "Cuerpo inválido."})
        if "urlencoded" in ctype:
            from urllib.parse import parse_qsl
            try:
                body = dict(parse_qsl(raw, keep_blank_values=True))
            except Exception:
                return self._json(400, {"error": "Cuerpo inválido."})
        else:
            try:
                body = json.loads(raw)
            except Exception:
                return self._json(400, {"error": "Cuerpo JSON inválido."})
        if path == "/api/telemetry-ingest":
            return self._ingest(body)
        if path == "/api/telemetry-enroll":
            return self._enroll(body)
        if path == "/api/sink-identity":
            return self._identity_action(body, "urlencoded" in ctype)
        return self._json(404, {"error": "Ruta no encontrada."})

    def _ingest(self, body):
        if not isinstance(body, dict):
            return self._json(400, {"error": "Cuerpo inválido."})
        if isinstance(body.get("signature"), str):
            err = _tc.validate_payload(body)
            if err:
                return self._json(400, {"error": err})
            return self._ingest_signed(body)
        if not TOKEN or not self._authorized():
            return self._json(401, {"error": "No autorizado."})
        err = _tc.validate_payload(body)
        if err:
            return self._json(400, {"error": err})
        con = db()
        stored = _store_aggregates(con, body, int(time.time()))
        con.close()
        return self._json(200, {"ok": True, "stored": stored})

    def _ingest_signed(self, body):
        iid = body["install_id"]
        con = db()
        row = con.execute("SELECT pubkey, status FROM sink_identities"
                          " WHERE install_id = ?", (iid,)).fetchone()
        if row is None:
            con.close()
            return self._json(403, {"enroll": "required"})
        if row["status"] != "approved":
            con.close()
            return self._json(403, {"enroll": row["status"]})
        stats = body.get("client_stats", {})
        canonical = _tc.canonical_envelope(iid, body.get("version"),
                                           body.get("platform"), stats,
                                           body["aggregates"])
        if not verify_envelope(row["pubkey"], iid, canonical, body["signature"]):
            con.close()
            return self._json(403, {"error": "Firma inválida."})
        stored = _store_aggregates(con, body, int(time.time()))
        con.close()
        return self._json(200, {"ok": True, "stored": stored})

    def _enroll(self, body):
        if not isinstance(body, dict):
            return self._json(400, {"error": "Cuerpo inválido."})
        err = _tc.validate_enroll(body)
        if err:
            return self._json(400, {"error": err})
        iid, pubkey = body["install_id"], body["pubkey"].strip()
        now = int(time.time())
        con = db()
        row = con.execute("SELECT pubkey, status FROM sink_identities"
                          " WHERE install_id = ?", (iid,)).fetchone()
        if row is None:
            con.execute("INSERT INTO sink_identities (install_id, pubkey, status,"
                        " first_seen, updated_at) VALUES (?,?,?,?,?)",
                        (iid, pubkey, "pending", now, now))
            status = "pending"
        elif row["status"] == "approved" and row["pubkey"] == pubkey:
            status = "approved"
        else:
            if row["pubkey"] != pubkey:
                # Clave cambiada: re-enrolar en pendiente (lo aprueba el coordinador).
                con.execute("UPDATE sink_identities SET pubkey = ?, status = 'pending',"
                            " updated_at = ? WHERE install_id = ?",
                            (pubkey, now, iid))
                status = "pending"
            else:
                status = row["status"]
        con.commit()
        con.close()
        return self._json(200, {"enroll": status})

    def _identity_action(self, body, from_form=False):
        # Solo localhost: el dashboard no tiene auth y el sink no se expone.
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return self._json(403, {"error": "Solo local."})
        if not isinstance(body, dict):
            return self._json(400, {"error": "Cuerpo inválido."})
        iid = str(body.get("install_id") or "")
        action = str(body.get("action") or "")
        new = {"approve": "approved", "reject": "rejected",
               "revoke": "rejected"}.get(action)
        if not iid or not new:
            return self._json(400, {"error": "Acción inválida."})
        con = db()
        cur = con.execute("UPDATE sink_identities SET status = ?, updated_at = ?"
                          " WHERE install_id = ?", (new, int(time.time()), iid))
        con.commit()
        con.close()
        if cur.rowcount == 0:
            return self._json(404, {"error": "Instalación desconocida."})
        if from_form:
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
            return None
        return self._json(200, {"enroll": new})

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
        identities = con.execute("SELECT install_id, pubkey, status, updated_at"
                                 " FROM sink_identities ORDER BY updated_at DESC").fetchall()
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

        def ident_section():
            if not identities:
                return ""
            rows = []
            for r in identities:
                iid, status = r["install_id"], r["status"]
                key = (r["pubkey"] or "").split()
                fp = key[1][-12:] if len(key) > 1 else "?"
                seen = time.strftime("%d/%m %H:%M", time.localtime(r["updated_at"] or 0))
                if status == "pending":
                    acts = (("<form method=post action='/api/sink-identity'>"
                             "<input type=hidden name=install_id value='%s'>"
                             "<input type=hidden name=action value='approve'>"
                             "<button>Aprobar</button></form>"
                             "<form method=post action='/api/sink-identity'>"
                             "<input type=hidden name=install_id value='%s'>"
                             "<input type=hidden name=action value='reject'>"
                             "<button>Rechazar</button></form>") % (_esc(iid), _esc(iid)))
                elif status == "approved":
                    acts = (("<form method=post action='/api/sink-identity'>"
                             "<input type=hidden name=install_id value='%s'>"
                             "<input type=hidden name=action value='revoke'>"
                             "<button>Expulsar</button></form>") % _esc(iid))
                else:
                    acts = "<span class=muted>rechazada</span>"
                rows.append("<div class=row><span class=name>%s… · %s</span>"
                            "<span class=num>%s</span><span>%s</span></div>"
                            % (_esc(iid[:12]), _esc(status), _esc(fp + " · " + seen), acts))
            return ("<section><h2>Instalaciones (%d)</h2><div class=ranking>%s</div>"
                    "<p class=muted>Solo las aprobadas cuentan en las estadísticas. "
                    "Lo pendiente se queda en la app emisora sin perderse.</p></section>"
                    % (len(identities), "".join(rows)))

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
form{display:inline}button{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:.3em .8em;cursor:pointer;font-size:.85em}button:hover{border-color:var(--accent)}
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
%s%s%s%s
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
            section("Plataformas", [(r[0], r[1]) for r in by_platform]),
            ident_section()))
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
        print("[sink] Sin NEVERRED_SINK_TOKEN: solo ingesta firmada (emparejamiento).")
    ap = argparse.ArgumentParser(description="Receptor de telemetría NeverRed.")
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
