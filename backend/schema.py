"""Esquema SQLite y conexiones del backend."""
import sqlite3

from config import DB_PATH


# ---------------- Base de datos ----------------
def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    # Las FK del esquema (CASCADE) solo se aplican si se activan por conexión.
    con.execute("PRAGMA foreign_keys=ON")
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
        -- sessions.token ya es PRIMARY KEY (índice propio); el índice
        -- redundante idx_sessions_token se retiró (migración abajo).
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_resets_user ON password_resets(user_id);
        CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
        CREATE INDEX IF NOT EXISTS idx_telemetry_user ON telemetry_events(user_id);
        CREATE INDEX IF NOT EXISTS idx_telemetry_event ON telemetry_events(event, ts);
        CREATE TABLE IF NOT EXISTS market_tickers (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            symbol TEXT NOT NULL,
            added_at INTEGER NOT NULL,
            PRIMARY KEY (user_id, symbol)
        );
        CREATE TABLE IF NOT EXISTS market_cache (
            symbol TEXT PRIMARY KEY,
            fetched_at INTEGER NOT NULL,
            points TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS market_keys (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            api_key TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS user_flags (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            last_export INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS market_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        );
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
    # Migración: orden manual de tickers (los existentes, por añadido).
    cols = [r[1] for r in con.execute("PRAGMA table_info(market_tickers)")]
    if "position" not in cols:
        con.execute("ALTER TABLE market_tickers ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
        con.execute("UPDATE market_tickers SET position = added_at")
        con.commit()
    # Migración: el índice redundante idx_sessions_token (token ya es PRIMARY
    # KEY de sessions) se quitó del esquema; se limpia de BDs anteriores.
    con.execute("DROP INDEX IF EXISTS idx_sessions_token")
    con.commit()
    con.close()
