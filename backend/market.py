"""Mercado (Herramientas): proxy Alpha Vantage y caché por símbolo.

La clave API nunca viaja al frontal ni al export: vive solo en este servidor
(variable de entorno o fichero 600 junto a la BD). Cuota gratuita: 25 req/día,
5/min → caché de 24 h por símbolo y separación mínima de 12 s entre llamadas.
"""
import json
import math
import os
import re
import threading
import time

from config import VERSION
from schema import db

MARKET_DAYS = 180
MARKET_TTL = 86400
MARKET_MIN_GAP = 12
MARKET_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-:]{1,12}$")
MARKET_KEY_RE = re.compile(r"^[A-Za-z0-9]{8,32}$")
_market_lock = threading.Lock()
_market_last_call = 0


def user_market_key(user_id):
    """Clave efectiva del usuario: la suya propia; si no tiene, el valor por
    defecto de la instalación (entorno, normalmente vacío). '' = sin clave."""
    con = db()
    try:
        row = con.execute("SELECT api_key FROM market_keys WHERE user_id = ?",
                          (user_id,)).fetchone()
    finally:
        con.close()
    if row and row["api_key"]:
        return row["api_key"]
    return (os.environ.get("NEVERRED_ALPHA_VANTAGE_KEY") or "").strip()


def set_user_market_key(user_id, key):
    """Guarda la clave propia del usuario. None = formato inválido."""
    key = (key or "").strip()
    if not MARKET_KEY_RE.match(key):
        return None
    con = db()
    try:
        con.execute("INSERT INTO market_keys (user_id, api_key) VALUES (?,?) "
                    "ON CONFLICT(user_id) DO UPDATE SET api_key=excluded.api_key",
                    (user_id, key))
        con.commit()
    finally:
        con.close()
    return True


def clear_user_market_key(user_id):
    con = db()
    try:
        con.execute("DELETE FROM market_keys WHERE user_id = ?", (user_id,))
        con.commit()
    finally:
        con.close()


def normalize_symbol(raw):
    return re.sub(r"\s+", "", str(raw or "")).upper()


def _av_get(symbol, key):
    """Una llamada a TIME_SERIES_DAILY en compact (100 sesiones, cierre de
    mercado). Dict parseado o None (red)."""
    import urllib.request
    import urllib.parse as _up
    qs = _up.urlencode({"function": "TIME_SERIES_DAILY", "symbol": symbol,
                        "outputsize": "compact", "apikey": key})
    req = urllib.request.Request("https://www.alphavantage.co/query?" + qs,
                                 headers={"User-Agent": "NeverRed/" + VERSION})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read(15_000_000)
    except Exception:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def parse_av_daily(payload, days=MARKET_DAYS):
    """Puro y testeable. Devuelve (data, err) con err en None/"invalid"/"rate".

    data = {"dates": [...], "closes": [...], "events": [...]} en ventana de
    `days` naturales; si la ventana trae <2 puntos se usa todo lo disponible.
    closes = cierre de mercado (sin ajustar: el ajustado es premium);
    events queda reservado (lista vacía con la cuota gratuita).
    """
    if not isinstance(payload, dict):
        return None, "rate"
    if "Error Message" in payload:
        return None, "invalid"
    if "Information" in payload or "Note" in payload:
        return None, "rate"
    ts = payload.get("Time Series (Daily)")
    if not isinstance(ts, dict) or not ts:
        return None, "invalid"
    pts = []
    for d, bar in sorted(ts.items()):
        if not isinstance(bar, dict):
            continue
        try:
            c = float(bar.get("4. close", ""))
        except (TypeError, ValueError):
            continue
        if math.isfinite(c):
            pts.append((d, c))
    if len(pts) < 2:
        return None, "invalid"
    import datetime as _dt
    cutoff = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
    window = [p for p in pts if p[0] >= cutoff]
    if len(window) < 2:
        window = pts[-100:] if len(pts) > 100 else pts
    return {"dates": [d for d, _ in window],
            "closes": [c for _, c in window], "events": []}, None


def rate_reason(payload):
    """Clasifica un fallo con cuota: 'net' (sin respuesta), 'burst'
    (límite por minuto/segundo: reintentar pronto sirve) o 'daily'."""
    if not isinstance(payload, dict):
        return "net"
    info = str(payload.get("Information", "") or payload.get("Note", "")).lower()
    if "minute" in info or "second" in info or "sparingly" in info:
        return "burst"
    return "daily"


def rate_message(payload):
    reason = rate_reason(payload)
    if reason == "net":
        return ("Alpha Vantage no responde (revisa tu conexión a internet "
                "y prueba de nuevo).")
    if reason == "burst":
        return ("Demasiadas peticiones seguidas a Alpha Vantage: "
                "espera un minuto y prueba de nuevo.")
    return ("Cuota diaria de Alpha Vantage agotada (25 req/día). "
            "Prueba mañana o quita algún ticker.")


def _market_stats(closes):
    first, last = closes[0], closes[-1]
    lo, hi = min(closes), max(closes)
    pct = round((last - first) / first * 100, 2) if first else 0.0
    trend = "up" if pct > 0.1 else ("down" if pct < -0.1 else "flat")
    return {"first": first, "last": last, "min": lo, "max": hi,
            "change_pct": pct, "trend": trend, "points": len(closes)}


def _market_meta(key):
    con = db()
    try:
        row = con.execute("SELECT value FROM market_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else ""
    finally:
        con.close()


def _market_set_meta(key, value):
    con = db()
    try:
        con.execute("INSERT INTO market_meta (key, value) VALUES (?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value))
        con.commit()
    finally:
        con.close()


def _av_today_key():
    import datetime as _dt
    # Aware (DeprecationWarning de utcnow en 3.12+); UTC fol usado para la
    # clave diaria de cuota.
    return "av_used_" + _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def _av_count_call():
    """Suma 1 al contador diario de llamadas (solo llamadas reales salientes)."""
    try:
        n = int(_market_meta(_av_today_key()) or 0)
    except ValueError:
        n = 0
    _market_set_meta(_av_today_key(), str(n + 1))


AV_DAILY_LIMIT = 25


def _av_quota():
    """(used, limit) de hoy según esta instalación. La serie ajustada en
    compact vale para claves gratuitas y premium: el tope mostrado es el
    de la cuota gratuita (25/día)."""
    try:
        used = int(_market_meta(_av_today_key()) or 0)
    except ValueError:
        used = 0
    return used, AV_DAILY_LIMIT


def market_history(symbol, key, force=False):
    """Serie de un símbolo. (code, obj) con code 200/404/429/502.

    Usa caché de 24 h; `force` la salta (respetando el guard de cuota).
    Ante cuota agotada sirve caché caducada marcada stale:true.
    """
    symbol = normalize_symbol(symbol)
    if not MARKET_SYMBOL_RE.match(symbol):
        return 400, {"error": "Símbolo no válido (letras, cifras y . - :)."}
    now = int(time.time())
    con = db()
    row = con.execute("SELECT fetched_at, points FROM market_cache WHERE symbol = ?",
                      (symbol,)).fetchone()
    cached = None
    if row:
        try:
            cached = json.loads(row["points"])
        except Exception:
            cached = None
    fresh = bool(row and cached and now - row["fetched_at"] < MARKET_TTL)
    if cached and fresh and not force:
        con.close()
        out = {"symbol": symbol, **_market_stats(cached["closes"]),
               "dates": cached["dates"], "closes": cached["closes"],
               "events": cached.get("events", []),
               "cached_at": row["fetched_at"], "stale": False}
        return 200, out
    global _market_last_call
    with _market_lock:
        wait = MARKET_MIN_GAP - (now - _market_last_call)
        if wait > 0:
            con.close()
            if cached:
                out = {"symbol": symbol, **_market_stats(cached["closes"]),
                       "dates": cached["dates"], "closes": cached["closes"],
                       "events": cached.get("events", []),
                       "cached_at": row["fetched_at"], "stale": True}
                return 200, out
            return 429, {"error": "Cuota de la API: reintenta en %d s." % wait}
        _market_last_call = int(time.time())
    # Ajustado en compact: 1 llamada siempre, sin sonseo premium. La cuota se
    # computa solo si la petición SALIÓ de verdad y hubo respuesta (antes se
    # sumaba también ante fallo de red/timeout, perdiendo cómputo del día).
    payload = _av_get(symbol, key)
    if payload is not None:
        _av_count_call()
    data, err = parse_av_daily(payload)
    con = db()
    if err is None:
        con.execute("INSERT INTO market_cache (symbol, fetched_at, points) VALUES (?,?,?) "
                    "ON CONFLICT(symbol) DO UPDATE SET fetched_at=excluded.fetched_at,"
                    " points=excluded.points",
                    (symbol, int(time.time()), json.dumps(data)))
        con.commit()
        con.close()
        return 200, {"symbol": symbol, **_market_stats(data["closes"]),
                     "dates": data["dates"], "closes": data["closes"],
                     "events": data.get("events", []),
                     "cached_at": int(time.time()), "stale": False}
    con.close()
    if err == "invalid":
        return 404, {"error": "No se encontró el ticker %s en Alpha Vantage." % symbol}
    if cached:
        out = {"symbol": symbol, **_market_stats(cached["closes"]),
               "dates": cached["dates"], "closes": cached["closes"],
               "events": cached.get("events", []),
               "cached_at": row["fetched_at"], "stale": True}
        return 200, out
    return 429, {"error": rate_message(payload)}
