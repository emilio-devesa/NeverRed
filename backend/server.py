#!/usr/bin/env python3
"""NeverRed backend — sin dependencias externas (solo biblioteca estandar).

- Sirve la app estatica (index.html, styles.css, app.js).
- API JSON con registro/login de usuarios, sesiones con token y
  persistencia de los datos contables por usuario en SQLite.
- La configuración, el esquema, la autenticación, los estáticos, el mercado y
  la telemetría viven en módulos hermanos; este archivo es el punto de entrada.

Uso:
    python3 backend/server.py [--port 8000]

La base de datos se crea automaticamente en backend/neverred.db.
"""
import hashlib
import json
import os
import secrets
import sqlite3  # re-export histórico para tests
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from auth import (
    EMAIL_RE,
    PBKDF2_ITERATIONS,
    _DUMMY_HASH,
    hash_password,
    password_ok,
    public_user,
    validate_data,
    verify_password,
)
from config import BASE_DIR, DB_PATH, VERSION  # BASE_DIR se re-exporta para tests
from market import (
    MARKET_SYMBOL_RE,
    MARKET_TTL,
    _av_quota,
    _market_stats,
    clear_user_market_key,
    market_history,
    normalize_symbol,
    set_user_market_key,
    user_market_key,
)
from schema import db, init_db
from static import MIME, StaticMixin  # MIME se re-exporta para tests
from telemetry import (
    SINK_LOCAL,
    TELEMETRY_DAYS,
    TELEMETRY_SINK,
    TELEMETRY_TOKEN,
    _validate_telemetry,
    forward_loop,
    forward_once,
    forward_state,
    identity_status,
    relay_ingest,
)

AUDIT_DAYS = 90
SESSION_DAYS = int(os.environ.get("NEVERRED_SESSION_DAYS", "30"))
# Caducidad por inactividad: el token muere si no se usa en este plazo,
# aunque no haya llegado a SESSION_DAYS (un token robado vale menos tiempo).
SESSION_IDLE_DAYS = int(os.environ.get("NEVERRED_SESSION_IDLE_DAYS", "7"))
# Amortización de escrituras de sesión: `last_seen` se reescribe como mucho
# cada 5 min y la limpieza global cada 5 min (antes: 4 escrituras + COMMIT en
# cada petición autenticada). La lectura sigue comprobando la caducidad exacta.
SESSION_TOUCH_SECONDS = 300
SESSION_CLEANUP_SECONDS = 300
# Rate-limit anti fuerza bruta (en memoria): intentos por IP y ventana
RATE_MAX = int(os.environ.get("NEVERRED_RATE_MAX", "10"))
RATE_WINDOW = int(os.environ.get("NEVERRED_RATE_WINDOW", "600"))
# Tope de claves del mapa: sin él, una IP falseada por petición (TRUST_PROXY)
# hace crecer el diccionario sin límite. Al llenarse se rechazan claves nuevas.
RATE_MAX_KEYS = int(os.environ.get("NEVERRED_RATE_MAX_KEYS", "10000"))
_rate = {}  # ip -> [intentos, fin_ventana_ts]
_rate_lock = threading.Lock()
_rate_ops = 0


def _rate_prune(now):
    """Elimina ventanas ya caducadas. Debe llamarse con _rate_lock tomado."""
    for key in [k for k, v in _rate.items() if v[1] < now]:
        del _rate[key]


def rate_limited(ip):
    global _rate_ops
    now = int(time.time())
    with _rate_lock:
        _rate_ops += 1
        if _rate_ops >= 256:
            _rate_ops = 0
            _rate_prune(now)
        count, reset = _rate.get(ip, (0, now + RATE_WINDOW))
        if now > reset:
            count, reset = 0, now + RATE_WINDOW
        if ip not in _rate and len(_rate) >= RATE_MAX_KEYS:
            return True  # tabla llena: fallar cerrado, nunca crecer sin tope
        count += 1
        _rate[ip] = [count, reset]
        return count > RATE_MAX


def rate_reset(ip):
    with _rate_lock:
        _rate.pop(ip, None)


# Rate-limit propio de /api/ping (latido cada 10 s por pestaña). No comparte
# mapa con el de login para no consumir el cupo anti fuerza bruta.
PING_MAX = int(os.environ.get("NEVERRED_PING_MAX", "240"))
PING_WINDOW = 60
_ping = {}  # ip -> [intentos, fin_ventana_ts]
_ping_lock = threading.Lock()


def ping_limited(ip):
    now = int(time.time())
    with _ping_lock:
        if len(_ping) > RATE_MAX_KEYS:
            for key in [k for k, v in _ping.items() if v[1] < now]:
                del _ping[key]
            if len(_ping) > RATE_MAX_KEYS:
                _ping.clear()  # cota dura: el cupo de ping no es un control de acceso
        count, reset = _ping.get(ip, (0, now + PING_WINDOW))
        if now > reset:
            count, reset = 0, now + PING_WINDOW
        count += 1
        _ping[ip] = [count, reset]
        return count > PING_MAX


# Latidos de pestaña para el autoapagado (lo activa el lanzador de macOS con
# NEVERRED_QUIT_WHEN_IDLE=1): cada pestaña late cada 10 s; sin latidos durante
# IDLE_TIMEOUT segundos y tras 60 s de arranque, el servidor se apaga solo.
QUIT_WHEN_IDLE = os.environ.get("NEVERRED_QUIT_WHEN_IDLE") == "1"
IDLE_TIMEOUT = int(os.environ.get("NEVERRED_IDLE_TIMEOUT", "20"))
_tabs = {}
_tabs_lock = threading.Lock()
_had_tabs = False
_start_ts = int(time.time())

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
# Métricas por petición: activas con NEVERRED_METRICS=1 o, por defecto, cuando
# hay log a fichero. Son una línea JSON por request; nunca incluyen el cuerpo.
METRICS = os.environ.get(
    "NEVERRED_METRICS", "1" if os.environ.get("NEVERRED_LOG_FILE") else "0") == "1"


def log_metric(method, path, status, elapsed_ms, user_id=None):
    line = json.dumps({
        "event": "http",
        "method": method,
        "path": path,
        "status": status,
        "ms": round(elapsed_ms, 1),
        "user_id": user_id,
    }, ensure_ascii=False, sort_keys=True)
    if _log:
        _log.info(line)
    else:
        print(line, flush=True)


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

# Limpieza global de sesiones caducadas y auditoría vieja, como mucho cada
# SESSION_CLEANUP_SECONDS. Devuelve True si esta llamada hizo trabajo.
_last_session_cleanup = 0.0
_session_cleanup_lock = threading.Lock()


def cleanup_sessions(con, now):
    global _last_session_cleanup
    with _session_cleanup_lock:
        if now - _last_session_cleanup < SESSION_CLEANUP_SECONDS:
            return False
        _last_session_cleanup = now
    con.execute("DELETE FROM sessions WHERE expires_at <= ? OR last_seen <= ?",
                (now, now - SESSION_IDLE_DAYS * 86400))
    con.execute("DELETE FROM audit_log WHERE ts <= ?", (now - AUDIT_DAYS * 86400,))
    return True


MAX_TABS = int(os.environ.get("NEVERRED_MAX_TABS", "64"))


def tab_seen(tab):
    """Registra el latido de una pestaña (id único por sessionStorage).

    Solo se guarda si el autoapagado por inactividad está activo: sin él nadie
    lee el mapa y almacenar cada latido sería memoria sin uso (y sin cota, ya
    que /api/ping es público). Con autoapagado el uso es local y las pestañas
    son unas pocas; aun así se poda y se acota por si acaso.
    """
    global _had_tabs
    if not QUIT_WHEN_IDLE:
        _had_tabs = True
        return
    now = time.time()
    with _tabs_lock:
        if tab not in _tabs and len(_tabs) >= MAX_TABS:
            for t in [t for t, ts in _tabs.items() if now - ts > IDLE_TIMEOUT]:
                del _tabs[t]
            if len(_tabs) >= MAX_TABS:
                return  # tope duro: se ignora el latido, no se expulsa a nadie
        _tabs[tab] = now
        _had_tabs = True


def prune_tabs(now=None):
    """Elimina pestañas sin latido reciente. Devuelve cuántas quedan vivas."""
    now = now if now is not None else time.time()
    with _tabs_lock:
        for t in [t for t, ts in _tabs.items() if now - ts > IDLE_TIMEOUT]:
            del _tabs[t]
        return len(_tabs)


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
        # Rotación por tamaño: se conservan las AUDIT_MAX más recientes
        # (empate de ts resuelto por orden de inserción con rowid).
        con.execute(
            "DELETE FROM audit_log WHERE rowid NOT IN"
            " (SELECT rowid FROM audit_log ORDER BY ts DESC, rowid DESC LIMIT ?)",
            (AUDIT_MAX,))
        con.commit()
        con.close()
    except Exception:
        pass


APP_URL = os.environ.get("NEVERRED_APP_URL", "http://127.0.0.1:8000").rstrip("/")
RESET_TTL = int(os.environ.get("NEVERRED_RESET_TTL", "3600"))
SESSION_COOKIE = "nr_session"
COOKIE_SECURE = False  # se activa al servir por TLS (ver main)
# Tope de audit_log por tamaño (además de la purga por antigüedad AUDIT_DAYS):
# los login_fail entre purgas no deben poder engordar la BD sin límite (B-8).
AUDIT_MAX = max(100, int(os.environ.get("NEVERRED_AUDIT_MAX", "5000")))


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


# ---------------- Servidor HTTP ----------------
class Handler(StaticMixin, BaseHTTPRequestHandler):
    server_version = "NeverRed/" + VERSION
    # Sin «Python/x.y» en Server: no aporta y sí revela el entorno
    # (mismo criterio que el relay y el sink; B-12). Se redefine
    # version_string() para además no dejar el espacio colgante de
    # «server_version + ' ' + sys_version» cuando sys_version es vacío.
    sys_version = ""

    def version_string(self):
        return (self.server_version or "").strip()
    # HTTP/1.1 permite keep-alive; todas las respuestas van con Content-Length
    # (o son 204/304 sin cuerpo) para que el framing sea inequívoco.
    protocol_version = "HTTP/1.1"
    # Corta clientes que no terminan de enviar cabeceras/cuerpo (slowloris).
    timeout = 15

    def log_message(self, fmt, *args):  # noqa: A002 - firma de la stdlib
        if args and "/api/ping" in str(args[0]):
            return  # los latidos cada 10 s no ensucian el log
        line = "[neverred] " + fmt % args
        if _log:
            _log.info(line)
        else:
            print(line, flush=True)

    def handle_one_request(self):
        # La instancia se reutiliza en keep-alive: el estado de una respuesta
        # (cookie, ETag extra, HEAD) no debe filtrarse a la siguiente.
        self._cookie = None
        self._extra = None
        self._head = False
        self._body_read = False
        self._headers_started = False
        self._status_code = 0
        self._user_id = None
        started = time.perf_counter()
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            # Un fallo no controlado no debe dejar una respuesta a medias: si
            # aún no se enviaron cabeceras, devolvemos un 500 JSON con framing;
            # si ya empezaron, lo único seguro es cerrar la conexión.
            traceback.print_exc()
            if not getattr(self, "_headers_started", False):
                self._body_read = True
                self.close_connection = True
                try:
                    self._send_json(500, {"error": "Error interno."})
                except Exception:
                    self.close_connection = True
            else:
                self.close_connection = True
        finally:
            if METRICS and self.command and self.path and self._status_code:
                path = urlparse(self.path).path
                if path != "/api/ping":
                    log_metric(self.command, path, self._status_code,
                               (time.perf_counter() - started) * 1000,
                               self._user_id)

    def send_response_only(self, code, message=None):
        # Marca el punto de no retorno: a partir de aquí no se puede sustituir
        # la respuesta por un 500 sin corromper el framing.
        self._headers_started = True
        self._status_code = code
        super().send_response_only(code, message)

    def _accepts_gzip(self):
        for part in (self.headers.get("Accept-Encoding") or "").split(","):
            name, _, params = part.strip().partition(";")
            if name.strip().lower() not in ("gzip", "*"):
                continue
            q = 1.0
            for p in params.split(";"):
                p = p.strip()
                if p.startswith("q="):
                    try:
                        q = float(p[2:])
                    except ValueError:
                        q = 0.0
            if q > 0:
                return True
        return False

    @staticmethod
    def _etag_matches(header, etag):
        # Igualdad exacta o validador débil con prefijo real "W/" (no un
        # lstrip de caracteres, que casaría con p. ej. "WoW/etag").
        for tag in (header or "").split(","):
            tag = tag.strip()
            if tag == "*" or tag == etag or tag == "W/" + etag:
                return True
        return False

    def _discard_body(self):
        """Consume un cuerpo pequeño que el handler no llegó a leer.

        Sin esto, en keep-alive el cuerpo pendiente se interpretaría como la
        siguiente petición. Los cuerpos enormes no se drenan: se cierra.
        """
        if self._body_read:
            return
        try:
            pending = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            pending = 0
        if pending <= 0:
            self._body_read = True
            return
        if pending > 1_000_000:
            self.close_connection = True
            return
        remaining = pending
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                break
            remaining -= len(chunk)
        self._body_read = True

    def client_ip(self):
        if os.environ.get("NEVERRED_TRUST_PROXY") == "1":
            fwd = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if fwd:
                return fwd
        return self.client_address[0]

    # -- utilidades --
    def _own_origin(self):
        """Origen exacto de este servidor (esquema + host + puerto del Host)."""
        host = (self.headers.get("Host") or "").strip()
        if not host:
            return None
        proto = ""
        if os.environ.get("NEVERRED_TRUST_PROXY") == "1":
            proto = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        if proto not in ("http", "https"):
            proto = "https" if COOKIE_SECURE else "http"
        return proto + "://" + host

    def _cors_headers(self):
        # Solo CORS al propio origen (comparación exacta, puerto incluido) y al
        # modo archivo local (Origin: null, que usa Bearer y nunca cookies).
        # Cualquier otro origen no recibe cabeceras y el navegador lo bloquea.
        origin = self.headers.get("Origin") or ""
        allowed = None
        if origin == "null":
            allowed = "null"
        elif origin:
            if origin == self._own_origin():
                allowed = origin
        if allowed:
            self.send_header("Access-Control-Allow-Origin", allowed)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, PUT, DELETE, OPTIONS")
            # Navegador cachea el preflight (por defecto lo repetiría cada
            # vez): un día de caches quita miles de preflights.
            self.send_header("Access-Control-Max-Age", "86400")

    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._cors_headers()
        for k, v in (getattr(self, "_extra", None) or {}).items():
            self.send_header(k, v)
        if getattr(self, "_cookie", None):
            self.send_header("Set-Cookie", self._cookie)
        if not self._body_read:
            self._discard_body()
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if not getattr(self, "_head", False):
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
        if length <= 0:
            self._body_read = True
            return None
        if length > 10_000_000:
            return None
        try:
            raw = self.rfile.read(length)
            self._body_read = True
            return json.loads(raw.decode("utf-8"))
        except Exception:
            self._body_read = True
            return None

    def _auth_user(self):
        token = self._current_token()
        if not token:
            return None
        now = int(time.time())
        con = db()
        row = con.execute(
            "SELECT u.*, s.last_seen AS session_last_seen "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token = ? AND s.expires_at > ? AND s.last_seen > ?",
            (token, now, now - SESSION_IDLE_DAYS * 86400),
        ).fetchone()
        if row is not None:
            self._user_id = row["id"]
            changed = False
            if now - row["session_last_seen"] >= SESSION_TOUCH_SECONDS:
                con.execute("UPDATE sessions SET last_seen = ? WHERE token = ?", (now, token))
                changed = True
            if cleanup_sessions(con, now):
                changed = True
            if changed:
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
        try:
            cur = con.execute(
                "INSERT INTO users (name, email, password_hash, created_at) VALUES (?,?,?,?)",
                (name, email, hash_password(password), int(time.time())),
            )
            user_id = cur.lastrowid
            con.execute(
                "INSERT INTO user_data (user_id, data, updated_at) VALUES (?, '{}', ?)",
                (user_id, int(time.time())),
            )
            con.execute("INSERT INTO user_flags (user_id, last_export) VALUES (?,?) "
                        "ON CONFLICT(user_id) DO NOTHING",
                        (user_id, int(time.time())))
        except sqlite3.IntegrityError:
            # Carrera: otro hilo registró el mismo email entre el check y el
            # INSERT (el índice UNIQUE manda). Mismo mensaje que el check.
            con.rollback()
            con.close()
            return self._send_json(409, {"error": "Ese correo ya está registrado. Inicia sesión."})
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
        con = db()
        row = con.execute("SELECT last_export FROM user_flags WHERE user_id = ?",
                          (user["id"],)).fetchone()
        con.close()
        self._send_json(200, {"user": public_user(user),
                              "last_export": row["last_export"] if row else 0})

    def _api_export_log(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        con = db()
        con.execute("INSERT INTO user_flags (user_id, last_export) VALUES (?,?) "
                    "ON CONFLICT(user_id) DO UPDATE SET last_export=excluded.last_export",
                    (user["id"], int(time.time())))
        con.commit()
        con.close()
        self._send_json(200, {"ok": True})

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
        con.execute("DELETE FROM market_tickers WHERE user_id = ?", (user["id"],))
        con.execute("DELETE FROM market_keys WHERE user_id = ?", (user["id"],))
        con.execute("DELETE FROM user_flags WHERE user_id = ?", (user["id"],))
        con.execute("DELETE FROM telemetry_events WHERE user_id = ?", (user["id"],))
        con.execute("DELETE FROM telemetry_consent WHERE user_id = ?", (user["id"],))
        con.execute("DELETE FROM users WHERE id = ?", (user["id"],))
        # "Eliminar mi cuenta" debe borrar su estela también del registro de
        # auditoría (dura 90 días): se anonimiza (user_id a NULL, detalle
        # vacío — p. ej. el email del "register") conservando el contador de
        # hechos para la métrica global.
        con.execute("UPDATE audit_log SET user_id = NULL, detail = ''"
                    " WHERE user_id = ?", (user["id"],))
        con.commit()
        con.close()
        audit("account_delete")
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
        ident = identity_status()
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
            self._body_read = True
            json.loads(raw.decode("utf-8"))
        except Exception:
            return self._send_json(400, {"error": "Cuerpo JSON inválido."})
        code, resp = relay_ingest(
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
        # Latido de pestaña (cada 10 s). Con rate-limit propio y generoso: es
        # público, así que sin tope sirve para inundar el proceso.
        if ping_limited(self.client_ip()):
            return self._send_json(429, {"error": "Demasiados latidos. Espera un momento."})
        body = self._read_json() or {}
        tab = str(body.get("tab") or "")[:64]
        if not tab:
            return self._send_json(400, {"error": "Falta el identificador de pestaña."})
        tab_seen(tab)
        self._send_json(200, {"ok": True})

    def _api_demo(self):
        # Entrar a probar sin registrarse: crea o restablece el usuario demo
        # (credenciales públicas) con datos de muestra e inicia sesión.
        if rate_limited(self.client_ip()):
            return self._send_json(429, {"error": "Demasiados intentos. Espera unos minutos."})
        data = demo_data()
        con = db()
        user = con.execute("SELECT * FROM users WHERE email = ?", (DEMO_EMAIL,)).fetchone()
        if user is None:
            try:
                cur = con.execute(
                    "INSERT INTO users (name, email, password_hash, created_at) VALUES (?,?,?,?)",
                    (DEMO_NAME, DEMO_EMAIL, hash_password(DEMO_PASSWORD), int(time.time())))
                user_id = cur.lastrowid
            except sqlite3.IntegrityError:
                # Carrera: otro hilo creó el demo a la vez; se reutiliza el suyo.
                con.rollback()
                user = con.execute("SELECT * FROM users WHERE email = ?",
                                   (DEMO_EMAIL,)).fetchone()
                if user is None:
                    con.close()
                    raise
                user_id = user["id"]
                con.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                            (hash_password(DEMO_PASSWORD), user_id))
        else:
            user_id = user["id"]
            con.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                        (hash_password(DEMO_PASSWORD), user_id))
        con.execute(
            "INSERT INTO user_data (user_id, data, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (user_id, json.dumps(data, ensure_ascii=False), int(time.time())))
        # La demo es efímera: ni tickers ni clave sobreviven al restablecido.
        con.execute("DELETE FROM market_tickers WHERE user_id = ?", (user_id,))
        con.execute("DELETE FROM market_keys WHERE user_id = ?", (user_id,))
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
        cur_etag = etag_of(current)
        want = self.headers.get("If-Match")
        if want and want != cur_etag:
            con.close()
            self._extra = {"ETag": cur_etag}
            return self._send_json(409, {
                "error": "Tus datos cambiaron en otro lugar. Recarga o confirma sobrescribir.",
                "server": current, "etag": cur_etag})
        # Serialización canónica única: el mismo texto sirve para el hash y
        # para guardar (el orden de claves JSON no cambia el contenido).
        payload = json.dumps(body, ensure_ascii=False, sort_keys=True)
        new_etag = hashlib.sha256(payload.encode()).hexdigest()[:32]
        con.execute(
            "INSERT INTO user_data (user_id, data, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (user["id"], payload, int(time.time())),
        )
        con.commit()
        con.close()
        self._extra = {"ETag": new_etag}
        self._send_json(200, {"ok": True, "etag": new_etag})

    def _api_market_status(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        con = db()
        n = con.execute("SELECT COUNT(*) FROM market_tickers WHERE user_id = ?",
                        (user["id"],)).fetchone()[0]
        con.close()
        # Ojo: jamás se devuelve la clave, solo si hay alguna configurada.
        used, limit = _av_quota()
        self._send_json(200, {"configured": bool(user_market_key(user["id"])),
                              "tickers": n,
                              "quota": None if used is None
                              else {"used": used, "limit": limit}})

    def _api_market_key(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        body = self._read_json()
        if not body:
            return self._send_json(400, {"error": "Cuerpo JSON inválido."})
        if set_user_market_key(user["id"], body.get("key")) is None:
            return self._send_json(400, {"error": "Clave no válida."})
        audit("market_key", user["id"])
        self._send_json(200, {"ok": True, "configured": True})

    def _api_market_key_del(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        clear_user_market_key(user["id"])
        audit("market_key_del", user["id"])
        self._send_json(200, {"ok": True, "configured": False})

    def _api_market_tickers(self):
        # Lista sin gastar cuota: usa solo la caché (last null = aún sin datos).
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        con = db()
        rows = con.execute(
            "SELECT t.symbol, t.added_at, c.fetched_at, c.points FROM market_tickers t "
            "LEFT JOIN market_cache c ON c.symbol = t.symbol "
            "WHERE t.user_id = ? ORDER BY t.position, t.added_at", (user["id"],)).fetchall()
        con.close()
        out = []
        for symbol, added_at, fetched_at, points in rows:
            item = {"symbol": symbol, "added_at": added_at, "last": None,
                    "change_pct": None, "trend": None, "cached_at": fetched_at,
                    "stale": True}
            try:
                data = json.loads(points) if points else None
                if data and data.get("closes"):
                    item.update(_market_stats(data["closes"]))
                    item["events"] = data.get("events", [])
                    item["cached_at"] = fetched_at
                    item["stale"] = (int(time.time()) - (fetched_at or 0)) > MARKET_TTL
            except Exception:
                pass
            out.append(item)
        self._send_json(200, {"tickers": out})

    def _api_market_add(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        body = self._read_json()
        if not body:
            return self._send_json(400, {"error": "Cuerpo JSON inválido."})
        symbol = normalize_symbol(body.get("symbol"))
        if not MARKET_SYMBOL_RE.match(symbol):
            return self._send_json(400, {"error": "Símbolo no válido (p. ej. AAPL, GOOG)."})
        key = user_market_key(user["id"])
        if not key:
            return self._send_json(400, {"error": "Configura primero tu clave de Alpha Vantage."})
        code, obj = market_history(symbol, key)
        if code != 200:
            return self._send_json(code, obj)
        con = db()
        row = con.execute("SELECT COALESCE(MAX(position), 0) FROM market_tickers "
                          "WHERE user_id = ?", (user["id"],)).fetchone()
        con.execute("INSERT INTO market_tickers (user_id, symbol, added_at, position)"
                    " VALUES (?,?,?,?) "
                    "ON CONFLICT(user_id, symbol) DO NOTHING",
                    (user["id"], symbol, int(time.time()), (row[0] or 0) + 1))
        con.commit()
        con.close()
        audit("market_add", user["id"], symbol)
        self._send_json(200, {"ok": True, "history": obj})

    def _api_market_order(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        body = self._read_json()
        order = body.get("order") if isinstance(body, dict) else None
        if (not isinstance(order, list) or not order
                or any(not MARKET_SYMBOL_RE.match(normalize_symbol(s)) for s in order)):
            return self._send_json(400, {"error": "Orden no válido."})
        symbols = [normalize_symbol(s) for s in order]
        if len(set(symbols)) != len(symbols):
            return self._send_json(400, {"error": "Orden no válido."})
        con = db()
        mine = {r[0] for r in con.execute(
            "SELECT symbol FROM market_tickers WHERE user_id = ?", (user["id"],))}
        con.close()
        if set(symbols) != mine:
            # Ni de más (de otro usuario) ni de menos: el orden cubre los míos.
            return self._send_json(400, {"error": "Orden no válido."})
        con = db()
        con.executemany("UPDATE market_tickers SET position = ? "
                        "WHERE user_id = ? AND symbol = ?",
                        [(i, user["id"], s) for i, s in enumerate(symbols)])
        con.commit()
        con.close()
        audit("market_order", user["id"], "n=%d" % len(symbols))
        self._send_json(200, {"ok": True})

    def _api_market_del(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        qs = parse_qs(urlparse(self.path).query)
        symbol = normalize_symbol((qs.get("symbol") or [""])[0])
        con = db()
        con.execute("DELETE FROM market_tickers WHERE user_id = ? AND symbol = ?",
                    (user["id"], symbol))
        con.commit()
        con.close()
        audit("market_del", user["id"], symbol)
        self._send_json(200, {"ok": True})

    def _api_market_history(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesión no válida."})
        qs = parse_qs(urlparse(self.path).query)
        symbol = normalize_symbol((qs.get("symbol") or [""])[0])
        force = (qs.get("refresh") or [""])[0] == "1"
        key = user_market_key(user["id"])
        if not key:
            return self._send_json(400, {"error": "Configura primero tu clave de Alpha Vantage."})
        code, obj = market_history(symbol, key, force=force)
        self._send_json(code, obj)

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
        if path == "/api/market/status":
            return self._api_market_status()
        if path == "/api/market/tickers":
            return self._api_market_tickers()
        if path == "/api/market/history":
            return self._api_market_history()
        if path == "/api/telemetry-consent":
            return self._api_telemetry_consent()
        if path == "/api/telemetry-summary":
            return self._api_telemetry_summary()
        if path == "/api/telemetry-forward-status":
            return self._api_forward_status()
        if path.startswith("/api/"):
            return self._send_json(404, {"error": "Ruta no encontrada."})
        return self._serve_static(path)

    def do_HEAD(self):  # noqa: N802 - firma de la stdlib
        # HEAD = GET sin cuerpo, pero con las mismas cabeceras (incluido
        # Content-Length). _send_json y _serve_static respetan self._head.
        self._head = True
        try:
            self.do_GET()
        finally:
            self._head = False

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
        if path == "/api/export-log":
            return self._api_export_log()
        if path == "/api/market/key":
            return self._api_market_key()
        if path == "/api/market/tickers":
            return self._api_market_add()
        return self._send_json(404, {"error": "Ruta no encontrada."})

    def do_PUT(self):  # noqa: N802 - firma de la stdlib
        path = urlparse(self.path).path
        if path == "/api/data":
            return self._api_put_data()
        if path == "/api/market/order":
            return self._api_market_order()
        if path == "/api/telemetry-consent":
            return self._api_telemetry_consent_put()
        return self._send_json(404, {"error": "Ruta no encontrada."})

    def do_DELETE(self):  # noqa: N802 - firma de la stdlib
        if urlparse(self.path).path == "/api/account":
            return self._api_delete_account()
        if urlparse(self.path).path == "/api/market/key":
            return self._api_market_key_del()
        if urlparse(self.path).path == "/api/market/tickers":
            return self._api_market_del()
        return self._send_json(404, {"error": "Ruta no encontrada."})

    def end_headers(self):  # noqa: N802 - firma de la stdlib
        # Privacidad, en un solo punto para TODA respuesta (API, estáticos,
        # OPTIONS y relays): la app es local y no debe fugarse nada por
        # referer; los permisos del navegador se cierran explícitamente.
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy",
                         "camera=(), microphone=(), geolocation=()")
        super().end_headers()

    def do_OPTIONS(self):  # noqa: N802 - firma de la stdlib
        # 204 no lleva cuerpo ni Content-Length (RFC 9110 §8.6).
        self.send_response(204)
        self._cors_headers()
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()

class Server(ThreadingHTTPServer):
    # Cada conexión tiene su hilo; daemon para que un cliente lento no impida
    # cerrar el proceso (el timeout de Handler corta la conexión a los 15 s).
    daemon_threads = True
    # Backlog por defecto de TCPServer (5) se queda corto en una ráfaga real
    # (p. ej. pestañas arrancando a la vez o reintentos simultáneos): los
    # connects que no cabían en la cola morían con «Connection refused»
    # sin razón visible y sin registro en el servidor.
    request_queue_size = 128


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
    srv = Server((args.host, args.port), Handler)
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
