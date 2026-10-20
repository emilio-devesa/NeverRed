"""Estáticos de la app: lista blanca, caché, ETag/gzip y respuestas HTTP."""
import gzip
import hashlib
import os
import threading
from email.utils import formatdate, mktime_tz, parsedate_tz
from urllib.parse import unquote

from config import BASE_DIR

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}

# Caché de estáticos: se relee solo si cambia (mtime+tamaño). El gzip se
# calcula una vez por versión del archivo y se guarda junto al original.
_STATIC_CACHE = {}
_STATIC_CACHE_LOCK = threading.Lock()
GZIP_MIN_BYTES = 1024

# Lista blanca: solo se sirve lo que la app necesita. Todo lo demás
# (markdown, informes, scripts de empaquetado, CI, tests…) queda fuera por
# defecto, aunque esté en el árbol o dentro de la imagen Docker.
STATIC_FILES = {
    "index.html", "styles.css", "app.js", "sw.js",
    "icon.svg", "manifest.webmanifest",
}
STATIC_DIRS = ("lib/", "assets/")
STATIC_EXT = {".css", ".js", ".json", ".svg", ".png", ".ico", ".webmanifest",
              ".jpg", ".jpeg", ".webp", ".woff", ".woff2"}


def _compressible(ctype):
    return (ctype.startswith("text/") or
            ctype.startswith("application/javascript") or
            ctype.startswith("application/json") or
            ctype.startswith("application/manifest") or
            ctype.startswith("image/svg"))


def _static_asset(rel):
    """Entrada cacheada de un estático: bytes, gzip perezoso, ETag y fecha."""
    full = os.path.join(BASE_DIR, rel)
    st = os.stat(full)
    key = (st.st_mtime_ns, st.st_size)
    with _STATIC_CACHE_LOCK:
        entry = _STATIC_CACHE.get(rel)
        if entry and entry["key"] == key:
            return entry
        with open(full, "rb") as f:
            raw = f.read()
        ext = os.path.splitext(full)[1].lower()
        entry = {
            "key": key,
            "raw": raw,
            "gzip": None,
            "etag": '"' + hashlib.sha256(raw).hexdigest()[:32] + '"',
            "last_modified": formatdate(st.st_mtime, usegmt=True),
            "mtime": int(st.st_mtime),
            "ctype": MIME.get(ext, "application/octet-stream"),
        }
        _STATIC_CACHE[rel] = entry
        return entry


class StaticMixin:
    """Métodos de servido de estáticos; se mezclan en el Handler HTTP."""

    def _static_rel(self, path):
        """Ruta relativa servible, o None si no está en la lista blanca."""
        rel = "index.html" if path in ("/", "") else unquote(path).lstrip("/")
        if not rel or ".." in rel.split("/") or "\\" in rel:
            return None
        if os.path.basename(rel).startswith("."):
            return None
        if rel in STATIC_FILES:
            return rel
        if rel.startswith(STATIC_DIRS) and \
                os.path.splitext(rel)[1].lower() in STATIC_EXT:
            return rel
        return None

    def _serve_static(self, path):
        rel = self._static_rel(path)
        if rel is None:
            raw = unquote(path).lstrip("/")
            if ".." in raw or raw.startswith(("backend/", ".git/")):
                return self._send_json(403, {"error": "Acceso denegado."})
            return self._send_json(404, {"error": "No encontrado."})
        full = os.path.join(BASE_DIR, rel)
        if not os.path.isfile(full):
            return self._send_json(404, {"error": "No encontrado."})
        try:
            entry = _static_asset(rel)
        except OSError:
            return self._send_json(404, {"error": "No encontrado."})
        ctype = entry["ctype"]
        etag = entry["etag"]
        compressible = _compressible(ctype)
        is_html = ctype.startswith("text/html")

        def common_headers():
            self.send_header("Content-Type", ctype)
            self.send_header("ETag", etag)
            self.send_header("Last-Modified", entry["last_modified"])
            # Revalidar siempre: el ETag abarata el 304, pero nunca se sirve
            # una carcasa vieja sin preguntar (el SW y ensureFreshShell ayudan).
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            if compressible:
                self.send_header("Vary", "Accept-Encoding")
            if is_html:
                self.send_header("Content-Security-Policy",
                                 "default-src 'self'; script-src 'self'; style-src 'self'; "
                                 "img-src 'self' data:; object-src 'none'; "
                                 "base-uri 'self'; frame-ancestors 'self'")
            if self.close_connection:
                self.send_header("Connection", "close")

        # Un GET con cuerpo (raro) no debe dejar bytes que se interpreten como
        # la siguiente petición keep-alive.
        self._discard_body()

        not_modified = False
        inm = self.headers.get("If-None-Match")
        if inm:
            not_modified = self._etag_matches(inm, etag)
        else:
            ims = self.headers.get("If-Modified-Since")
            if ims:
                parsed = parsedate_tz(ims)
                if parsed:
                    try:
                        not_modified = entry["mtime"] <= int(mktime_tz(parsed))
                    except (ValueError, OverflowError):
                        not_modified = False
        if not_modified:
            self.send_response(304)
            common_headers()
            self.end_headers()  # 304 no lleva cuerpo ni Content-Length
            return

        body = entry["raw"]
        gzipped = False
        if compressible and len(body) >= GZIP_MIN_BYTES and self._accepts_gzip():
            with _STATIC_CACHE_LOCK:
                if entry["gzip"] is None:
                    entry["gzip"] = gzip.compress(body, 9)
                body = entry["gzip"]
            gzipped = True
        self.send_response(200)
        common_headers()
        if gzipped:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not getattr(self, "_head", False):
            self.wfile.write(body)
