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
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

import telemetry_common as _tc


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


# ---------------- Identidad y emparejamiento (Ed25519) ----------------
def have_signer():
    """True si hay ssh-keygen disponible para firmar."""
    return shutil.which("ssh-keygen") is not None


def key_paths(data_dir):
    priv = os.path.join(data_dir, "telemetry-key")
    return priv, priv + ".pub"


def ensure_keypair(data_dir):
    """(priv_path, pubkey) generándola si falta; (None, None) sin firmador."""
    if not have_signer():
        return None, None
    priv, pub = key_paths(data_dir)
    if not (os.path.isfile(priv) and os.path.isfile(pub)):
        try:
            os.makedirs(data_dir, exist_ok=True)
            r = subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-f", priv,
                 "-N", "", "-C", "neverred-telemetry"],
                capture_output=True, timeout=30)
            if r.returncode != 0 or not os.path.isfile(pub):
                return None, None
            os.chmod(priv, 0o600)
        except Exception:
            return None, None
    try:
        with open(pub, encoding="utf-8") as f:
            pk = f.read().strip().splitlines()[0].strip()
    except (OSError, IndexError):
        return None, None
    if _tc.validate_pubkey(pk):
        return None, None
    return priv, pk


def identity_status(con):
    """none|pending|approved|rejected|unavailable (lado App)."""
    con.execute("CREATE TABLE IF NOT EXISTS telemetry_identity ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), "
                "status TEXT NOT NULL DEFAULT 'none', "
                "updated_at INTEGER NOT NULL DEFAULT 0)")
    con.execute("INSERT OR IGNORE INTO telemetry_identity (id) VALUES (1)")
    row = con.execute("SELECT status FROM telemetry_identity WHERE id = 1").fetchone()
    con.commit()
    st = row["status"] if row else "none"
    return st if st in _tc.ENROLL_STATES else "none"


def set_identity(con, status):
    if status not in _tc.ENROLL_STATES:
        return
    con.execute("CREATE TABLE IF NOT EXISTS telemetry_identity ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), "
                "status TEXT NOT NULL DEFAULT 'none', "
                "updated_at INTEGER NOT NULL DEFAULT 0)")
    con.execute("INSERT OR IGNORE INTO telemetry_identity (id) VALUES (1)")
    con.execute("UPDATE telemetry_identity SET status = ?, updated_at = ? WHERE id = 1",
                (status, int(time.time())))
    con.commit()


def sign_envelope(priv_path, canonical):
    """Firma armor (str) o None si no se puede firmar."""
    if not have_signer():
        return None
    tmpd = tempfile.mkdtemp(prefix="nr-sign-")
    try:
        msg = os.path.join(tmpd, "m")
        with open(msg, "wb") as f:
            f.write(canonical)
        r = subprocess.run(
            ["ssh-keygen", "-Y", "sign", "-f", priv_path,
             "-n", _tc.SIGN_NAMESPACE, msg],
            capture_output=True, timeout=20)
        if r.returncode != 0:
            return None
        with open(msg + ".sig", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def _post_json(url, payload, timeout=15):
    """(código|None, dict)."""
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            try:
                return r.status, json.loads(r.read().decode() or "{}")
            except Exception:
                return r.status, {}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except Exception:
        return None, {}


def enroll_once(post_url, install_id, pubkey):
    """Respuesta del monitor al enrolar ({} si la red falla)."""
    _, data = _post_json(post_url, {"install_id": install_id, "pubkey": pubkey})
    return data if isinstance(data, dict) else {}


def _backoff(con, st, now, max_delay):
    fails = st["fails"] + 1
    delay = backoff_delay(fails, max_delay)
    con.execute("UPDATE telemetry_forward SET fails = ?, next_retry = ?, "
                "total_fails = total_fails + 1 WHERE id = 1",
                (fails, now + delay))
    con.commit()
    return fails, delay


def _forward_legacy(con, install_id, version, cfg, status, st, now):
    """Comportamiento histórico con bearer compartido (transición)."""
    status["enroll"] = "legacy"
    status["enrolled"] = True
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
        con.commit()
        status.update(pending=0, sent=True, last_ok=now, fails=0,
                      next_retry_in=cfg.every)
    else:
        fails, delay = _backoff(con, st, now, cfg.max_delay)
        status.update(fails=fails, next_retry_in=delay)
    return status


def forward_once(con, install_id, version, cfg, key_dir, force=False):
    """Intenta un envío al receptor. Devuelve dict de estado (siempre).

    Sin token: emparejamiento asimétrico (genera clave, enrola, envía
    firmado cuando el coordinador aprueba). Lo no aprobado se queda en
    local tras el watermark: nada se pierde.
    """
    st = forward_state(con)
    now = int(time.time())
    status = {"configured": bool(cfg.sink), "pending": 0, "sent": False,
              "enrolled": False, "enroll": "none",
              "last_ok": st["last_ok"], "fails": st["fails"],
              "next_retry_in": max(0, st["next_retry"] - now)}
    if not cfg.sink:
        return status
    if not force and now < st["next_retry"]:
        return status
    if cfg.token:
        return _forward_legacy(con, install_id, version, cfg, status, st, now)
    priv, pubkey = ensure_keypair(key_dir)
    if not priv:
        status["enroll"] = "unavailable"
        return status
    ident = identity_status(con)
    status["enroll"] = ident
    if ident != "approved":
        resp = enroll_once(cfg.sink + "/api/telemetry-enroll", install_id, pubkey)
        new = str(resp.get("enroll") or "")
        if new in ("pending", "approved", "rejected"):
            set_identity(con, new)
            status["enroll"] = new
        if status["enroll"] != "approved":
            return status
        ident = "approved"  # aprobada en este mismo intento: seguir y enviar
    status["enrolled"] = True
    aggs = build_aggregates(con, st["watermark"])
    status["pending"] = len(aggs)
    if not aggs:
        status["sent"] = True
        return status
    con.execute("UPDATE telemetry_forward SET total_sends = total_sends + 1 WHERE id = 1")
    con.commit()
    stats = {"total_sends": st["total_sends"] + 1, "total_fails": st["total_fails"]}
    canonical = _tc.canonical_envelope(install_id, version, cfg.platform, stats, aggs)
    sig = sign_envelope(priv, canonical)
    if not sig:
        print("[neverred] no se pudo firmar el lote; reintento con backoff.")
        fails, delay = _backoff(con, st, now, cfg.max_delay)
        status.update(fails=fails, next_retry_in=delay)
        return status
    payload = {"install_id": install_id, "version": version,
               "platform": cfg.platform, "client_stats": stats,
               "aggregates": aggs, "signature": sig}
    code, resp = _post_json(cfg.sink + "/api/telemetry-ingest", payload)
    if code == 200:
        con.execute("UPDATE telemetry_forward SET watermark = ?, last_ok = ?, "
                    "fails = 0, next_retry = ? WHERE id = 1",
                    (now, now, now + cfg.every))
        con.commit()
        status.update(pending=0, sent=True, last_ok=now, fails=0,
                      next_retry_in=cfg.every)
        return status
    if code == 403 and isinstance(resp, dict) and resp.get("enroll") in (
            "required", "pending", "approved", "rejected"):
        new = "none" if resp["enroll"] == "required" else resp["enroll"]
        set_identity(con, new)
        status["enroll"] = new
        status["enrolled"] = new == "approved"
        return status
    print("[neverred] forward fallido (http %s); reintento con backoff." % code)
    fails, delay = _backoff(con, st, now, cfg.max_delay)
    status.update(fails=fails, next_retry_in=delay)
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


def relay_ingest(raw, auth_header, sink_local, endpoint="ingest"):
    """Reenvía un lote crudo al monitor local. Devuelve (código, cuerpo).

    El destino está fijado a localhost (sin SSRF posible); la autenticación
    viaja intacta para que el monitor la valide. Solo `ingest` y `enroll`.
    """
    if endpoint not in ("ingest", "enroll"):
        return 400, b'{"error": "Relay no soportado."}'
    req = urllib.request.Request(
        sink_local + "/api/telemetry-" + endpoint, data=raw, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": auth_header or ""})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception:
        return 502, '{"error": "Receptor no disponible; se reintentará."}'.encode("utf-8")
