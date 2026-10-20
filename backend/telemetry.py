"""Telemetría de la app: store, forward y relay.

El catálogo y los validadores viven en telemetry_common (contrato con el
Monitor); la infraestructura de envío en telemetry_forward. Aquí quedan las
funciones que conectan esa infraestructura con la BD de esta instalación.
"""
import os
import secrets
import sys

import telemetry_common as _tc
import telemetry_forward as _fwd
from config import DB_PATH, VERSION
from schema import db

TELEMETRY_DAYS = _tc.TELEMETRY_DAYS
_validate_telemetry = _tc.validate_telemetry

# Destino del forward: entorno primero, telemetry.conf junto a la BD después.
# Sin ninguno: modo local puro, nada sale de este servidor.
_CONF_PATH = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "telemetry.conf")
TELEMETRY_SINK, TELEMETRY_TOKEN = _tc.resolve_sink(os.environ, _CONF_PATH)
FORWARD_EVERY = int(os.environ.get("NEVERRED_FORWARD_EVERY", "900"))
FORWARD_MAX_DELAY = 3600
SINK_LOCAL = os.environ.get("NEVERRED_SINK_LOCAL", "http://127.0.0.1:8140").rstrip("/")
# Plataforma de ESTA instalación (solo SO, nada identificable).
TELEMETRY_PLATFORM = {"darwin": "macOS", "linux": "Linux",
                      "win32": "Windows"}.get(sys.platform, sys.platform)


def get_install_id():
    """ID seudónimo de esta instalación (contar betas + idempotencia).
    Se genera una vez junto a la BD; no identifica a ninguna persona."""
    path = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "install.id")
    try:
        with open(path) as f:
            iid = f.read().strip()
        if iid:
            return iid
    except OSError:
        pass
    iid = secrets.token_hex(16)
    try:
        with open(path, "w") as f:
            f.write(iid)
        os.chmod(path, 0o600)
    except OSError:
        pass
    return iid


def _forward_cfg():
    """Config del forward con los valores efectivos de esta instalación."""
    return _fwd.ForwardConfig(sink=TELEMETRY_SINK, token=TELEMETRY_TOKEN,
                              platform=TELEMETRY_PLATFORM, every=FORWARD_EVERY,
                              max_delay=FORWARD_MAX_DELAY, sink_local=SINK_LOCAL)


def backoff_delay(fails):
    return _fwd.backoff_delay(fails, FORWARD_MAX_DELAY)


def forward_state():
    con = db()
    try:
        return _fwd.forward_state(con)
    finally:
        con.close()


def _key_dir():
    """Directorio de la clave de emparejamiento (junto a la BD)."""
    return os.path.dirname(os.path.abspath(DB_PATH))


def forward_once(force=False):
    """Intenta un envío al receptor. Devuelve dict de estado (siempre)."""
    con = db()
    try:
        return _fwd.forward_once(con, get_install_id(), VERSION, _forward_cfg(),
                                 _key_dir(), force)
    finally:
        con.close()


def forward_loop():
    """Hilo: envía agregados cada FORWARD_EVERY s; ante fallo, backoff."""
    _fwd.forward_loop(db, get_install_id, VERSION, _forward_cfg, _key_dir)


def identity_status():
    con = db()
    try:
        return _fwd.identity_status(con)
    finally:
        con.close()


def relay_ingest(raw, auth_header, sink_local, endpoint="ingest"):
    return _fwd.relay_ingest(raw, auth_header, sink_local, endpoint)
