"""Contraseñas, usuarios públicos y validación de los datos contables."""
import hashlib
import hmac
import math
import os
import re
import secrets
from datetime import date as _date

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
# Los IDs acaban en atributos `data-*` del HTML: se restringen a un alfabeto
# seguro para que ni un backup manipulado pueda inyectar marcado.
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 256
PBKDF2_ITERATIONS = 200_000 if os.environ.get("NEVERRED_FAST_HASH") == "1" else 600_000


# ---------------- Contraseñas ----------------
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


# ---------------- Validación de datos contables ----------------
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
        if not ID_RE.match(a["id"]):
            return "Identificador de cuenta inválido: %r." % (a["id"],)
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
        if not isinstance(e.get("id"), str) or not ID_RE.match(e["id"]):
            return "Asiento sin identificador válido."
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
        if "id" in r and (not isinstance(r["id"], str) or not ID_RE.match(r["id"])):
            return "Identificador de plantilla inválido."
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
