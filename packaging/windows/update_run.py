"""NeverRed — actualizador de Windows (equivalente a check-update.sh + update-common.sh).

Uso (invocado por launcher.py, no a mano):
  update_run.py --check [--root DIR] [--data DIR]
  update_run.py --dialog REMOTA FICHERO-NOTAS [local]

Salidas (como el bash):
  0  = seguir con el arranque normal
  42 = actualización instalada: la nueva versión ya se reabrió sola

Flujo del intercambio (Windows no permite tocar un .exe en ejecución, y un
directorio con un .exe abierto tampoco se renombra):
  1. Descarga el ZIP firmado y lo comprueba con ssh-keygen -Y verify.
  2. Lo extrae en un directorio a HERMANA de la instalación (nunca dentro),
     valida la versión y escribe el fichero update.cmd al directorio de datos.
  3. Lanza ese .cmd desvinculado y sale (42). El launcher sale y el .cmd,
     ya sin nada bloqueado: para el servidor por PID, renombra la vieja
     instalación a *.update-old, pone la nueva, verifica, relanza y se borra.
  Todo con vuelta atrás: si el intercambio falla, la instalación actual queda
  intacta y el siguiente arranque reintentará la oferta.
"""
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.request
import zipfile

REPO = "emilio-devesa/NeverRed"
# Clave pública de release: solo se instala lo firmado con su privada
# (packaging/release-key, fuera del repo). Sin firma válida no hay update.
RELEASE_PUBKEY = ("ssh-ed25519 "
                  "AAAAC3NzaC1lZDI1NTE5AAAAIBXSqPW7x7HSHNj8akFRTv2dX7dUq1ZGnxwO5x3yRkCM")
SIGN_ID = "neverred-release"
SIGN_NS = "neverred-update"
UPDATED_EXIT = 42

# ── Helpers compartidos con launcher.py ────────────────────────────────
# app_root/data_dir/ulog vivían por partida doble (launcher.py y aquí).
# Ahora la fuente es launcher.py; queda un respaldo local mínimo para el
# caso improbable de que el import falle (p. ej. un empaquetado que no
# embeba launcher en el exe del actualizador).
try:
    import launcher as _launcher
except ImportError:  # pragma: no cover - solo en empaquetados recortados
    _launcher = None


def data_dir():
    """Igual que launcher.data_dir (con gancho NEVERRED_APPDATA_DATA)."""
    if _launcher is not None:
        return _launcher.data_dir()
    if os.environ.get("NEVERRED_APPDATA_DATA"):
        return os.environ["NEVERRED_APPDATA_DATA"]
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "NeverRed")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/NeverRed")
    return os.path.expanduser("~/.local/share/NeverRed")


def app_root():
    """Igual que launcher.app_root, con el gancho NEVERRED_APPDATA_ROOT."""
    if os.environ.get("NEVERRED_APPDATA_ROOT"):
        return os.environ["NEVERRED_APPDATA_ROOT"]
    if _launcher is not None:
        return _launcher.app_root()
    return os.path.dirname(os.path.abspath(__file__))


def parse_args(argv):
    root, data, args = None, None, []
    it = iter(argv)
    for a in it:
        if a == "--root":
            root = next(it, None)
        elif a == "--data":
            data = next(it, None)
        else:
            args.append(a)
    return root, data, args


def ulog(data, msg):
    """Igual que launcher.ulog (log con cola de 200 líneas)."""
    if _launcher is not None:
        return _launcher.ulog(data, msg)
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


# ---------- utilidades de release (paridad con update-common.sh) ----------

def version_newer(remote, local):
    def v(s):
        return [int(x) for x in s.lstrip("v").split(".") if x.isdigit()]
    return v(remote) > v(local)


def read_installed_version(root):
    path = os.path.join(root, "backend", "config.py")
    try:
        with open(path, encoding="utf-8") as f:
            m = re.search(r'"NEVERRED_VERSION",\s*"([0-9][0-9.]*)"', f.read())
        return m.group(1) if m else "0.0.0"
    except OSError:
        return "0.0.0"


def ca_context():
    """Contexto TLS con CAs razonables.

    En Windows moderno el almacén del sistema basta. En XP (y Python viejo) el
    almacén carece de los roots TLS actuales (p. ej. los de api.github.com), y
    la verificación fallaría cerrada; si existe un cacert.pem junto al exe o en
    la raíz del portable se usa. Sin bundle y sin almacén válido: falla cerrado,
    como todo el resto del flujo. NEVERRED_CA fuerza una ruta de bundle."""
    candidates = []
    env = os.environ.get("NEVERRED_CA")
    if env:
        candidates.append(env)
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(
            os.path.dirname(os.path.abspath(sys.executable)), "cacert.pem"))
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(here, "cacert.pem"))
        candidates.append(os.path.join(here, os.pardir, os.pardir, "cacert.pem"))
    for path in candidates:
        try:
            if path and os.path.isfile(path):
                return ssl.create_default_context(cafile=path)
        except (OSError, ssl.SSLError):
            continue  # bundle ilegible o inválido: se prueba el siguiente
    return None  # urllib usará el almacén del sistema (o fallará cerrado)


def fetch_latest_release():
    req = urllib.request.Request(
        "https://api.github.com/repos/%s/releases/latest" % REPO,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "NeverRed-Windows-Update"})
    with urllib.request.urlopen(req, timeout=15, context=ca_context()) as r:
        return json.loads(r.read().decode("utf-8"))


def release_eval(rel, asset_hint_on_windows):
    """Selección del activo de la release.
    Con .exe instalado se busca el ZIP del exe (sin 'portable'); si corre la
    variante portable, el -portable. Devuelve (tag, url_asset, url_sig, notas)."""
    tag = str(rel.get("tag_name") or "")
    assets = [str(a.get("browser_download_url") or "")
              for a in rel.get("assets", [])]
    names = [u.rsplit("/", 1)[-1] for u in assets]

    def pick(pred):
        cands = [u for u, n in zip(assets, names) if pred(n, u)]
        return cands[-1] if cands else ""
    exe_mode = asset_hint_on_windows
    if exe_mode:
        asset = pick(lambda n, u: n.startswith("NeverRed-windows-")
                     and n.endswith(".zip") and "portable" not in n)
    else:
        asset = pick(lambda n, u: n.startswith("NeverRed-windows-")
                     and n.endswith("-portable.zip"))
    if not asset:
        return tag, "", "", ""
    sig = pick(lambda n, u: u == asset + ".sig")
    body = str(rel.get("body") or "")[:1200]
    return tag, asset, sig, body


def verify_sig(data, file_path, sig_url):
    """Comprobación de firma con el ssh-keygen de serie (OpenSSH >= 8.9).
    Falla cerrado como update-common.sh: sin ssh-keygen o sin firma no hay update."""
    if not sig_url:
        return False
    if shutil.which("ssh-keygen") is None:
        ulog(data, "ssh-keygen no disponible (se necesita OpenSSH >= 8.9)")
        return False
    sig = os.path.join(data, ".%s.sig" % os.path.basename(file_path))
    try:
        r = urllib.request.urlopen(sig_url, timeout=60, context=ca_context())
        with open(sig, "wb") as f:
            f.write(r.read())
    except Exception as e:
        ulog(data, "descarga de firma falló: %s" % e)
        try:
            os.remove(sig)
        except OSError:
            pass
        return False
    allow = os.path.join(data, ".allowed_signers")
    try:
        with open(allow, "w", encoding="utf-8") as f:
            f.write("%s %s\n" % (SIGN_ID, RELEASE_PUBKEY))
        with open(file_path, "rb") as stdin:
            proc = subprocess.run(
                ["ssh-keygen", "-Y", "verify", "-f", allow,
                 "-I", SIGN_ID, "-n", SIGN_NS, "-s", sig],
                stdin=stdin, capture_output=True)
        if proc.returncode == 0:
            return True
        ulog(data, "firma rechazada: %s" % proc.stderr.decode("utf-8", "replace").strip())
        return False
    except OSError as e:
        ulog(data, "verificación falló: %s" % e)
        return False
    finally:
        for p in (sig, allow):
            try:
                os.remove(p)
            except OSError:
                pass


def is_skipped(data, remote):
    try:
        with open(os.path.join(data, "skipped_version"), encoding="utf-8") as f:
            return f.read().strip() == remote
    except OSError:
        return False


def mark_skipped(data, remote):
    try:
        os.makedirs(data, exist_ok=True)
        with open(os.path.join(data, "skipped_version"), "w", encoding="utf-8") as f:
            f.write(remote)
    except OSError:
        pass


def unskip_if_failed_swap(data, remote, local):
    """Si de una pasada anterior quedó un intercambio pendiente sin éxito,
    borra la marca de «omitido» para que la versión vuelva a ofrecerse."""
    pend = os.path.join(data, ".update_pending")
    ok = os.path.join(data, ".update_ok")
    ok_ver = ""
    try:
        if os.path.isfile(ok):
            with open(ok, encoding="utf-8") as f:
                ok_ver = f.read().strip()
    except OSError:
        ok_ver = ""
    if os.path.isfile(ok) and ok_ver == local:
        # Intercambio cumplido: soltar los restos (pending y skip).
        for p in (pend,):
            try:
                os.remove(p)
            except OSError:
                pass
        return
    if os.path.isfile(pend):
        # Había un intercambio a medias: no se logró (o solo principio).
        ulog(data, "intercambio anterior no concluido: se reofrece la %s" % remote)
        try:
            if is_skipped(data, remote):
                sp = os.path.join(data, "skipped_version")
                # Windows no deja borrar un fichero aún abierto por el propio
                # proceso: leer y CERRAR antes del remove (no dentro del with).
                skip = False
                with open(sp, encoding="utf-8") as f:
                    skip = f.read().strip() == remote
                if skip:
                    os.remove(sp)
        except OSError:
            pass
        try:
            os.remove(pend)
        except OSError:
            pass


def download(url, dest, timeout=120):
    # Timeout por conexión y de respuesta completa (deadline de 120 s como el bash).
    import socket
    total = 0
    start = time.time()
    with urllib.request.urlopen(url, timeout=30, context=ca_context()) as r:
        chunk_size = 64 * 1024
        with open(dest, "wb") as out:
            while True:
                left = max(1.0, timeout - (time.time() - start))
                socket.setdefaulttimeout(left)
                chunk = r.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                out.write(chunk)
    return total


def extract_zip(zip_path, stage):
    """Extrae si y solo si el árbol contiene lo mínimo vital en la raíz."""
    lite = {"NeverRed.exe", "backend", "launcher.py", "NeverRed.bat"}
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        if any(n.startswith("/") or ".." in n for n in names):
            raise ValueError("zip con rutas peligrosas")
        mk = {n.split("/")[0] for n in names if n}
        if not (mk & lite):
            raise ValueError("zip no reconoce estructura de NeverRed")
        z.extractall(stage)
    for root_, dirs, files in os.walk(stage):
        os.chmod(root_, 0o755)
    return stage


def valid_stage(stage, remote, wants_exe):
    if read_installed_version(stage) != remote:
        return False
    if wants_exe and not os.path.isfile(os.path.join(stage, "NeverRed.exe")):
        return False
    wants_rel = "NeverRed.exe" if wants_exe else "NeverRed.bat"
    if not os.path.isfile(os.path.join(stage, wants_rel)):
        return False
    if not os.path.isfile(os.path.join(stage, "backend", "server.py")):
        return False
    return True


def build_update_cmd(target, old, stage, data, relauncher, remote):
    """Genera el .cmd que hace el swap sin dejar la instalación a medias:
    1) para el servidor (PID del pidfile), 2) renombra la instalación actual
    a un directorio oculto hermano (`ren` en cmd exige solo el nombre, no ruta),
    3) mueve la nueva a la ruta original, 4) verifica `.update_version` y
    relanza, 5) borra la vieja y el propio .cmd. Con vuelta atrás en 3) y 4)."""
    oldname = os.path.basename(old)
    cmd = os.path.join(data, "update.cmd")
    os.makedirs(data, exist_ok=True)
    with open(cmd, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(
            "@echo off\r\n"
            "rem NeverRed — intercambio de actualización (creado por NeverRed-Update).\r\n"
            "rem Uso: update.cmd <target> <old> <new> <data> <relauncher> <version> <oldname>\r\n"
            'if x%~6==x exit /b 1\r\n'
            'if not exist "%~1" exit /b 1\r\n'
            'if not exist "%~3" exit /b 1\r\n'
            ":para_servidor\r\n"
            'if not exist "%~4\\server.pid" goto sin_pid\r\n'
            'set /p PID=<"%~4\\server.pid"\r\n'
            'taskkill /PID %PID% /T /F >nul 2>&1\r\n'
            'ping -n 3 127.0.0.1 >nul\r\n'
            'del "%~4\\server.pid" >nul 2>&1\r\n'
            ":sin_pid\r\n"
            'if exist "%~2" rmdir /s /q "%~2"\r\n'
            'ren "%~1" "%~7"\r\n'
            'if errorlevel 1 (\r\n'
            '  >>"%~4\\update.log" echo %date% %time% fallo ren a update-old\r\n'
            "  exit /b 1\r\n"
            ")\r\n"
            'move "%~3" "%~1" >nul 2>&1\r\n'
            'if errorlevel 1 (\r\n'
            '  >>"%~4\\update.log" echo %date% %time% fallo move nueva\r\n'
            '  ren "%~2" "%~nx1"\r\n'
            "  exit /b 1\r\n"
            ")\r\n"
            'if not exist "%~1\\.update_version" (\r\n'
            '  >>"%~4\\update.log" echo %date% %time% verificacion ausente\r\n'
            '  rmdir /s /q "%~1"\r\n'
            '  ren "%~2" "%~nx1"\r\n'
            "  exit /b 1\r\n"
            ")\r\n"
            'set /p INST=<"%~1\\.update_version"\r\n'
            'if not "%INST%"=="%~6" (\r\n'
            '  >>"%~4\\update.log" echo %date% %time% copia no verificada %INST%\r\n'
            '  rmdir /s /q "%~1"\r\n'
            '  ren "%~2" "%~nx1"\r\n'
            "  exit /b 1\r\n"
            ")\r\n"
            'rmdir /s /q "%~2"\r\n'
            'del "%~1\\.update_version" >nul 2>&1\r\n'
            'start "" "%~1\\%~5"\r\n'
            '>>"%~4\\update.log" echo %date% %time% instalada %~6\r\n'
            'echo %~6> "%~4\\.update_ok"\r\n'
            'del "%~4\\.update_pending" >nul 2>&1\r\n'
            '(goto) 2>nul & del "%~f0"\r\n'
        )
    return cmd


def stage_relauncher(stage):
    """Qué reabrir tras el swap: el .exe si existe; si no, el .bat portable."""
    for n in ("NeverRed.exe", "NeverRed.bat"):
        if os.path.isfile(os.path.join(stage, n)):
            return n
    return None


def pendiente_marker(data, target, old, stage, relauncher, remote):
    try:
        with open(os.path.join(data, ".update_pending"), "w", encoding="utf-8") as f:
            f.write("%s|%s|%s|%s|%s|%s" % (target, old, stage, data,
                                           relauncher, remote))
    except OSError:
        pass


def swap_and_relaunch(cmd, data):
    """Arranca update.cmd desvinculado y termina este proceso (el .cmd ya
    relanza la nueva; el launcher responde con 42 y sale)."""
    if os.name != "nt":
        ulog(data, "el swap real solo puede ejecutarse en Windows")
        return False
    subprocess.Popen(["cmd", "/c", cmd],
                     creationflags=subprocess.DETACHED_PROCESS,
                     close_fds=True)
    return True


def do_check():
    root, data, extra = parse_args(sys.argv[1:])
    root = root or app_root()
    data = data or data_dir()
    root, data = os.path.abspath(root), os.path.abspath(data)
    if os.environ.get("NEVERRED_NO_UPDATE") == "1":
        return 0
    local = read_installed_version(root)
    stage = None
    ulog(data, "check: arranque (local=%s)" % local)
    try:
        rel = fetch_latest_release()
    except Exception as e:
        ulog(data, "sin red (%s; local=%s)" % (e, local))
        return 0
    wants_exe = os.path.isfile(os.path.join(root, "NeverRed.exe"))
    remote, asset, sig, notes = release_eval(rel, wants_exe)
    ulog(data, "check: release leída (remota=%s asset=%s)" % (remote, "sí" if asset else "no"))
    if not remote or not asset:
        ulog(data, "sin activo de Windows para %s" % (remote or "?"))
        return 0
    try:
        os.makedirs(data, exist_ok=True)
    except OSError:
        return 0
    unskip_if_failed_swap(data, remote, local)
    if is_skipped(data, remote) or not version_newer(remote, local):
        ulog(data, "al día (local=%s remota=%s)" % (local, remote))
        return 0
    ulog(data, "nueva %s (local=%s)" % (remote, local))

    notas_file = write_notes(data, notes)
    choice = run_dialog(sys.executable, remote, local, notas_file)
    try:
        os.remove(notas_file)
    except OSError:
        pass
    if choice == "skip":
        mark_skipped(data, remote)
        ulog(data, "omitida por el usuario: %s" % remote)
        return 0
    if choice != "install":
        ulog(data, "pospuesta por el usuario o diálogo fallido: %s" % remote)
        return 0

    parent = os.path.dirname(root)
    base = os.path.basename(root)
    old = os.path.join(parent, "." + base + ".update-old")
    stage = os.path.join(parent, "." + base + ".update-new")
    pkg = os.path.join(data, "NeverRed-update.zip")
    try:
        if download(asset, pkg) < 1:
            raise RuntimeError("descarga vacía")
        if not verify_sig(data, pkg, sig):
            raise RuntimeError("firma no válida o no verificable")
        if os.path.isdir(stage):
            shutil.rmtree(stage, ignore_errors=True)
        extract_zip(pkg, stage)
        if not valid_stage(stage, remote, wants_exe):
            raise RuntimeError("árbol extraído no coincide con la %s" % remote)
        marker = os.path.join(stage, ".update_version")
        with open(marker, "w", encoding="utf-8") as f:
            f.write(remote)
        relauncher = stage_relauncher(stage)
        if not relauncher:
            raise RuntimeError("sin lanzador en el árbol nuevo")
        mark_skipped(data, remote)  # evita doble oferta hasta confirmar el swap
        pendiente_marker(data, root, old, stage, relauncher, remote)
        cmd = build_update_cmd(root, old, stage, data, relauncher, remote)
        if not swap_and_relaunch(cmd, data):
            return 0
        ulog(data, "intercambio programado para %s" % remote)
        return UPDATED_EXIT
    except Exception as e:
        ulog(data, "update de %s falló: %s" % (remote, e))
        for p in (pkg,):
            try:
                os.remove(p)
            except OSError:
                pass
        if stage and os.path.isdir(stage):
            shutil.rmtree(stage, ignore_errors=True)
        return 0


def write_notes(data, notes):
    path = os.path.join(data, "release-notes.txt")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(notes.replace("\r\n", "\n").replace("\r", "\n"))
    except OSError:
        return None
    return path


def run_dialog(exe, remote, local, notas_file):
    """Lanza el diálogo tkinter en un proceso aparte; devuelve install|skip|later.
    NEVERRED_UPDATE_CHOICE lo salta (única vía de automatizar en pruebas)."""
    if os.environ.get("NEVERRED_UPDATE_CHOICE"):
        return os.environ["NEVERRED_UPDATE_CHOICE"]
    if not notas_file:
        return "later"
    if getattr(sys, "frozen", False):
        args = [exe, "--dialog", remote, notas_file, local]
    else:
        args = [exe, os.path.abspath(__file__), "--dialog", remote, notas_file, local]
    try:
        creation = 0
        if os.name == "nt":
            creation = subprocess.CREATE_NO_WINDOW
        p = subprocess.run(args, capture_output=True, timeout=125,
                           creationflags=creation)
        return p.stdout.decode("utf-8", "replace").strip()
    except Exception:
        return "later"


def run_dialog_inline(remote, notes_path, local):
    """Ventana de actualización. Misma experiencia que el de macOS."""
    import tkinter as tk
    from tkinter import scrolledtext
    try:
        with open(notes_path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError:
        raw = ""
    notas = raw.split("## Descargar")[0].strip()[:2000] or \
        "Mejoras internas y correcciones de errores.\n\nTus datos y ajustes se conservan tal cual."
    choice = {"v": "later"}
    root = tk.Tk()
    root.title("Actualización de NeverRed")
    root.geometry("+250+250")
    root.attributes("-topmost", True)
    tk.Label(root, text="NeverRed %s disponible (tienes %s)." % (remote, local),
             font=("TkDefaultFont", 11, "bold")).grid(
        row=0, column=0, sticky="w", padx=12, pady=(12, 4))
    tk.Label(root, text="Tus datos se conservan. La descarga va firmada.").grid(
        row=1, column=0, sticky="w", padx=12)
    box = scrolledtext.ScrolledText(root, width=64, height=12)
    box.insert("1.0", notas)
    box.config(state="disabled")
    box.grid(row=2, column=0, padx=12, pady=8, sticky="nsew")
    row3 = tk.Frame(root)
    row3.grid(row=3, column=0, sticky="e", padx=12, pady=(0, 12))

    def ci(v):
        choice["v"] = v
        root.destroy()
    tk.Button(row3, text="Omitir versión", command=lambda: ci("skip")).grid(
        row=0, column=0, padx=6)
    tk.Button(row3, text="Más tarde", command=lambda: ci("later")).grid(
        row=0, column=1, padx=6)
    tk.Button(row3, text="Instalar", command=lambda: ci("install")).grid(
        row=0, column=2, padx=6)
    root.protocol("WM_DELETE_WINDOW", lambda: ci("later"))
    root.grab_set()
    root.after(1, lambda: None)
    root.mainloop()
    print("%s" % choice["v"])


def main():
    root, data, rest = parse_args(sys.argv[1:])
    if "--dialog" in rest:
        i = rest.index("--dialog")
        # argv originales preservados tras --dialog
        try:
            _, remote, notas, local = rest[i], rest[i + 1], rest[i + 2], rest[i + 3]
        except IndexError:
            print("later")
            return 1
        try:
            run_dialog_inline(remote, notas, local)
        except Exception:
            print("later")
        return 0
    if "--check" in rest:
        return do_check()
    if "--version" in rest:
        print(read_installed_version(root or app_root()))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
