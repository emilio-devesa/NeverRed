#!/usr/bin/env python3
"""NeverRed — forward store-and-forward + relay (lado infraestructura).

Mueve los agregados de telemetría desde la BD de la App hasta el Monitor,
con reintentos exponenciales y upsert idempotente en destino.

NO importa `server`: todas las dependencias de la App (conexión a BD, ID
de instalación, versión) se inyectan como parámetros. `server.py` lo usa
a través de envoltorios finos que mantienen la API histórica
(`forward_state`, `forward_once`, `backoff_delay`, `build_aggregates`).
"""

import json
import time
import urllib.error
import urllib.request


class ForwardConfig:
    """Config del destino. `sink`/`token` vacíos = modo local puro."""

    def __init__(self, sink="", token="", platform="", every=900,
                 max_delay=3600, sink_local="http://127.0.0.1:8140"):
        self.sink = (sink or "").rstrip("/")
        self.token = token or ""
        self.platform = platform or ""
        self.every = every
        self.max_delay = max_delay
        self.sink_local = (sink_local or "http://127.0.0.1:8140").rstrip("/")


def backoff_delay(fails, max_delay=3600):
    return min(max_delay, 60 * (2 ** max(0, fails)))


def build_aggregates(con, since_ts):
    """Agregados pendientes: [{event, date, hour, count}]. Solo conteos."""
    rows = con.execute(
        "SELECT event, date(ts, 'unixepoch'), strftime('%H', ts, 'unixepoch'), COUNT(*)"
        " FROM telemetry_events WHERE ts > ?"
        " GROUP BY event, date(ts, 'unixepoch'), strftime('%H', ts, 'unixepoch')"
        " ORDER BY 2, 3, 1",
        (since_ts,)).fetchall()
    return [{"event": e, "date": d, "hour": h, "count": c} for e, d, h, c in rows]


def forward_state(con):
    con.execute("INSERT OR IGNORE INTO telemetry_forward (id) VALUES (1)")
    row = con.execute("SELECT * FROM telemetry_forward WHERE id = 1").fetchone()
    con.commit()
    return row


def forward_once(con, install_id, version, cfg, force=False):
    """Intenta un envío al receptor. Devuelve dict de estado (siempre)."""
    st = forward_state(con)
    now = int(time.time())
    status = {"configured": bool(cfg.sink and cfg.token), "pending": 0, "sent": False,
              "last_ok": st["last_ok"], "fails": st["fails"],
              "next_retry_in": max(0, st["next_retry"] - now)}
    if not cfg.sink or not cfg.token:
        return status
    if not force and now < st["next_retry"]:
        return status
    aggs = build_aggregates(con, st["watermark"])
    status["pending"] = len(aggs)
    if not aggs:
        status["sent"] = True
        return status
    # El intento cuenta antes de enviar: lo que viaja ya incluye este envío.
    con.execute("UPDATE telemetry_forward SET total_sends = total_sends + 1 WHERE id = 1")
    con.commit()
    body = json.dumps({"install_id": install_id, "version": version,
                       "platform": cfg.platform,
                       "client_stats": {"total_sends": st["total_sends"] + 1,
                                        "total_fails": st["total_fails"]},
                       "aggregates": aggs}).encode()
    req = urllib.request.Request(
        cfg.sink + "/api/telemetry-ingest", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + cfg.token})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            ok = r.status == 200
    except Exception as e:
        print("[neverred] forward fallido (%s); reintento con backoff." % e)
        ok = False
    if ok:
        con.execute("UPDATE telemetry_forward SET watermark = ?, last_ok = ?, "
                    "fails = 0, next_retry = ? WHERE id = 1",
                    (now, now, now + cfg.every))
        status.update(pending=0, sent=True, last_ok=now, fails=0,
                      next_retry_in=cfg.every)
    else:
        fails = st["fails"] + 1
        delay = backoff_delay(fails, cfg.max_delay)
        con.execute("UPDATE telemetry_forward SET fails = ?, next_retry = ?, "
                    "total_fails = total_fails + 1 WHERE id = 1",
                    (fails, now + delay))
        status.update(fails=fails, next_retry_in=delay)
    con.commit()
    return status


def forward_loop(db_fn, install_id_fn, version, cfg_fn):
    """Hilo: envía agregados cada `every` s; ante fallo, backoff."""
    while True:
        time.sleep(60)
        try:
            if cfg_fn().sink:
                con = db_fn()
                try:
                    forward_once(con, install_id_fn(), version, cfg_fn())
                finally:
                    con.close()
        except Exception:
            pass


def relay_ingest(raw, auth_header, sink_local):
    """Reenvía un lote crudo al monitor local. Devuelve (código, cuerpo).

    El destino está fijado a localhost (sin SSRF posible); la autenticación
    viaja intacta para que el monitor la valide.
    """
    req = urllib.request.Request(
        sink_local + "/api/telemetry-ingest", data=raw, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": auth_header or ""})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception:
        return 502, '{"error": "Receptor no disponible; se reintentará."}'.encode("utf-8")
