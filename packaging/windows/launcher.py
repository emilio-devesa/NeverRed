"""NeverRed — lanzador de Windows (y reutilizable en macOS/Linux para pruebas).

Es el equivalente de packaging/macos/Launcher.sh: prepara los datos, comprueba
actualizaciones, arranca el servidor como hijo y abre el navegador.

Modos:
  (por defecto)  datos + update + servidor hijo + navegador + esperar
  --serve        ejecuta el servidor en este proceso (lo invoca el modo por defecto)
  --version      imprime la versión de backend/config.py y sale
  --no-browser   (pruebas) no abre el navegador
  --no-update    (pruebas) salta la comprobación de actualizaciones

Solo stdlib. La ruta de datos en Windows es %APPDATA%\\NeverRed (decisión de la
fase Windows nivel 2); el servidor la recibe por entorno, sin tocar
backend/config.py, igual que hace el Launcher.sh de macOS.
"""
import os
import re
import subprocess
import sys
import time
import urllib.request

# ── Imports para el hijo --serve en el exe congelado ──────────────────
# PyInstaller solo embebe en el .exe lo que ESTE módulo importa estáticamente.
# En modo --serve el hijo carga server.py (y los otros módulos backend) desde
# disco; sus imports (esta unión, calculada sobre los 9 módulos backend)
# tienen que venir embebidos en el exe o fallarán con ModuleNotFoundError
# (p. ej. "No module named 'json'" observado en Windows 11).
# Los módulos ya importados arriba no se repiten; socket/socketserver entren
# solos por ser dependencias de http.server.
import argparse  # noqa: F401
import calendar  # noqa: F401
import datetime  # noqa: F401
import email.message  # noqa: F401
import email.utils  # noqa: F401
import gzip  # noqa: F401
import hashlib  # noqa: F401
import hmac  # noqa: F401
import http.server  # noqa: F401
import json  # noqa: F401
import logging  # noqa: F401
import logging.handlers  # noqa: F401
import math  # noqa: F401
import random  # noqa: F401
import secrets  # noqa: F401
import shutil  # noqa: F401
import smtplib  # noqa: F401
import sqlite3  # noqa: F401
import ssl  # noqa: F401
import tempfile  # noqa: F401
import threading  # noqa: F401
import traceback  # noqa: F401
import urllib.error  # noqa: F401
import urllib.parse  # noqa: F401

CREATE_NO_WINDOW = 0x08000000   # solo Windows
DETACHED_PROCESS = 0x00000008   # solo Windows
UPDATED_EXIT = 42               # como check-update.sh: ya se instaló y reabrió la nueva


def is_frozen():
    """True cuando corre como exe de PyInstaller."""
    return getattr(sys, "frozen", False)


def app_root():
    """Raíz de la instalación:
      - exe de PyInstaller: junto al propio .exe;
      - portable/repo: sube buscando backend/config.py (hasta 3 niveles),
        p. ej. desde packaging/windows/."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    here = os.path.dirname(os.path.abspath(__file__))
    cand = here
    for _ in range(3):
        if os.path.isfile(os.path.join(cand, "backend", "config.py")):
            return cand
        nxt = os.path.dirname(cand)
        if nxt == cand:
            break
        cand = nxt
    return here


def data_dir():
    """Datos por SO: %APPDATA% en Windows; ~/Library y ~/.local/share en las
    demás (útiles para probar bajo macOS/Linux sin empaquetar).
    NEVERRED_APPDATA_DATA lo anula todo (gancho para pruebas automatizadas)."""
    if os.environ.get("NEVERRED_APPDATA_DATA"):
        return os.environ["NEVERRED_APPDATA_DATA"]
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "NeverRed")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/NeverRed")
    return os.path.expanduser("~/.local/share/NeverRed")


def config_version(root):
    """Versión leída de backend/config.py del disco (fuente única, sin reglas)."""
    path = os.path.join(root, "backend", "config.py")
    try:
        with open(path, encoding="utf-8") as f:
            m = re.search(r'"NEVERRED_VERSION",\s*"([0-9][0-9.]*)"', f.read())
        return m.group(1) if m else "0.0.0"
    except OSError:
        return "0.0.0"


def ulog(data, msg):
    """Diario del lanzador/update (mismas reglas que update-common.sh: acotado)."""
    try:
        os.makedirs(data, exist_ok=True)
        log = os.path.join(data, "update.log")
        # %F no existe en el strftime de Windows: fecha completa explícita.
        with open(log, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
        with open(log, encoding="utf-8", errors="replace") as f:
            tail = f.readlines()[-200:]
        tmp = log + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(tail)
        os.replace(tmp, log)
    except OSError:
        pass


def telemetry_conf_markers():
    """Defectos de telemetría marcados por el build (build.ps1 los sustituye).
    Marcadores intactos —como en el Launcher.sh— equivalen a vacío."""
    sink, token = "__NEVERRED_BUILD_SINK__", "__NEVERRED_BUILD_TOKEN__"
    return ("", "") if (sink.startswith("__NEVERRED_BUILD_")
                        and token.startswith("__NEVERRED_BUILD_")) else (sink, token)


def telemetry_from_conf(data, build_sink, build_token):
    """Paridad con Launcher.sh: entorno > telemetry.conf > defecto del build.
    Sin destino: telemetría local pura, nada sale del equipo. Devuelve el sink
    efectivo y exporta sink/token al entorno para el hijo servidor."""
    sink = os.environ.get("NEVERRED_TELEMETRY_SINK", "").strip()
    token = os.environ.get("NEVERRED_TELEMETRY_TOKEN", "").strip()
    conf = os.path.join(data, "telemetry.conf")
    if not os.path.isfile(conf):
        try:
            with open(conf, "w", encoding="utf-8") as f:
                f.write("# Destino de telemetría (opcional). Sin destino, nada sale de "
                        "este equipo.\n# Prioridad: entorno > este fichero > defecto del "
                        "build.\n")
                f.write((("NEVERRED_TELEMETRY_SINK=%s" % build_sink) if build_sink
                         else "#NEVERRED_TELEMETRY_SINK=https://...") + "\n")
                f.write((("NEVERRED_TELEMETRY_TOKEN=%s" % build_token) if build_token
                         else "#NEVERRED_TELEMETRY_TOKEN=...") + "\n")
        except OSError:
            pass
    try:
        with open(conf, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, sep, val = line.partition("=")
                val = val.strip()
                if not sep or not val:
                    continue
                if key == "NEVERRED_TELEMETRY_SINK" and not sink:
                    sink = val
                elif key == "NEVERRED_TELEMETRY_TOKEN" and not token:
                    token = val
    except OSError:
        pass
    if not sink and build_sink:
        sink = build_sink
    if not token and build_token:
        token = build_token
    if sink:
        os.environ["NEVERRED_TELEMETRY_SINK"] = sink
    if token:
        os.environ["NEVERRED_TELEMETRY_TOKEN"] = token
    return sink


def updater_command(root):
    """Comando del comprobador de actualizaciones: exe aparte si existe (así el
    swap no bloquea el exe en uso), o el propio intérprete con este módulo en
    la variante portable. Devuelve lista de args o None si no está disponible."""
    if is_frozen():
        upd = os.path.join(root, "NeverRed-Update.exe")
        if os.path.isfile(upd):
            return [upd, "--check", "--root", root, "--data", data_dir()]
        return None
    upd = os.path.join(root, "update_run.py")
    if os.path.isfile(upd):
        return [sys.executable, upd, "--check", "--root", root, "--data",
                data_dir()]
    return None


def health_ok(url):
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def open_browser(url, no_browser):
    if no_browser or os.environ.get("NEVERRED_BROWSER") == "none":
        return
    import webbrowser
    webbrowser.open(url)


def process_alive(pid):
    """¿Sigue vivo el PID? Para el vigía del padre (paridad del trap unix).
    os.kill(pid, 0) no envía nada: solo comprueba existencia (Unix);
    en Windows OpenProcess con QUERY_LIMITED_INFORMATION."""
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x0400, False, pid)  # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        k32.CloseHandle(h)
        return True
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True      # existe pero no es nuestro (sandbox): está vivo
    except ProcessLookupError:
        return False
    except OSError:
        return False


def serve(root, data):
    """Modo servidor (proceso hijo): monta server.py en el sys.path y arranca su
    main(). Vigila al padre: sin el trap de señales del bash, el hijo sale
    si el padre desaparece."""
    # Limpio argv: server.main() parsea los mismos --port/--host que este
    # launcher, y los nuestros (--serve/--root/--data) serían rechazados.
    sys.argv = [os.path.basename(sys.argv[0])]
    sys.path.insert(0, os.path.join(root, "backend"))
    server = __import__("server")
    try:
        parent = int(os.environ.get("NEVERRED_PARENT_PID", ""))
    except ValueError:
        parent = 0
    if parent:
        import threading

        def vigila():
            while process_alive(parent):
                time.sleep(2)
            os._exit(0)  # el padre murió sin cerrar: cortar también aquí

        threading.Thread(target=vigila, daemon=True).start()
    server.main()
    return 0


def run_server_child(root, data, port, no_browser):
    """Modo por defecto: hijo servidor, abrir navegador, esperar y salir."""
    port = str(port)
    url = "http://127.0.0.1:%s" % port
    pidfile = os.path.join(data, "server.pid")
    # ¿Servidor vivo ya en ese puerto? Solo abre el navegador, como macOS.
    if health_ok(url) and os.path.isfile(pidfile):
        open_browser(url, no_browser)
        return 0
    if os.path.isfile(pidfile):
        try:
            os.remove(pidfile)  # pidfile rancio (tras un autoapagado)
        except OSError:
            pass

    env = dict(os.environ)
    env.update({
        "NEVERRED_DB": os.path.join(data, "neverred.db"),
        "HOST": "127.0.0.1",
        "PORT": port,
        "NEVERRED_QUIT_WHEN_IDLE": "1",
        "NEVERRED_IDLE_TIMEOUT": env.get("NEVERRED_IDLE_TIMEOUT", "20"),
        "NEVERRED_PARENT_PID": str(os.getpid()),
    })
    if is_frozen():
        args = [sys.executable, "--serve", "--root", root, "--data", data]
    else:
        args = [sys.executable, os.path.abspath(__file__), "--serve"]
    log = open(os.path.join(data, "server.log"), "ab")
    creation = CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, env=env,
                            creationflags=creation, cwd=root)
    with open(pidfile, "w", encoding="utf-8") as f:
        f.write(str(proc.pid))
    open_browser(url, no_browser)
    ulog(data, "servidor arrancado pid=%d puerto=%s" % (proc.pid, port))
    try:
        return proc.wait()
    except KeyboardInterrupt:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        try:
            os.remove(pidfile)
        except OSError:
            pass
        return 0


_app_mutex = None


def hold_app_mutex():
    """Windows: mantiene un mutex con la vida del proceso. El instalador
    (Inno Setup, AppMutex=NeverRedAppMutex) lo usa para detectar si la app
    está en marcha y poder cerrarla al instalar/desinstalar."""
    global _app_mutex
    if os.name == "nt" and _app_mutex is None:
        try:
            import ctypes
            # Guardar el handle es obligatorio: sin referencias vivas el
            # objeto del kernel se destruiría de inmediato.
            _app_mutex = ctypes.windll.kernel32.CreateMutexW(
                None, False, "NeverRedAppMutex")
        except OSError:
            _app_mutex = None


def main():
    no_browser = "--no-browser" in sys.argv
    no_update = no_browser or os.environ.get("NEVERRED_NO_UPDATE") == "1"
    root = app_root()
    data = data_dir()
    hold_app_mutex()
    try:
        os.makedirs(data, exist_ok=True)
    except OSError as e:
        print("No puedo crear el directorio de datos %s: %s" % (data, e))
        return 1

    if "--version" in sys.argv:
        print(config_version(root))
        return 0
    if "--serve" in sys.argv:
        return serve(root, data)

    port = int(os.environ.get("PORT", "8000"))
    if not no_update:
        cmd = updater_command(root)
        if cmd:
            creation = CREATE_NO_WINDOW if os.name == "nt" else 0
            try:
                code = subprocess.run(
                    cmd, env=dict(os.environ), cwd=root,
                    creationflags=creation).returncode
            except OSError as e:
                ulog(data, "actualizador no ejecutable: %s" % e)
                code = 0
            if code == UPDATED_EXIT:
                # La nueva versión ya se abrió a sí misma tras el intercambio:
                # este arranque termina sin montar nada encima.
                ulog(data, "actualización instalada; fin de este arranque")
                return 0
    sink, token = telemetry_conf_markers()
    telemetry_from_conf(data, sink, token)
    return run_server_child(root, data, port, no_browser)


if __name__ == "__main__":
    sys.exit(main())
