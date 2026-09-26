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
import os
import re
import secrets
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Ruta de la BD configurable (imprescindible para Docker/volúmenes)
DB_PATH = os.environ.get(
    "NEVERRED_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "neverred.db"),
)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
MIN_PASSWORD_LEN = 8
SESSION_DAYS = 30
PBKDF2_ITERATIONS = 200_000
VERSION = os.environ.get("NEVERRED_VERSION", "1.0.0")
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

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


# ---------------- Base de datos ----------------
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = db()
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
        """
    )
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


def public_user(row):
    return {"id": row["id"], "name": row["name"], "email": row["email"]}


# ---------------- Servidor HTTP ----------------
class Handler(BaseHTTPRequestHandler):
    server_version = "NeverRed/1.0"

    def log_message(self, fmt, *args):  # noqa: A002 - firma de la stdlib
        print("[neverred] " + fmt % args)

    # -- utilidades --
    def _cors_headers(self):
        # Permite que la app funcione incluso abierta como archivo local (file://)
        # apuntando a http://127.0.0.1:8000. App personal local: riesgo minimo.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")

    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

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
        auth = self.headers.get("Authorization") or ""
        if not auth.startswith("Bearer "):
            return None
        token = auth[len("Bearer "):].strip()
        if not token:
            return None
        con = db()
        row = con.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token = ? AND s.expires_at > ?",
            (token, int(time.time())),
        ).fetchone()
        con.close()
        return row

    def _new_session(self, user_id):
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        con = db()
        con.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, user_id, now, now + SESSION_DAYS * 86400),
        )
        # Limpieza oportunista de sesiones caducadas
        con.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        con.commit()
        con.close()
        return token

    # -- API --
    def _api_register(self):
        if rate_limited(self.client_address[0]):
            return self._send_json(429, {"error": "Demasiados intentos. Espera unos minutos."})
        body = self._read_json()
        if not body:
            return self._send_json(400, {"error": "Cuerpo JSON invalido."})
        name = str(body.get("name") or "").strip()[:40] or "contable"
        email = str(body.get("email") or "").strip().lower()
        password = str(body.get("password") or "")
        if not EMAIL_RE.match(email):
            return self._send_json(400, {"error": "Correo electronico no valido."})
        if len(password) < MIN_PASSWORD_LEN:
            return self._send_json(
                400, {"error": "La contrasena debe tener al menos 8 caracteres."}
            )
        con = db()
        if con.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            con.close()
            return self._send_json(409, {"error": "Ese correo ya esta registrado. Inicia sesion."})
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
        token = self._new_session(user_id)
        self._send_json(201, {"token": token, "user": public_user(user)})

    def _api_login(self):
        if rate_limited(self.client_address[0]):
            return self._send_json(429, {"error": "Demasiados intentos. Espera unos minutos."})
        body = self._read_json()
        if not body:
            return self._send_json(400, {"error": "Cuerpo JSON invalido."})
        email = str(body.get("email") or "").strip().lower()
        password = str(body.get("password") or "")
        con = db()
        user = con.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        con.close()
        if user is None or not verify_password(password, user["password_hash"]):
            return self._send_json(401, {"error": "Correo o contrasena incorrectos."})
        rate_reset(self.client_address[0])
        token = self._new_session(user["id"])
        self._send_json(200, {"token": token, "user": public_user(user)})

    def _api_password(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesion no valida."})
        body = self._read_json()
        if not body:
            return self._send_json(400, {"error": "Cuerpo JSON invalido."})
        current = str(body.get("current") or "")
        new = str(body.get("new") or "")
        if not verify_password(current, user["password_hash"]):
            return self._send_json(403, {"error": "La contrasena actual no es correcta."})
        if len(new) < MIN_PASSWORD_LEN:
            return self._send_json(
                400, {"error": "La nueva contrasena debe tener al menos 8 caracteres."}
            )
        auth = self.headers.get("Authorization") or ""
        mine = auth[len("Bearer "):].strip() if auth.startswith("Bearer ") else ""
        con = db()
        con.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                    (hash_password(new), user["id"]))
        # Cierra las demás sesiones (la actual sigue válida)
        con.execute("DELETE FROM sessions WHERE user_id = ? AND token != ?",
                    (user["id"], mine))
        con.commit()
        con.close()
        self._send_json(200, {"ok": True})

    def _api_logout(self):
        auth = self.headers.get("Authorization") or ""
        token = auth[len("Bearer "):].strip() if auth.startswith("Bearer ") else ""
        if token:
            con = db()
            con.execute("DELETE FROM sessions WHERE token = ?", (token,))
            con.commit()
            con.close()
        self._send_json(200, {"ok": True})

    def _api_me(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesion no valida."})
        self._send_json(200, {"user": public_user(user)})

    def _api_get_data(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesion no valida."})
        con = db()
        row = con.execute(
            "SELECT data FROM user_data WHERE user_id = ?", (user["id"],)
        ).fetchone()
        con.close()
        try:
            data = json.loads(row["data"]) if row else {}
        except Exception:
            data = {}
        self._send_json(200, {"data": data})

    def _api_put_data(self):
        user = self._auth_user()
        if user is None:
            return self._send_json(401, {"error": "Sesion no valida."})
        body = self._read_json()
        if not isinstance(body, dict):
            return self._send_json(400, {"error": "Cuerpo JSON invalido."})
        # Validacion minima de forma (la validacion contable vive en el frontend)
        for key in ("accounts", "entries"):
            if key in body and not isinstance(body[key], list):
                return self._send_json(400, {"error": "Formato de datos invalido."})
        con = db()
        con.execute(
            "INSERT INTO user_data (user_id, data, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (user["id"], json.dumps(body, ensure_ascii=False), int(time.time())),
        )
        con.commit()
        con.close()
        self._send_json(200, {"ok": True})

    # -- enrutado --
    def do_GET(self):  # noqa: N802 - firma de la stdlib
        path = urlparse(self.path).path
        if path == "/api/health":
            return self._send_json(200, {"ok": True, "version": "1.0"})
        if path == "/api/me":
            return self._api_me()
        if path == "/api/data":
            return self._api_get_data()
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
        return self._send_json(404, {"error": "Ruta no encontrada."})

    def do_PUT(self):  # noqa: N802 - firma de la stdlib
        if urlparse(self.path).path == "/api/data":
            return self._api_put_data()
        return self._send_json(404, {"error": "Ruta no encontrada."})

    def do_OPTIONS(self):  # noqa: N802 - firma de la stdlib
        self.send_response(204)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- estaticos --
    def _serve_static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        if ".." in rel or rel.startswith("backend/") or rel.startswith(".git/"):
            return self._send_json(403, {"error": "Acceso denegado."})
        if rel.endswith(".db"):
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
    init_db()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("NeverRed en http://{}:{}  (BD: {})".format(args.host, args.port, DB_PATH))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nApagando NeverRed.")


if __name__ == "__main__":
    main()
