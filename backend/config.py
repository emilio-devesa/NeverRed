"""Configuración compartida del backend (sin dependencias)."""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Ruta de la BD configurable (imprescindible para Docker/volúmenes).
DB_PATH = os.environ.get(
    "NEVERRED_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "neverred.db"),
)
VERSION = os.environ.get("NEVERRED_VERSION", "2.6.9")
