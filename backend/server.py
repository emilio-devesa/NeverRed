#!/usr/bin/env python3
"""NeverRed backend — sin dependencias externas (solo biblioteca estandar).

- Sirve la app estatica (index.html, styles.css, app.js).
- API JSON con registro/login de usuarios, sesiones con token y
  persistencia de los datos contables por usuario en SQLite.

Uso:
    python3 backend/server.py [--port 8000]

La base de datos se crea automaticamente en backend/neverred.db.
"""
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from datetime import date as _date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Ruta de la BD configurable (imprescindible para Docker/volúmenes)
DB_PATH = os.environ.get(
    "NEVERRED_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "neverred.db"),
)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 256
AUDIT_DAYS = 90
SESSION_DAYS = int(os.environ.get("NEVERRED_SESSION_DAYS", "30"))
# Caducidad por inactividad: el token muere si no se usa en este plazo,
# aunque no haya llegado a SESSION_DAYS (un token robado vale menos tiempo).
SESSION_IDLE_DAYS = int(os.environ.get("NEVERRED_SESSION_IDLE_DAYS", "7"))
PBKDF2_ITERATIONS = 200_000 if os.environ.get("NEVERRED_FAST_HASH") == "1" else 600_000
VERSION = os.environ.get("NEVERRED_VERSION", "2.3.7")
# Rate-limit anti fuerza bruta (en memoria): intentos por IP y ventana
RATE_MAX = int(os.environ.get("NEVERRED_RATE_MAX", "10"))
RATE_WINDOW = int(os.environ.get("NEVERRED_RATE_WINDOW", "600"))
_rate = {}  # ip -> [intentos, fin_ventana_ts]


def rate_limited(ip):
    now = int(time.time())
    count, reset = _rate.get(ip, (0, now + RATE_WINDOW))
    if now > reset:
        count, reset = 0, now + RATE_WINDOW
    count += 1
    _rate[ip] = [count, reset]
    return count > RATE_MAX


def rate_reset(ip):
    _rate.pop(ip, None)


# Latidos de pestaña para el autoapagado (lo activa el lanzador de macOS con
# NEVERRED_QUIT_WHEN_IDLE=1): cada pestaña late cada 10 s; sin latidos durante
# IDLE_TIMEOUT segundos y tras 60 s de arranque, el servidor se apaga solo.
QUIT_WHEN_IDLE = os.environ.get("NEVERRED_QUIT_WHEN_IDLE") == "1"
IDLE_TIMEOUT = int(os.environ.get("NEVERRED_IDLE_TIMEOUT", "20"))
_tabs = {}
_tabs_lock = threading.Lock()
_had_tabs = False
_start_ts = int(time.time())

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


# ---------------- Base de datos ----------------
def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    return con


def init_db():
    con = db()
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_data (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            data TEXT NOT NULL DEFAULT '{}',
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS password_resets (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            user_id INTEGER,
            action TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS telemetry_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            event TEXT NOT NULL,
            props TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS telemetry_consent (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            enabled INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS telemetry_forward (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            watermark INTEGER NOT NULL DEFAULT 0,
            last_ok INTEGER NOT NULL DEFAULT 0,
            fails INTEGER NOT NULL DEFAULT 0,
            next_retry INTEGER NOT NULL DEFAULT 0,
            total_sends INTEGER NOT NULL DEFAULT 0,
            total_fails INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS telemetry_identity (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            status TEXT NOT NULL DEFAULT 'none',
            updated_at INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_resets_user ON password_resets(user_id);
        CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
        CREATE INDEX IF NOT EXISTS idx_telemetry_user ON telemetry_events(user_id);
        CREATE INDEX IF NOT EXISTS idx_telemetry_event ON telemetry_events(event, ts);
        """
    )
    con.commit()
    # Migración: actividad de sesión para la caducidad por inactividad.
    cols = [r[1] for r in con.execute("PRAGMA table_info(sessions)")]
    if "last_seen" not in cols:
        con.execute("ALTER TABLE sessions ADD COLUMN last_seen INTEGER NOT NULL DEFAULT 0")
        con.execute("UPDATE sessions SET last_seen = created_at WHERE last_seen = 0")
        con.commit()
    # Migración: saber si al usuario ya se le preguntó por la telemetría.
    cols = [r[1] for r in con.execute("PRAGMA table_info(telemetry_consent)")]
    if "asked" not in cols:
        con.execute("ALTER TABLE telemetry_consent ADD COLUMN asked INTEGER NOT NULL DEFAULT 0")
        con.commit()
    # Migración: contadores acumulados de envíos para la media de reintentos.
    cols = [r[1] for r in con.execute("PRAGMA table_info(telemetry_forward)")]
    for col in ("total_sends", "total_fails"):
        if col not in cols:
            con.execute("ALTER TABLE telemetry_forward ADD COLUMN %s INTEGER NOT NULL DEFAULT 0" % col)
            con.commit()
    con.close()


# ---------------- Contrasenas ----------------
def hash_password(password, salt=None):
    """PBKDF2-HMAC-SHA256. Formato: pbkdf2$iter$salt_hex$hash_hex. Nunca texto plano."""
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2${}${}${}".format(PBKDF2_ITERATIONS, salt.hex(), dk.hex())


def verify_password(password, stored):
    try:
        _, iters, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# Hash ficticio para igualar tiempos en el login: si el correo no existe se
# verifica igualmente contra esto, para no filtrar usuarios por temporización.
_DUMMY_HASH = hash_password("neverred-usuario-inexistente")


def public_user(row):
    return {"id": row["id"], "name": row["name"], "email": row["email"]}


def password_ok(pw):
    return isinstance(pw, str) and MIN_PASSWORD_LEN <= len(pw) <= MAX_PASSWORD_LEN


# ---------------- Telemetría: store (App) ----------------
# El catálogo y los validadores viven en telemetry_common (contrato con el
# Monitor); el forward/relay en telemetry_forward (infraestructura). Aquí
# solo queda el store: datos del usuario, con su consentimiento y borrado.
# (Import namespaced a propósito: `from ... import` en cascada pierde
# nombres en el Python del sistema.)
import telemetry_common as _tc
import telemetry_forward as _fwd

TELEMETRY_EVENTS = _tc.TELEMETRY_EVENTS  # re-export histórico
TELEMETRY_DAYS = _tc.TELEMETRY_DAYS
TELEMETRY_MAX_BATCH = _tc.TELEMETRY_MAX_BATCH
_validate_telemetry = _tc.validate_telemetry  # compatibilidad histórica

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


# Usuario de demostración (credenciales públicas y documentadas)
DEMO_EMAIL = "demo@neverred.local"
DEMO_PASSWORD = "DemoNeverRed2026"
DEMO_NAME = "Demo"


def demo_data():
    """Genera 6 meses de contabilidad de muestra (dinámico, siempre actual)."""
    import calendar
    import datetime
    import random
    random.seed(7)
    today = datetime.date.today()
    months = []
    y, m = today.year, today.month
    for _ in range(6):
        months.insert(0, (y, m, (y, m) == (today.year, today.month)))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    acc = [
        ('a570', '570', 'Caja · Efectivo', 'Activo'),
        ('a572', '572', 'Banco cuenta principal', 'Activo'),
        ('a573', '573', 'Ahorros', 'Activo'),
        ('a520', '520', 'Tarjeta de crédito', 'Pasivo'),
        ('a101', '101', 'Capital inicial', 'Patrimonio'),
        ('a700', '700', 'Sueldos y salarios', 'Ingreso'),
        ('a701', '701', 'Ingresos extra', 'Ingreso'),
        ('a600', '600', 'Alquiler / Vivienda', 'Gasto'),
        ('a601', '601', 'Comida y supermercado', 'Gasto'),
        ('a602', '602', 'Transporte', 'Gasto'),
        ('a603', '603', 'Ocio y suscripciones', 'Gasto'),
        ('a604', '604', 'Salud', 'Gasto'),
        ('a605', '605', 'Suministros (luz, agua, internet)', 'Gasto'),
    ]
    accounts = [{'id': i, 'code': c, 'name': n, 'type': t, 'archived': False}
                for i, c, n, t in acc]
    entries, seq = [], [1]

    def add(date, desc, pairs):
        entries.append({'id': 'e%03d' % seq[0], 'n': seq[0], 'date': date, 'desc': desc,
                        'lines': [{'accountId': a, 'debit': db, 'credit': cr}
                                  for a, db, cr in pairs]})
        seq[0] += 1

    y0, m0, _ = months[0]
    add('%04d-%02d-01' % (y0, m0), 'Asiento de apertura',
        [('a570', 800, 0), ('a572', 3500, 0), ('a101', 0, 4300)])
    supers = ['Mercadona semanal', 'Compra grande Carrefour', 'Frutería del barrio']
    ocios = [('Netflix + Spotify', 15.99), ('Cena con amigos', 42.5), ('Cine', 19.0),
             ('Concierto', 55.0), ('Escapada fin de semana', 120.0)]
    for idx, (y, m, current) in enumerate(months):
        last = calendar.monthrange(y, m)[1]
        top = min(last, today.day) if current else last
        P = lambda d: '%04d-%02d-%02d' % (y, m, min(d, top))
        mk = '%04d-%02d' % (y, m)
        add(P(1), 'Nómina del mes', [('a572', 2450, 0), ('a700', 0, 2450)])
        add(P(5), 'Alquiler piso', [('a600', 850, 0), ('a572', 0, 850)])
        add(P(3), 'Abono transporte', [('a602', 40, 0), ('a572', 0, 40)])
        s = round(random.uniform(82, 112), 2)
        add(P(12), 'Suministros (luz, agua, internet)', [('a605', s, 0), ('a572', 0, s)])
        for _ in range(3):
            g = round(random.uniform(45, 130), 2)
            add(P(random.randint(1, top)), random.choice(supers),
                [('a601', g, 0), ('a520', 0, g)])
        desc, imp = random.choice(ocios)
        add(P(random.randint(1, top)), desc, [('a603', imp, 0), ('a572', 0, imp)])
        add(P(20), 'Transferencia a ahorros', [('a573', 200, 0), ('a572', 0, 200)])
        deuda = round(sum(l['credit'] - l['debit'] for e in entries
                          for l in e['lines'] if l['accountId'] == 'a520'
                          and e['date'].startswith(mk)), 2)
        if deuda > 0 and not current:
            add(P(last), 'Pago tarjeta de crédito', [('a520', deuda, 0), ('a572', 0, deuda)])
    entries.sort(key=lambda e: (e['date'], e['n']))
    data = {'accounts': accounts, 'entries': entries, 'seq': seq[0], 'currency': 'EUR',
            'budgets': {'a601': 350, 'a603': 120},
            'recurring': [{'id': 'r001', 'desc': 'Alquiler piso', 'day': 5,
                           'lastRun': '%04d-%02d' % (today.year, today.month),
                           'lines': [{'accountId': 'a600', 'debit': 850, 'credit': 0},
                                     {'accountId': 'a572', 'debit': 0, 'credit': 850}]}]}
    assert validate_data({**data}) is None
    return data


_log = None


def setup_logging():
    """Log a fichero con rotación si NEVERRED_LOG_FILE está definido."""
    global _log
    path = os.environ.get("NEVERRED_LOG_FILE")
    if not path:
        return
    import logging
    from logging.handlers import RotatingFileHandler
    logger = logging.getLogger("neverred")
    logger.setLevel(logging.INFO)
    logger.addHandler(RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5,
                                          encoding="utf-8"))
    _log = logger


def etag_of(data):
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:32]


MAX_SESSIONS = 10


def tab_seen(tab):
    """Registra el latido de una pestaña (id único por sessionStorage)."""
    global _had_tabs
    with _tabs_lock:
        _tabs[tab] = time.time()
        _had_tabs = True


def prune_tabs(now=None):
    """Elimina pestañas sin latido reciente. Devuelve cuántas quedan vivas."""
    now = now if now is not None else time.time()
    with _tabs_lock:
        for t in [t for t, ts in _tabs.items() if now - ts > IDLE_TIMEOUT]:
            del _tabs[t]
        return len(_tabs)


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


def build_aggregates(since_ts):
    """Agregados pendientes: [{event, date, hour, count}]. Solo conteos."""
    con = db()
    try:
        return _fwd.build_aggregates(con, since_ts)
    finally:
        con.close()


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
    _fwd.forward_loop(db, get_install_id, VERSION, _forward_cfg)


def idle_sweep(server):
    """Hilo vigilante: apaga el servidor si hubo pestañas y ya no queda ninguna."""
    while True:
        time.sleep(5)
        if not QUIT_WHEN_IDLE:
            return
        if _had_tabs and time.time() - _start_ts > 60 and prune_tabs() == 0:
            print("[neverred] Sin pestañas: apagando.")
            server.shutdown()
            return


def audit(action, user_id=None, detail=""):
    try:
        con = db()
        con.execute("INSERT INTO audit_log (ts, user_id, action, detail) VALUES (?,?,?,?)",
                    (int(time.time()), user_id, action, str(detail)[:200]))
        con.commit()
        con.close()
    except Exception:
        pass


APP_URL = os.environ.get("NEVERRED_APP_URL", "http://127.0.0.1:8000").rstrip("/")
RESET_TTL = int(os.environ.get("NEVERRED_RESET_TTL", "3600"))
SESSION_COOKIE = "nr_session"
COOKIE_SECURE = False  # se activa al servir por TLS (ver main)


def _send_reset_email(to, link):
    """Envía el correo de recuperación (stdlib). Sin SMTP, lo muestra por consola (desarrollo)."""
    host = os.environ.get("NEVERRED_SMTP_HOST")
    if not host:
        # Sin SMTP no se envía nada: el token NUNCA se imprime (acabaría en el
        # log en claro). Solo en modo depuración explícito se muestra.
        if os.environ.get("NEVERRED_DEBUG") == "1":
            print("[neverred] (debug) Enlace de recuperación para %s: %s" % (to, link))
        else:
            print("[neverred] SMTP sin configurar: no se pudo enviar el correo a %s "
                  "(define NEVERRED_SMTP_*; en desarrollo usa NEVERRED_DEBUG=1)." % to)
        return
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["Subject"] = "NeverRed: recupera tu contraseña"
    msg["From"] = os.environ.get("NEVERRED_SMTP_FROM", "neverred@localhost")
    msg["To"] = to
    msg.set_content(
        "Hola,\n\nPide cambiar tu contraseña en NeverRed (caduca en 1 hora):\n%s\n\n"
        "Si no fuiste tú, ignora este correo.\n" % link)
    port = int(os.environ.get("NEVERRED_SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=15) as s:
        s.starttls()
        if os.environ.get("NEVERRED_SMTP_USER"):
            s.login(os.environ["NEVERRED_SMTP_USER"], os.environ.get("NEVERRED_SMTP_PASS", ""))
        s.send_message(msg)


VALID_TYPES = ("Activo", "Pasivo", "Patrimonio", "Ingreso", "Gasto")


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _validate_lines(lines, ids):
    """Devuelve (debe, haber, error)."""
    if not isinstance(lines, list) or len(lines) < 2:
        return 0, 0, "Todo asiento necesita al menos 2 líneas."
    d = h = 0.0
    for l in lines:
        if not isinstance(l, dict):
            return 0, 0, "Línea de asiento inválida."
        if l.get("accountId") not in ids:
            return 0, 0, "Línea con cuenta desconocida."
        db, cr = l.get("debit", 0), l.get("credit", 0)
        if not _num(db) or not _num(cr) or db < 0 or cr < 0:
            return 0, 0, "Importes inválidos en un asiento."
        if db > 0 and cr > 0:
            return 0, 0, "Una línea no puede tener debe y haber."
        if db == 0 and cr == 0:
            return 0, 0, "Línea sin importe."
        d, h = d + db, h + cr
    if round(d, 2) != round(h, 2) or d <= 0:
        return 0, 0, "Asiento descuadrado: debe ≠ haber."
    return d, h, None


def validate_data(body):
    """Valida la contabilidad antes de guardarla. None = válido, str = error."""
    if not isinstance(body, dict):
        return "Cuerpo JSON inválido."
    accounts = body.get("accounts", [])
    entries = body.get("entries", [])
    if not isinstance(accounts, list) or not isinstance(entries, list):
        return "Formato de datos inválido."
    ids = set()
    for a in accounts:
        if not isinstance(a, dict):
            return "Cuenta inválida."
        if not isinstance(a.get("id"), str) or not a["id"]:
            return "Cuenta sin identificador."
        if a.get("type") not in VALID_TYPES:
            return "Tipo de cuenta inválido: %r." % (a.get("type"),)
        if not isinstance(a.get("name"), str) or not a["name"].strip():
            return "Cuenta sin nombre."
        if "code" in a and not isinstance(a["code"], str):
            return "Código de cuenta inválido."
        ids.add(a["id"])
    for e in entries:
        if not isinstance(e, dict):
            return "Asiento inválido."
        if not isinstance(e.get("date"), str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", e["date"]):
            return "Fecha inválida en un asiento."
        try:
            _date(*map(int, e["date"].split("-")))
        except ValueError:
            return "Fecha inexistente en un asiento."
        _, _, err = _validate_lines(e.get("lines"), ids)
        if err:
            return err
    budgets = body.get("budgets", {})
    if not isinstance(budgets, dict):
        return "Presupuestos inválidos."
    for k, v in budgets.items():
        if not isinstance(k, str) or not _num(v) or v < 0:
            return "Presupuesto inválido."
    rec = body.get("recurring", [])
    if not isinstance(rec, list):
        return "Recurrentes inválidos."
    for r in rec:
        if not isinstance(r, dict):
            return "Plantilla recurrente inválida."
        if not isinstance(r.get("desc"), str) or not r["desc"].strip():
            return "Plantilla recurrente sin descripción."
        if not isinstance(r.get("day"), int) or not 1 <= r["day"] <= 28:
            return "Día inválido en plantilla recurrente."
        if "lastRun" in r and (not isinstance(r["lastRun"], str) or
                               not re.match(r"^\d{4}-\d{2}$", r["lastRun"])):
            return "Marca de generación inválida."
        _, _, err = _validate_lines(r.get("lines"), ids)
        if err:
            return err
    return None


# ---------------- Servidor HTTP ----------------
class Handler(BaseHTTPRequestHandler):
    server_version = "NeverRed/" + VERSION

    def log_message(self, fmt, *args):  # noqa: A002 - firma de la stdlib
        if args and "/api/ping" in str(args[0]):
            return  # los latidos cada 10 s no ensucian el log
        line = "[neverred] " + fmt % args
        if _log:
            _log.info(line)
        else:
            print(line)

    def client_ip(self):
        if os.environ.get("NEVERRED_TRUST_PROXY") == "1":
            fwd = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if fwd:
                return fwd
        return self.client_address[0]

    # -- utilidades --
    def _cors_headers(self):
        # Solo se permite CORS al mismo origen y al modo archivo local
        # (Origin: null). Cualquier otro sitio no recibe cabeceras.
        from urllib.parse import urlparse as _up
        origin = self.headers.get("Origin") or ""
        host = (self.headers.get("Host") or "").split(":")[0]
        allowed = None
        if origin == "null":
            allowed = "*"  # index.html abierto con doble clic
        elif origin:
            try:
                if _up(origin).hostname in (host, "127.0.0.1", "localhost"):
                    allowed = origin
            except Exception:
                allowed = None
        if allowed:
            self.send_header("Access-Control-Allow-Origin", allowed)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")

    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self._cors_headers()
        for k, v in (getattr(self, "_extra", None) or {}).items():
            self.send_header(k, v)
        if getattr(self, "_cookie", None):
            self.send_header("Set-Cookie", self._cookie)
        self.end_headers()
        self.wfile.write(body)

    def _set_session_cookie(self, token, days):
        parts = ["%s=%s" % (SESSION_COOKIE, token), "Path=/", "HttpOnly", "SameSite=Lax"]
        if days:
            parts.append("Max-Age=%d" % (days * 86400))
        if COOKIE_SECURE:
            parts.append("Secure")
        self._cookie = "; ".join(parts)

    def _clear_session_cookie(self):
        self._cookie = "%s=; Path=/; Max-Age=0" % SESSION_COOKIE

    def _current_token(self):
        # Cookie HttpOnly primero; Bearer solo para el modo archivo local.
        for part in (self.headers.get("Cookie") or "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                if k == SESSION_COOKIE and v:
                    return v
        auth = self.headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            return auth[len("Bearer "):].strip() or None
        return None

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 2_000_000:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return None

    def _auth_user(self):
        token = self._current_token()
        if not token:
            return None
        now = int(time.time())
        con = db()
        row = con.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token = ? AND s.expires_at > ? AND s.last_seen > ?",
            (token, now, now - SESSION_IDLE_DAYS * 86400),
        ).fetchone()
        if row is not None:
            con.execute("UPDATE sessions SET last_seen = ? WHERE token = ?", (now, token))
            con.execute("DELETE FROM sessions WHERE expires_at <= ? OR last_seen <= ?",
                        (now, now - SESSION_IDLE_DAYS * 86400))
            con.execute("DELETE FROM audit_log WHERE ts <= ?", (now - AUDIT_DAYS * 86400,))
            con.commit()
        con.close()
        return row

    def _new_session(self, user_id, days=None):
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        days = days or SESSION_DAYS
        con = db()
        con.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at, last_seen)"
            " VALUES (?,?,?,?,?)",
            (token, user_id, now, now + days * 86400, now),
        )
        # Limpieza oportunista de sesiones caducadas
        con.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        # Tope de sesiones por usuario: expulsa las más antiguas (rowid = orden real)
        con.execute(
            "DELETE FROM sessions WHERE user_id = ? AND token NOT IN "
            "(SELECT token FROM sessions WHERE user_id = ? ORDER BY rowid DESC LIMIT ?)",
            (user_id, user_id, MAX_SESSIONS))
        con.commit()
        con.close()
        return token

    # -- API --
    def _api_register(self):
        if rate_limited(self.client_ip()):
            return self._send_json(429, {"error": "Demasiados intentos. Espera unos minutos."})
        body = self._read_json()
        if not body:
            return self._send_json(400, {"error": "Cuerpo JSON inválido."})
        name = str(body.get("name") or "").strip()[:40] or "contable"
        email = str(body.get("email") or "").strip().lower()
        password = str(body.get("password") or "")
        if not EMAIL_RE.match(email):
            return self._send_json(400, {"error": "Correo electrónico no válido."})
        if not password_ok(password):
            return self._send_json(
                400, {"error": "La contraseña debe tener entre 8 y 256 caracteres."}
            )
        con = db()
        if con.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            con.close()
            return self._send_json(409, {"error": "Ese correo ya está registrado. Inicia sesión."})
        cur = con.execute(
            "INSERT INTO users (name, email, password_hash, created_at) VALUES (?,?,?,?)",
            (name, email, hash_password(password), int(time.time())),
        )
        user_id = cur.lastrowid
        con.execute(
            "INSERT INTO user_data (user_id, data, updated_at) VALUES (?, '{}', ?)",
            (user_id, int(time.time())),
        )
        con.commit()
        user = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        con.close()
        audit("register", user_id, email)
        remember = body.get("remember", True) is not False
        token = self._new_session(user_id, SESSION_DAYS if remember else 1)
        self._set_session_cookie(token, SESSION_DAYS if remember else 0)
        self._send_json(201, {"token": token, "user": public_user(user)})

    def _api_login(self):
        if rate_limited(self.client_ip()):
            return self._send_json(429, {"error": "Demasiados intentos. Espera unos minutos."})
        body = self._read_json()
        if not body:
            return self._send_json(400, {"error": "Cuerpo JSON inválido."})
        email = str(body.get("email") or "").strip().lower()
        password = str(body.get("password") or "")
        con = db()
        user = con.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        con.close()
        ok = verify_password(password, user["password_hash"]) if user is not None \
            else verify_password(password, _DUMMY_HASH) and False
        if not ok:
            audit("login_fail", None, email)
            return self._send_json(401, {"error": "Correo o contraseña incorrectos."})
        try:
            stored_iters = int(user["password_hash"].split("$")[1])
        except Exception:
            stored_iters = 0
        if stored_iters != PBKDF2_ITERATIONS:
            # Re-hash oportunista: endurece hashes creados con parámetros viejos.
            con = db()
            con.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                        (hash_password(password), user["id"]))
            con.commit()
            con.close()
        audit("login", user["id"])
        rate_reset(self.client_ip())
        remember = body.get("remember", True) is not False
        token = self._new_session(user["id"], SESSION_DAYS if remember else 1)
        self._set_session_cookie(token, SESSION_DAYS if remember else 0)
        self._send_json(200, {"token": token, "user": public_user(user)})

    def _api_password(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        body = self._read_json()
        if not body:
            return self._send_json(400, {"error": "Cuerpo JSON inválido."})
        current = str(body.get("current") or "")
        new = str(body.get("new") or "")
        if not verify_password(current, user["password_hash"]):
            return self._send_json(403, {"error": "La contraseña actual no es correcta."})
        if not password_ok(new):
            return self._send_json(
                400, {"error": "La nueva contraseña debe tener entre 8 y 256 caracteres."}
            )
        mine = self._current_token() or ""
        con = db()
        con.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                    (hash_password(new), user["id"]))
        # Cierra las demás sesiones (la actual sigue válida)
        con.execute("DELETE FROM sessions WHERE user_id = ? AND token != ?",
                    (user["id"], mine))
        con.commit()
        con.close()
        audit("password_change", user["id"])
        self._send_json(200, {"ok": True})

    def _api_logout(self):
        token = self._current_token()
        if token:
            con = db()
            con.execute("DELETE FROM sessions WHERE token = ?", (token,))
            con.commit()
            con.close()
        self._clear_session_cookie()
        self._send_json(200, {"ok": True})

    def _api_me(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        self._send_json(200, {"user": public_user(user)})

    def _api_reset_request(self):
        if rate_limited(self.client_ip()):
            return self._send_json(429, {"error": "Demasiados intentos. Espera unos minutos."})
        body = self._read_json() or {}
        email = str(body.get("email") or "").strip().lower()
        # Respuesta idéntica exista o no el correo (no filtrar usuarios)
        con = db()
        user = con.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user is not None:
            token = secrets.token_urlsafe(32)
            th = hashlib.sha256(token.encode()).hexdigest()
            now = int(time.time())
            con.execute("DELETE FROM password_resets WHERE user_id = ?", (user["id"],))
            con.execute(
                "INSERT INTO password_resets (token_hash, user_id, created_at, expires_at)"
                " VALUES (?,?,?,?)", (th, user["id"], now, now + RESET_TTL))
            con.commit()
            try:
                _send_reset_email(email, "%s/?reset=%s" % (APP_URL, token))
            except Exception as e:
                print("[neverred] No se pudo enviar el correo: %s" % e)
        con.close()
        if user is not None:
            audit("reset_request", user["id"])
        self._send_json(200, {"ok": True})

    def _api_reset_confirm(self):
        if rate_limited(self.client_ip()):
            return self._send_json(429, {"error": "Demasiados intentos. Espera unos minutos."})
        body = self._read_json()
        if not body:
            return self._send_json(400, {"error": "Cuerpo JSON inválido."})
        token = str(body.get("token") or "")
        new = str(body.get("new") or "")
        if not password_ok(new):
            return self._send_json(
                400, {"error": "La nueva contraseña debe tener entre 8 y 256 caracteres."})
        th = hashlib.sha256(token.encode()).hexdigest()
        con = db()
        row = con.execute(
            "SELECT * FROM password_resets WHERE token_hash = ? AND expires_at > ?",
            (th, int(time.time()))).fetchone()
        if row is None:
            con.close()
            return self._send_json(400, {"error": "Enlace inválido o caducado."})
        con.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                    (hash_password(new), row["user_id"]))
        con.execute("DELETE FROM password_resets WHERE user_id = ?", (row["user_id"],))
        con.execute("DELETE FROM sessions WHERE user_id = ?", (row["user_id"],))
        con.commit()
        con.close()
        audit("reset_confirm", row["user_id"])
        self._send_json(200, {"ok": True})

    def _api_delete_account(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        # Borrarlo todo es irreversible: exige la contraseña actual aunque la
        # sesión sea válida (frena el daño si roban el token).
        body = self._read_json()
        if not body:
            return self._send_json(400, {"error": "Cuerpo JSON inválido."})
        if not verify_password(str(body.get("current") or ""), user["password_hash"]):
            audit("account_delete_denied", user["id"])
            return self._send_json(403, {"error": "La contraseña no es correcta."})
        con = db()
        con.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
        con.execute("DELETE FROM user_data WHERE user_id = ?", (user["id"],))
        con.execute("DELETE FROM telemetry_events WHERE user_id = ?", (user["id"],))
        con.execute("DELETE FROM telemetry_consent WHERE user_id = ?", (user["id"],))
        con.execute("DELETE FROM users WHERE id = ?", (user["id"],))
        con.commit()
        con.close()
        audit("account_delete", user["id"], user["email"])
        self._send_json(200, {"ok": True})

    def _api_sessions(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        mine = self._current_token() or ""
        con = db()
        rows = con.execute(
            "SELECT token, created_at, expires_at FROM sessions WHERE user_id = ?"
            " AND expires_at > ? ORDER BY created_at",
            (user["id"], int(time.time()))).fetchall()
        con.close()
        self._send_json(200, {"sessions": [
            {"id": t[:12], "current": t == mine, "created_at": c, "expires_at": e}
            for t, c, e in rows]})

    def _api_sessions_rotate(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        mine = self._current_token() or ""
        con = db()
        cur = con.execute("DELETE FROM sessions WHERE user_id = ? AND token != ?",
                          (user["id"], mine))
        con.commit()
        con.close()
        audit("sessions_rotate", user["id"], "cerradas=%d" % cur.rowcount)
        self._send_json(200, {"ok": True, "closed": cur.rowcount})

    def _api_telemetry(self):
        # Solo store: acepta el lote si el usuario dio consentimiento y todo
        # el lote pasa el allowlist. No hay forward a ningún sitio.
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        con = db()
        row = con.execute("SELECT enabled FROM telemetry_consent WHERE user_id = ?",
                          (user["id"],)).fetchone()
        if not row or not row["enabled"]:
            con.close()
            return self._send_json(403, {"error": "Telemetría no activada."})
        body = self._read_json()
        events = body.get("events") if isinstance(body, dict) else None
        err = _validate_telemetry(events)
        if err:
            con.close()
            return self._send_json(400, {"error": err})
        now = int(time.time())
        con.executemany(
            "INSERT INTO telemetry_events (ts, user_id, event, props) VALUES (?,?,?,?)",
            [(now, user["id"], e["event"], json.dumps(e["props"], sort_keys=True))
             for e in events])
        con.execute("DELETE FROM telemetry_events WHERE ts <= ?", (now - TELEMETRY_DAYS * 86400,))
        con.commit()
        con.close()
        self._send_json(200, {"ok": True, "received": len(events)})

    def _api_telemetry_consent(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        con = db()
        row = con.execute("SELECT enabled, asked FROM telemetry_consent WHERE user_id = ?",
                          (user["id"],)).fetchone()
        con.close()
        self._send_json(200, {"enabled": bool(row and row["enabled"]),
                              "asked": bool(row and row["asked"])})

    def _api_telemetry_consent_put(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        body = self._read_json()
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            return self._send_json(400, {"error": "Cuerpo JSON inválido."})
        con = db()
        con.execute(
            "INSERT INTO telemetry_consent (user_id, enabled, asked) VALUES (?,?,1) "
            "ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled, asked=1",
            (user["id"], 1 if body["enabled"] else 0))
        if not body["enabled"]:
            # Al revocar, se borra lo acumulado: sin consentimiento no hay datos.
            con.execute("DELETE FROM telemetry_events WHERE user_id = ?", (user["id"],))
        con.commit()
        con.close()
        audit("telemetry_consent", user["id"], "enabled=%s" % body["enabled"])
        self._send_json(200, {"ok": True, "enabled": body["enabled"]})

    def _api_telemetry_summary(self):
        # Agregado local de los últimos 30 días: lo mismo que vería el usuario
        # en su panel; base del futuro forward (que enviaría solo esto).
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        con = db()
        rows = con.execute(
            "SELECT event, COUNT(*) FROM telemetry_events "
            "WHERE user_id = ? AND ts > ? GROUP BY event ORDER BY 2 DESC",
            (user["id"], int(time.time()) - 30 * 86400)).fetchall()
        con.close()
        self._send_json(200, {"summary": [{"event": e, "count": c} for e, c in rows]})

    def _api_forward_status(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        st = forward_state()
        now = int(time.time())
        sends, fails = st["total_sends"], st["total_fails"]
        con = db()
        try:
            ident = _fwd.identity_status(con)
        finally:
            con.close()
        self._send_json(200, {"configured": bool(TELEMETRY_SINK),
                              "last_ok": st["last_ok"], "fails": st["fails"],
                              "next_retry_in": max(0, st["next_retry"] - now),
                              "total_sends": sends, "total_fails": fails,
                              "mean_fails": round(fails / sends, 2) if sends else 0,
                              "enroll": ident if not TELEMETRY_TOKEN else "legacy",
                              "enrolled": ident == "approved" or bool(TELEMETRY_TOKEN)})

    def _api_ingest_relay(self, endpoint="ingest"):
        # Las betas sin tailnet envían aquí (URL pública); se reenvía tal cual
        # al sink local, que revalida bearer/firma + allowlist. Destino fijo a
        # localhost: sin SSRF posible. Con rate-limit anti-spam.
        if rate_limited(self.client_ip()):
            return self._send_json(429, {"error": "Demasiados intentos. Espera unos minutos."})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 1_000_000:
            return self._send_json(400, {"error": "Cuerpo inválido."})
        try:
            raw = self.rfile.read(length)
            json.loads(raw.decode("utf-8"))
        except Exception:
            return self._send_json(400, {"error": "Cuerpo JSON inválido."})
        code, resp = _fwd.relay_ingest(
            raw, self.headers.get("Authorization") or "",
            os.environ.get("NEVERRED_SINK_LOCAL", "").rstrip("/") or SINK_LOCAL,
            endpoint)
        if code == 502:
            return self._send_json(502, {"error": "Receptor no disponible; se reintentará."})
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(resp)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(resp)

    def _api_enroll_relay(self):
        # Enrolamiento hacia el sink local (mismo canal que la ingesta).
        return self._api_ingest_relay("enroll")

    def _api_forward_now(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        try:
            status = forward_once(force=True)
        except Exception:
            status = {"configured": bool(TELEMETRY_SINK), "pending": -1, "sent": False}
        self._send_json(200, status)

    def _api_ping(self):
        # rate-limit (laten cada 10 s). El CORS restringido ya impide el abuso
        # desde sitios de terceros (petición JSON = preflight).
        body = self._read_json() or {}
        tab = str(body.get("tab") or "")[:64]
        if not tab:
            return self._send_json(400, {"error": "Falta el identificador de pestaña."})
        tab_seen(tab)
        self._send_json(200, {"ok": True})

    def _api_demo(self):
        # Entrar a probar sin registrarse: crea o restablece el usuario demo
        # (credenciales públicas) con datos de muestra e inicia sesión.
        if rate_limited(self.client_address[0]):
            return self._send_json(429, {"error": "Demasiados intentos. Espera unos minutos."})
        data = demo_data()
        con = db()
        user = con.execute("SELECT * FROM users WHERE email = ?", (DEMO_EMAIL,)).fetchone()
        if user is None:
            cur = con.execute(
                "INSERT INTO users (name, email, password_hash, created_at) VALUES (?,?,?,?)",
                (DEMO_NAME, DEMO_EMAIL, hash_password(DEMO_PASSWORD), int(time.time())))
            user_id = cur.lastrowid
        else:
            user_id = user["id"]
            con.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                        (hash_password(DEMO_PASSWORD), user_id))
        con.execute(
            "INSERT INTO user_data (user_id, data, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (user_id, json.dumps(data, ensure_ascii=False), int(time.time())))
        con.commit()
        user = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        con.close()
        audit("demo", user_id)
        token = self._new_session(user_id)
        self._set_session_cookie(token, SESSION_DAYS)
        self._send_json(200, {"token": token, "user": public_user(user)})

    def _api_get_data(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        con = db()
        row = con.execute(
            "SELECT data FROM user_data WHERE user_id = ?", (user["id"],)
        ).fetchone()
        con.close()
        try:
            data = json.loads(row["data"]) if row else {}
        except Exception:
            data = {}
        self._extra = {"ETag": etag_of(data)}
        self._send_json(200, {"data": data})

    def _api_put_data(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        body = self._read_json()
        err = validate_data(body)
        if err:
            return self._send_json(400, {"error": err})
        con = db()
        row = con.execute(
            "SELECT data FROM user_data WHERE user_id = ?", (user["id"],)
        ).fetchone()
        try:
            current = json.loads(row["data"]) if row else {}
        except Exception:
            current = {}
        want = self.headers.get("If-Match")
        if want and want != etag_of(current):
            con.close()
            self._extra = {"ETag": etag_of(current)}
            return self._send_json(409, {
                "error": "Tus datos cambiaron en otro lugar. Recarga o confirma sobrescribir.",
                "server": current, "etag": etag_of(current)})
        con.execute(
            "INSERT INTO user_data (user_id, data, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (user["id"], json.dumps(body, ensure_ascii=False), int(time.time())),
        )
        con.commit()
        con.close()
        self._extra = {"ETag": etag_of(body)}
        self._send_json(200, {"ok": True, "etag": etag_of(body)})

    # -- enrutado --
    def do_GET(self):  # noqa: N802 - firma de la stdlib
        path = urlparse(self.path).path
        if path == "/api/health":
            return self._send_json(200, {"ok": True, "version": VERSION})
        if path == "/api/sessions":
            return self._api_sessions()
        if path == "/api/me":
            return self._api_me()
        if path == "/api/data":
            return self._api_get_data()
        if path == "/api/telemetry-consent":
            return self._api_telemetry_consent()
        if path == "/api/telemetry-summary":
            return self._api_telemetry_summary()
        if path == "/api/telemetry-forward-status":
            return self._api_forward_status()
        if path.startswith("/api/"):
            return self._send_json(404, {"error": "Ruta no encontrada."})
        return self._serve_static(path)

    def do_POST(self):  # noqa: N802 - firma de la stdlib
        path = urlparse(self.path).path
        if path == "/api/register":
            return self._api_register()
        if path == "/api/login":
            return self._api_login()
        if path == "/api/logout":
            return self._api_logout()
        if path == "/api/password":
            return self._api_password()
        if path == "/api/reset-request":
            return self._api_reset_request()
        if path == "/api/reset-confirm":
            return self._api_reset_confirm()
        if path == "/api/sessions/rotate":
            return self._api_sessions_rotate()
        if path == "/api/ping":
            return self._api_ping()
        if path == "/api/telemetry":
            return self._api_telemetry()
        if path == "/api/telemetry-ingest":
            return self._api_ingest_relay()
        if path == "/api/telemetry-enroll":
            return self._api_enroll_relay()
        if path == "/api/telemetry-forward":
            return self._api_forward_now()
        if path == "/api/demo":
            return self._api_demo()
        return self._send_json(404, {"error": "Ruta no encontrada."})

    def do_PUT(self):  # noqa: N802 - firma de la stdlib
        path = urlparse(self.path).path
        if path == "/api/data":
            return self._api_put_data()
        if path == "/api/telemetry-consent":
            return self._api_telemetry_consent_put()
        return self._send_json(404, {"error": "Ruta no encontrada."})

    def do_DELETE(self):  # noqa: N802 - firma de la stdlib
        if urlparse(self.path).path == "/api/account":
            return self._api_delete_account()
        return self._send_json(404, {"error": "Ruta no encontrada."})

    def do_OPTIONS(self):  # noqa: N802 - firma de la stdlib
        self.send_response(204)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- estaticos --
    # Extensiones que jamás se sirven (claves, secretos, volcados de BD…),
    # aunque alguien las deje en el árbol servido al configurar TLS o backups.
    BLOCKED_EXT = {".db", ".pem", ".key", ".env"}
    BLOCKED_SUFFIX = ("-wal", "-shm", "-journal")

    def _serve_static(self, path):
        rel = "index.html" if path in ("/", "") else unquote(path).lstrip("/")
        _, ext = os.path.splitext(rel)
        if (".." in rel or rel.startswith("backend/") or rel.startswith(".git/")
                or ext.lower() in self.BLOCKED_EXT
                or rel.endswith(self.BLOCKED_SUFFIX)
                or os.path.basename(rel).startswith(".")):
            return self._send_json(403, {"error": "Acceso denegado."})
        full = os.path.join(BASE_DIR, rel)
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            full = os.path.join(BASE_DIR, "index.html")
        _, ext = os.path.splitext(full)
        ctype = MIME.get(ext, "application/octet-stream")
        try:
            with open(full, "rb") as f:
                content = f.read()
        except OSError:
            return self._send_json(404, {"error": "No encontrado."})
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        if ext == ".html":
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self'; style-src 'self' "
                             "'unsafe-inline'; img-src 'self' data:; object-src 'none'; "
                             "base-uri 'self'; frame-ancestors 'self'")
        # La BD y el estado de sesion nunca deben cachearse
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Servidor de NeverRed (API + SQLite).")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    args = parser.parse_args()
    # Los datos contables son sensibles: todo lo que cree este proceso
    # (BD, -wal, logs) nace solo-legible por el dueño.
    os.umask(0o077)
    init_db()
    for p in (DB_PATH, DB_PATH + "-wal", DB_PATH + "-shm", DB_PATH + "-journal"):
        try:
            if os.path.isfile(p):
                os.chmod(p, 0o600)
        except OSError:
            pass
    setup_logging()
    try:
        if os.environ.get("NEVERRED_LOG_FILE") and os.path.isfile(
                os.environ["NEVERRED_LOG_FILE"]):
            os.chmod(os.environ["NEVERRED_LOG_FILE"], 0o600)
    except OSError:
        pass
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    cert, key = os.environ.get("NEVERRED_TLS_CERT"), os.environ.get("NEVERRED_TLS_KEY")
    scheme = "http"
    if cert and key and os.path.isfile(cert) and os.path.isfile(key):
        import ssl
        global COOKIE_SECURE
        COOKIE_SECURE = True
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        scheme = "https"
    print("NeverRed en {}://{}:{}  (BD: {})".format(scheme, args.host, args.port, DB_PATH))
    if QUIT_WHEN_IDLE:
        threading.Thread(target=idle_sweep, args=(srv,), daemon=True).start()
        print("[neverred] Autoapagado activo: sin pestañas durante %ds." % IDLE_TIMEOUT)
    if TELEMETRY_SINK:
        threading.Thread(target=forward_loop, daemon=True).start()
        print("[neverred] Forward de telemetría activo hacia %s." % TELEMETRY_SINK)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nApagando NeverRed.")


if __name__ == "__main__":
    main()
