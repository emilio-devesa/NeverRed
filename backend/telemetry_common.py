#!/usr/bin/env python3
"""NeverRed — contrato compartido App <-> Monitor de telemetría.

Frontera entre los dos mundos: el catálogo cerrado de eventos, los
validadores puros (sin E/S, sin BD), el protocolo de emparejamiento
asimétrico (Ed25519 vía ssh-keygen) y la lectura del fichero de
configuración del destino (`telemetry.conf`).

Lo importan `server.py` (lado App), `telemetry_forward.py` y
`telemetry_sink.py` (lado Monitor). El monitor NO importa `server`:
se despliega copiando `telemetry_sink.py` + este fichero.

NOTA: importar siempre como `import telemetry_common as _tc` (nunca
`from telemetry_common import ...` en cascada): el Python del sistema
provoca pérdidas de nombres con la segunda forma tras varias importaciones.
"""

import json
import os
import re

# ---------------- Catálogo cerrado ----------------
# Solo estos eventos con solo estas props (conteos) se aceptan. Nada de
# importes, textos, nombres ni identificadores: por construcción es
# imposible que se cuele contenido contable.
TELEMETRY_EVENTS = {
    "app_opened": {},
    "vista_inicio": {}, "vista_diario": {}, "vista_mayor": {},
    "vista_cuentas": {}, "vista_informes": {}, "vista_herramientas": {},
    "asiento_creado": {"n_lineas": int},
    "asiento_eliminado": {},
    "csv_importado": {"n_filas": int},
    "import_json": {"n_asientos": int},
    "recurrentes_generados": {"n": int},
    "demo_entrada": {},
    "export_json": {},
    "conflicto_409": {},
}
TELEMETRY_DAYS = 90
TELEMETRY_MAX_BATCH = 100
AGGREGATE_MAX_BATCH = 500
COUNT_MAX = 1_000_000

# Emparejamiento asimétrico (v1): la App genera un par Ed25519 y el Monitor
# aprueba su pubkey. Espacio de nombres de firma (ssh-keygen -Y).
SIGN_NAMESPACE = "neverred-telemetry-v1"
PUBKEY_RE = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/]+={0,2}( .*)?$")
ENROLL_STATES = ("none", "pending", "approved", "rejected", "unavailable", "legacy")

# Claves permitidas en telemetry.conf (nada más se lee de ese fichero).
CONF_SINK = "NEVERRED_TELEMETRY_SINK"
CONF_TOKEN = "NEVERRED_TELEMETRY_TOKEN"


def validate_telemetry(events):
    """None = lote válido; str = error. Normaliza props a enteros acotados."""
    if not isinstance(events, list) or not events or len(events) > TELEMETRY_MAX_BATCH:
        return "Lote de telemetría inválido."
    for e in events:
        if not isinstance(e, dict):
            return "Evento de telemetría inválido."
        name = e.get("event")
        spec = TELEMETRY_EVENTS.get(name) if isinstance(name, str) else None
        if spec is None:
            return "Evento no catalogado: %r." % (name,)
        props = e.get("props", {})
        if not isinstance(props, dict):
            return "Props de telemetría inválidas."
        clean = {}
        for k, v in props.items():
            if k not in spec or not isinstance(v, int) or isinstance(v, bool):
                return "Prop no permitida en %s: %r." % (name, k)
            clean[k] = max(0, min(v, COUNT_MAX))
        e["props"] = clean
    return None


def validate_payload(body):
    """None = lote de agregados válido; str = error. Usado por el monitor."""
    if not isinstance(body, dict):
        return "Cuerpo JSON inválido."
    iid = body.get("install_id")
    if not isinstance(iid, str) or not 8 <= len(iid) <= 128:
        return "install_id inválido."
    aggs = body.get("aggregates")
    if not isinstance(aggs, list) or not aggs or len(aggs) > AGGREGATE_MAX_BATCH:
        return "Lote inválido."
    for a in aggs:
        if not isinstance(a, dict):
            return "Agregado inválido."
        if a.get("event") not in TELEMETRY_EVENTS:
            return "Evento no catalogado: %r." % (a.get("event"),)
        if not isinstance(a.get("date"), str) or \
                not re.match(r"^\d{4}-\d{2}-\d{2}$", a["date"]):
            return "Fecha inválida."
        # Tolerancia con emisores de la primera beta (sin hora): hora 00.
        a["hour"] = a.get("hour") or "00"
        if not isinstance(a["hour"], str) or \
                not re.match(r"^([01]\d|2[0-3])$", a["hour"]):
            return "Hora inválida."
        if not isinstance(a.get("count"), int) or isinstance(a.get("count"), bool) \
                or not 0 <= a["count"] <= COUNT_MAX:
            return "Conteo inválido."
    stats = body.get("client_stats", {})
    if not isinstance(stats, dict):
        return "Stats inválidas."
    for k in ("total_sends", "total_fails"):
        v = stats.get(k, 0)
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            return "Stat inválida: %s." % k
    return None


def validate_pubkey(pubkey):
    """None = pubkey Ed25519 con formato válido; str = error."""
    if not isinstance(pubkey, str) or not PUBKEY_RE.match(pubkey.strip()):
        return "Pubkey inválida (se espera ssh-ed25519)."
    return None


def validate_enroll(body):
    """None = solicitud de enrolamiento válida; str = error."""
    if not isinstance(body, dict):
        return "Cuerpo JSON inválido."
    iid = body.get("install_id")
    if not isinstance(iid, str) or not 8 <= len(iid) <= 128:
        return "install_id inválido."
    return validate_pubkey(body.get("pubkey"))


def canonical_envelope(install_id, version, platform, client_stats, aggregates):
    """Bytes canónicos que firma la App y verifica el Monitor (JSON ordenado)."""
    stats = client_stats if isinstance(client_stats, dict) else {}
    env = {"install_id": install_id, "version": str(version or "")[:16],
           "platform": str(platform or ""),
           "client_stats": {"total_sends": int(stats.get("total_sends", 0)),
                            "total_fails": int(stats.get("total_fails", 0))},
           "aggregates": aggregates if isinstance(aggregates, list) else []}
    return json.dumps(env, sort_keys=True, separators=(",", ":")).encode("utf-8")


def read_telemetry_conf(path):
    """Lee telemetry.conf (formato CLAVE=valor, una por línea).

    Solo reconoce NEVERRED_TELEMETRY_SINK y NEVERRED_TELEMETRY_TOKEN;
    ignora comentarios (#), líneas vacías y cualquier otra clave.
    Devuelve {} si el fichero no existe. Nunca ejecuta nada.
    """
    conf = {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("\"'")
        if key in (CONF_SINK, CONF_TOKEN) and value:
            conf[key] = value.rstrip("/") if key == CONF_SINK else value
    return conf


def resolve_sink(env=os.environ, conf_path=None):
    """Destino de telemetría efectivo: entorno primero, telemetry.conf después.

    Devuelve (sink_url, token), vacíos si no hay nada configurado (modo
    local puro: la app no envía nada a ningún sitio).
    """
    sink = (env.get(CONF_SINK) or "").rstrip("/")
    token = env.get(CONF_TOKEN) or ""
    if (not sink or not token) and conf_path:
        conf = read_telemetry_conf(conf_path)
        sink = sink or conf.get(CONF_SINK, "")
        token = token or conf.get(CONF_TOKEN, "")
    return sink, token
