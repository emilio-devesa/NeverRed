#!/usr/bin/env python3
"""Pruebas del paquete Windows de NeverRed (nivel 2).

Se ejecutan igual en macOS/Linux que en Windows: solo prueban la lógica
Python (versiones, selección de activo, firma, zip, generación del
update.cmd, telemetría). Las piezas que renuncio a validar aquí —el .cmd
real, PyInstaller, el swap con cmd.exe— solo se pueden probar en Windows: ver
packaging/windows/README.md ("Pendiente de probar en Windows").

Uso: python3 packaging/windows/selftest.py    (unittest, sin dependencias)
"""
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))   # packaging/windows -> repo
sys.path.insert(0, HERE)

import launcher      # noqa: E402
import update_run    # noqa: E402


class CAs(unittest.TestCase):
    """ca_context(): bundle de CAs para TLS en Windows viejo (XP sin roots)."""

    def test_bundle_junto_al_script(self):
        if not os.path.isfile(os.path.join(HERE, "cacert.pem")):
            self.skipTest("sin cacert.pem en packaging/windows/")
        ctx = update_run.ca_context()
        self.assertIsNotNone(ctx)
        # Un bundle válido chequea hostname/CN: sin ese ajuste no verificaría.
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)

    def test_env_ruta_explicita(self):
        pem = os.path.join(HERE, "cacert.pem")
        if not os.path.isfile(pem):
            self.skipTest("sin cacert.pem en packaging/windows/")
        tmp = os.path.join(tempfile.mkdtemp(), "ca-bundle.pem")
        shutil.copyfile(pem, tmp)
        viejo = os.environ.get("NEVERRED_CA")
        os.environ["NEVERRED_CA"] = tmp
        try:
            self.assertIsNotNone(update_run.ca_context())
        finally:
            if viejo is None:
                os.environ.pop("NEVERRED_CA", None)
            else:
                os.environ["NEVERRED_CA"] = viejo
            shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)


class Versiones(unittest.TestCase):
    def test_version_oficial(self):
        self.assertEqual(launcher.config_version(REPO), "2.6.9")
        self.assertEqual(update_run.read_installed_version(REPO), "2.6.9")

    def test_version_otro_arbol(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "backend"))
            with open(os.path.join(d, "backend", "config.py"), "w") as f:
                f.write('VERSION = os.environ.get("NEVERRED_VERSION", "3.0.1")')
            self.assertEqual(update_run.read_installed_version(d), "3.0.1")
            self.assertEqual(launcher.config_version(d), "3.0.1")

    def test_version_newer(self):
        self.assertTrue(update_run.version_newer("2.6.9", "2.6.8"))
        self.assertTrue(update_run.version_newer("2.7.0", "2.6.9"))
        self.assertFalse(update_run.version_newer("2.6.8", "2.6.8"))
        self.assertFalse(update_run.version_newer("2.6.7", "2.6.10"))
        self.assertFalse(update_run.version_newer("v2.6.8", "2.6.8"))

    def test_app_root(self):
        # Desde packaging/windows la raíz real del repo (2 niveles arriba).
        self.assertEqual(launcher.app_root(), REPO)
        self.assertEqual(update_run.app_root(), REPO)


class ActivoWindows(unittest.TestCase):
    """Selección del activo en la release: zipfile del .exe o del -portable."""

    def _rel(self):
        ASSETS = ["NeverRed-windows-2.6.9.zip",
                  "NeverRed-windows-2.6.9.zip.sig",
                  "NeverRed-windows-2.6.9-portable.zip",
                  "NeverRed-windows-2.6.9-portable.zip.sig",
                  "NeverRed-macOS-2.6.9.dmg"]
        return {"tag_name": "v2.6.9", "body": "Notas\n## Descargar\n...",
                "assets": [{"name": n,
                            "browser_download_url":
                                "https://ejemplo/download/v2.6.9/" + n}
                           for n in ASSETS]}

    def test_para_exe(self):
        tag, asset, sig, notes = update_run.release_eval(self._rel(), True)
        self.assertEqual(tag, "v2.6.9")
        self.assertTrue(asset.endswith("/NeverRed-windows-2.6.9.zip"))
        self.assertTrue(sig.endswith(".zip.sig"))
        self.assertNotIn("portable", asset)
        self.assertIn("Notas", notes)
        # el bloque de Descargar llega entero; lo recorta el diálogo (run_dialog_inline)

    def test_para_portable(self):
        _, asset, sig, _ = update_run.release_eval(self._rel(), False)
        self.assertTrue(asset.endswith("-portable.zip"))
        self.assertTrue(sig.endswith("-portable.zip.sig"))

    def test_sin_activo(self):
        rel = {"tag_name": "v1.0.0",
               "assets": [{"name": "viejo.dmg",
                           "browser_download_url": "https://x/viejo.dmg"}]}
        _, asset, sig, _ = update_run.release_eval(rel, True)
        self.assertEqual((asset, sig), ("", ""))


class Firma(unittest.TestCase):
    """Firma Ed25519 con un par generado al vuelo (no toca release-key)."""

    @staticmethod
    def _run(args):
        # py3.4-safe: subprocess.run no existe (3.5+).
        proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        out, err = proc.communicate()
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(
                proc.returncode, args, err.decode("utf-8", "replace").strip())

    def _clave(self, d):
        key = os.path.join(d, "test-key")
        self._run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key,
             "-C", "neverred-test"])
        return key

    def test_firma_valida_e_invalida(self):
        if shutil.which("ssh-keygen") is None:
            self.skipTest("sin ssh-keygen (Windows sin OpenSSH >= 8.9)")
        prev = (update_run.RELEASE_PUBKEY, update_run.SIGN_ID)
        try:
            with tempfile.TemporaryDirectory() as d:
                key = self._clave(d)
                with open(key + ".pub", encoding="utf-8") as f:
                    parts = f.read().split()
                update_run.RELEASE_PUBKEY = parts[0] + " " + parts[1]
                update_run.SIGN_ID = "neverred-test"
                target = os.path.join(d, "paquete.zip")
                with open(target, "wb") as f:
                    f.write(b"contenido de prueba" * 64)
                self._run(
                    ["ssh-keygen", "-Y", "sign", "-f", key,
                     "-n", update_run.SIGN_NS, target])
                sig_url = "file://" + target + ".sig"
                # Intacto: firma válida.
                self.assertTrue(update_run.verify_sig(d, target, sig_url))
                # Un byte cambiado: rechazada.
                with open(target, "rb+") as f:
                    f.seek(0)
                    f.write(b"t")
                self.assertFalse(update_run.verify_sig(d, target, sig_url))
        finally:
            (update_run.RELEASE_PUBKEY, update_run.SIGN_ID) = prev

    def test_sin_ssh_keygen_falla_cerrado(self):
        env_path = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = os.pathsep.join(
                [p for p in env_path.split(os.pathsep)
                 if "ssh" not in p.lower()] + ["/no-hay-ssh"])
            with tempfile.TemporaryDirectory() as d:
                target = os.path.join(d, "paquete.zip")
                with open(target, "wb") as f:
                    f.write(b"x")
                self.assertFalse(
                    update_run.verify_sig(d, target, "https://nada/f.sig"))
        finally:
            os.environ["PATH"] = env_path

    def test_sin_url_firma(self):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "paquete.zip")
            with open(target, "wb") as f:
                f.write(b"x")
            self.assertFalse(update_run.verify_sig(d, target, ""))


class ZipYStage(unittest.TestCase):
    def _zip(self, d, remote="2.6.9", with_exe=True):
        tree = os.path.join(d, "tree")
        os.makedirs(os.path.join(tree, "backend"))
        with open(os.path.join(tree, "backend", "config.py"), "w") as f:
            f.write('VERSION = os.environ.get("NEVERRED_VERSION", "%s")' % remote)
        open(os.path.join(tree, "backend", "server.py"), "w").close()
        open(os.path.join(tree, "index.html"), "w").close()
        if with_exe:
            with open(os.path.join(tree, "NeverRed.exe"), "wb") as f:
                f.write(b"MZ")
        zap = os.path.join(d, "paquete.zip")
        with zipfile.ZipFile(zap, "w") as z:
            for base, _, files in os.walk(tree):
                for n in files:
                    p = os.path.join(base, n)
                    z.write(p, os.path.relpath(p, tree))
        return zap

    def test_extract_y_stage_valido(self):
        with tempfile.TemporaryDirectory() as d:
            zap = self._zip(d, with_exe=True)
            stage = os.path.join(d, "stage")
            update_run.extract_zip(zap, stage)
            self.assertTrue(update_run.valid_stage(stage, "2.6.9", True))
            # árbol de .exe no vale como portable: le falta el .bat
            self.assertFalse(update_run.valid_stage(stage, "2.6.9", False))
            self.assertFalse(update_run.valid_stage(stage, "3.0.0", True))

    def test_extract_stage_portable(self):
        with tempfile.TemporaryDirectory() as d:
            zap = self._zip_portable(d)
            stage = os.path.join(d, "stage")
            update_run.extract_zip(zap, stage)
            self.assertTrue(update_run.valid_stage(stage, "2.6.9", False))
            # sin NeverRed.exe no vale para la variante .exe
            self.assertFalse(update_run.valid_stage(stage, "2.6.9", True))

    def _zip_portable(self, d):
        tree = os.path.join(d, "portable-tree")
        os.makedirs(os.path.join(tree, "backend"))
        with open(os.path.join(tree, "backend", "config.py"), "w") as f:
            f.write('VERSION = os.environ.get("NEVERRED_VERSION", "2.6.9")')
        open(os.path.join(tree, "backend", "server.py"), "w").close()
        open(os.path.join(tree, "launcher.py"), "w").close()
        open(os.path.join(tree, "NeverRed.bat"), "w").close()
        zap = os.path.join(d, "portable.zip")
        with zipfile.ZipFile(zap, "w") as z:
            for base, _, files in os.walk(tree):
                for n in files:
                    p = os.path.join(base, n)
                    z.write(p, os.path.relpath(p, tree))
        return zap

    def test_zip_traversing_rechazado(self):
        with tempfile.TemporaryDirectory() as d:
            mal = os.path.join(d, "traversal.zip")
            with zipfile.ZipFile(mal, "w") as z:
                z.writestr("../maligno.txt", "boom")
            stage = os.path.join(d, "stage")
            with self.assertRaises(ValueError):
                update_run.extract_zip(mal, stage)
            self.assertFalse(os.path.isfile(
                os.path.join(d, "maligno.txt")))


class UpdateCmd(unittest.TestCase):
    """El .cmd es la pieza más delicada: comprobamos el texto resultante."""

    def test_render_completo(self):
        data = tempfile.mkdtemp()
        try:
            cmd = update_run.build_update_cmd(
                r"C:\App\NeverRed", r"C:\App\.NeverRed.update-old",
                r"C:\App\.NeverRed.update-new",
                data,   # dir real: en POSIX un path tipo C:\ crearía carpetas raras
                "NeverRed.exe", "2.6.9")
            with open(cmd, encoding="utf-8", newline="") as f:
                text = f.read()
        finally:
            shutil.rmtree(data, ignore_errors=True)
        self.assertIn("\r\n", text)               # CRLF (cmd)
        # el ren de cmd exige SOLO el nombre: ren "%~1" "%~7".
        self.assertIn('ren "%~1" "%~7"', text)
        self.assertNotIn('ren "%~1" "%~2"', text)
        # vuelta atrás: restaurar el nombre original vía %~nx1
        self.assertIn('ren "%~2" "%~nx1"', text)
        self.assertIn("/T /F", text)              # parada del servidor
        self.assertIn("instalada %~6", text)
        self.assertIn("(goto) 2>nul", text)       # se borra a sí mismo al acabar
        self.assertIn('.update_version', text)
        self.assertIn(".update_ok", text)
        self.assertIn(".update_pending", text)

    def test_oldname_es_basename(self):
        import ntpath
        self.assertEqual(
            ntpath.basename(r"C:\App\.NeverRed.update-old"),
            ".NeverRed.update-old")


class SkipYPendiente(unittest.TestCase):
    def test_marcar_y_omitir(self):
        with tempfile.TemporaryDirectory() as d:
            update_run.mark_skipped(d, "2.6.9")
            self.assertTrue(update_run.is_skipped(d, "2.6.9"))
            self.assertFalse(update_run.is_skipped(d, "2.7.0"))

    def test_swap_fallido_re_ofrece(self):
        with tempfile.TemporaryDirectory() as d:
            update_run.mark_skipped(d, "2.6.9")
            with open(os.path.join(d, ".update_pending"), "w") as f:
                f.write("t|o|s|d|NeverRed.bat|2.6.9")
            update_run.unskip_if_failed_swap(d, "2.6.9", local="2.6.8")
            self.assertFalse(update_run.is_skipped(d, "2.6.9"))
            self.assertFalse(os.path.exists(
                os.path.join(d, ".update_pending")))

    def test_swap_reducido_deja_marcas(self):
        # .update_ok con versión verifica: cierre normal (solo queda pending por limpiar).
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, ".update_ok"), "w") as f:
                f.write("2.6.9")
            with open(os.path.join(d, ".update_pending"), "w") as f:
                f.write("x")
            update_run.unskip_if_failed_swap(d, "2.6.9", local="2.6.9")
            self.assertFalse(os.path.exists(
                os.path.join(d, ".update_pending")))
            # y el skip, si existía, ya no bloquea una siguiente oferta
            update_run.mark_skipped(d, "2.6.9")
            self.assertTrue(update_run.is_skipped(d, "2.6.9"))


class Telemetria(unittest.TestCase):
    def setUp(self):
        self.claves = ("NEVERRED_TELEMETRY_SINK", "NEVERRED_TELEMETRY_TOKEN")
        self.guardadas = {k: os.environ.get(k) for k in self.claves}
        for k in self.claves:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self.guardadas.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_entorno_pisa_todo(self):
        with tempfile.TemporaryDirectory() as d:
            launcher.telemetry_from_conf(
                d, "https://conf/ejemplo", "TOK-CONF")
            for k in self.claves:              # el primer arranque ya exportó
                os.environ.pop(k, None)
            os.environ["NEVERRED_TELEMETRY_SINK"] = "https://entorno/cero"
            sink = launcher.telemetry_from_conf(d, "https://conf/x", "TOK")
            self.assertEqual(sink, "https://entorno/cero")
            self.assertEqual(os.environ["NEVERRED_TELEMETRY_TOKEN"], "TOK-CONF")

    def test_conf_pisa_defecto_build(self):
        with tempfile.TemporaryDirectory() as d:
            launcher.telemetry_from_conf(d, "https://build/x", "TOK-B")
            for k in self.claves:              # limpiar la exportación del arranque
                os.environ.pop(k, None)
            # tras el primer arranque, un conf reescrito por el usuario gana
            with open(os.path.join(d, "telemetry.conf"), "w", encoding="utf-8") as f:
                f.write("NEVERRED_TELEMETRY_SINK=https://usuario/y\n")
            sink = launcher.telemetry_from_conf(d, "https://build/x", "TOK-B")
            self.assertEqual(sink, "https://usuario/y")

    def test_sin_nada_local_pura(self):
        with tempfile.TemporaryDirectory() as d:
            sink = launcher.telemetry_from_conf(d, "", "")
            self.assertFalse(sink)
            for k in self.claves:              # no exporta nada
                self.assertNotIn(k, os.environ)
            with open(os.path.join(d, "telemetry.conf"), encoding="utf-8") as f:
                c = f.read()
            self.assertIn("#NEVERRED_TELEMETRY_SINK", c)


class Instalador(unittest.TestCase):
    """installer.iss (Inno Setup): invariantes verificadas en Windows 11."""

    def setUp(self):
        iss = os.path.join(HERE, "installer.iss")
        self.assertTrue(os.path.isfile(iss), "falta packaging/windows/installer.iss")
        with open(iss, encoding="utf-8") as f:
            self.iss = f.read()

    def test_por_usuario_sin_elevacion(self):
        # per-usuario en %LOCALAPPDATA% sin UAC: el auto-update necesita
        # renombrar su propio directorio, lo que Program Files no permitiría
        self.assertIn("PrivilegesRequired=lowest", self.iss)
        self.assertIn("DefaultDirName={localappdata}", self.iss)

    def test_app_mutex_coincide_con_launcher(self):
        # el .iss y el launcher deben usar el MISMO nombre de mutex
        self.assertIn("AppMutex=NeverRedAppMutex", self.iss)
        with open(os.path.join(HERE, "launcher.py"), encoding="utf-8") as f:
            l = f.read()
        self.assertIn('"NeverRedAppMutex"', l)

    def test_desinstalacion_cierra_la_app(self):
        # el desinstalador de Inno no aplica CloseApplications (solo el
        # setup): sin taskkill en [UninstallRun] aborta rc=1 en VERYSILENT
        self.assertIn("[UninstallRun]", self.iss)
        self.assertIn("taskkill", self.iss)

    def test_build_ps1_compila_el_instalador(self):
        with open(os.path.join(HERE, "build.ps1"), encoding="utf-8") as f:
            b = f.read()
        self.assertIn("installer.iss", b)
        self.assertIn("ISCC", b)


if __name__ == "__main__":
    unittest.main(verbosity=2)
