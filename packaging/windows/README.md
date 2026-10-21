# NeverRed para Windows (fase nivel 2)

Estado: implementado y probado **lógicamente** en macOS (selftest 23/23 —
en una Windows XP sin OpenSSH: 22 + 1 skip) y **parcialmente en Windows real
(Windows XP SP3, Python 3.4.4 + PyInstaller 3.5)**: los dos `.exe` se generan
allá y responden `--version`; el `--check` del actualizador completa contra
GitHub (ver «Lo probado en Windows real (VM XP)»). El resto —doble clic,
diálogo tkinter y el swap real— sigue en «Pendiente de probar en Windows».
**Nada de esto está commiteado todavía.**

## Estructura

```
packaging/windows/
├─ launcher.py         lanzador (equivale al Launcher.sh de macOS)
├─ update_run.py       actualizador (equivale a check-update.sh + update-common.sh)
├─ NeverRed.bat         arranque portable (doble clic; requiere Python 3.12+)
├─ NeverRed-stop.bat    detiene el servidor portable por PID
├─ build.ps1            build para Windows: .exe (PyInstaller) + ZIP portable
├─ cacert.pem           bundle de CAs (curl.se) para TLS en Windows sin roots modernos
└─ selftest.py          23 pruebas de la lógica (corren en cualquier SO)
.github/workflows/packaging-windows.yml   CI en tags v* (windows-latest)
```

## Cómo corre la app en Windows

- **Datos:** `%APPDATA%\NeverRed` (launcher exporta `NEVERRED_DB`; no se toca
  `backend/config.py`). Igual que macOS guarda en `~/Library/Application
  Support/NeverRed`.
- **Servidor:** hijo del launcher (`launcher.py --serve`), escucha en
  `127.0.0.1:8000` (puerto configurado). Con `HOST=127.0.0.1` no salta el
  firewall de Windows en la primera ejecución.
- **Autoapagado:** paridad con macOS: `NEVERRED_QUIT_WHEN_IDLE=1` + latidos
  (`/api/ping`); 20 s tras cerrar la última pestaña se apaga todo.
- **Vigía padre-hijo:** sin `trap` de señales (no existen en Windows), el
  hijo `--serve` vigila al launcher y muere con él (`process_alive` vía
  `OpenProcess`/`os.kill(pid, 0)`).

## Actualizaciones (mismo criterio Ed25519 que macOS/Linux)

1. Al abrir, el launcher ejecuta `NeverRed-Update.exe --check`
   (portable: `update_run.py --check`).
2. Consulta la última release de GitHub, filtra el activo
   `NeverRed-windows-<v>.zip` (exe) o `...-portable.zip` (portable) y su `.sig`.
3. Descarga, verifica con `ssh-keygen -Y verify` (clave pública embebida,
   identidad `neverred-release`, namespace `neverred-update`). Con OpenSSH
   < 8.9 (algunos Windows 10) falla cerrado y lo deja en el `update.log`.
   TLS: se usa el almacén del sistema; si ese almacén no trae los roots
   actuales (Windows XP) se usa `cacert.pem` junto al exe (o la ruta de
   `NEVERRED_CA`). Sin almacén ni bundle: falla cerrado («sin red»).
4. Diálogo tkinter (mismo criterio que mac: Instalar / Omitir versión /
   Más tarde; se recuerda la versión omitida).
5. Intercambio seguro: extrae el zip a un directorio HERMANO
   (`.NeverRed.update-new`), lo valida (versión + estructura), escribe
   `update.cmd` en el directorio de datos y se desvincula. El launcher sale
   (código 42) y el `.cmd`: para el servidor por PID (`taskkill /T /F`),
   aparta la instalación vieja, mueve la nueva, verifica
   `.update_version`, relanza y se borra a sí mismo. Todo con vuelta atrás.
6. Si un intercambio se queda a medias (fallo del swap), el siguiente
   arranque detecta `.update_pending` sin `.update_ok`, quita la marca de
   «omitido» y vuelve a ofrecer la actualización.

## Build del .exe (en tu entorno de Windows)

```powershell
python -m pip install pyinstaller
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 `
  [-Version 2.6.9] [-SkipExe]
```

- Produce `dist/windows/NeverRed-windows-<v>.zip` (NeverRed.exe +
  NeverRed-Update.exe + app) y `...-portable.zip` (mismo árbol, sin `.exe`,
  con los `.bat` y los scripts Python en `packaging/windows/`).
- Icono: si existe `assets/icon.ico` se aplica (convierte `icon.svg` a `.ico`
  una vez; p. ej. con ImageMagick: `magick -background none icon.svg -define
  icon:auto-resize=16,32,48,256 assets/icon.ico`).
- Telemetría del build: `-SinkUrl`, `-SinkToken` (vacíos = local pura), igual
  que NEVERRED_BUILD_SINK en los builds de mac/Linux.
- Tras publicar, firma cada zip y sube sus `.sig` (docs/ops/releases.md):
  el flujo Ed25519 es el mismo.

## Instalador de Windows (Inno Setup 6)

`build.ps1` genera automáticamente el instalador si encuentra Inno Setup 6
(winget: `JRSoftware.InnoSetup`):

```powershell
winget install --id JRSoftware.InnoSetup -e --source winget
python -m pip install pyinstaller
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -Version 2.6.8
# -> dist\windows\NeverRed-setup-2.6.8.exe  (+ los zips y el árbol en dist\windows\NeverRed)
```

Características del instalador (`installer.iss`):

- **Per-usuario, sin UAC** (`PrivilegesRequired=lowest`): instala en
  `%LOCALAPPDATA%\NeverRed`. Razón: el auto-update renombra su propio
  directorio al aplicar una release; en `Program Files` eso exigiría
  elevación constante. Los **datos** del usuario viven en
  `%APPDATA%\NeverRed` y sobreviven a cualquier desinstalación.
- Incluye el árbol completo del build (`NeverRed.exe`,
  `NeverRed-Update.exe`, app web, `backend/` como ficheros, `cacert.pem`).
- Desinstalación integrada en «Apps y características»
  (`{9E7A2C1F-4B8D-4E3A-9F1C-6D2B5A7C8E90}_is1` en
  `HKCU\...\Uninstall`). La clave como GUID es la convención estándar de
  Inno; el nombre visible es «NeverRed».
- `AppMutex=NeverRedAppMutex`: el launcher mantiene ese mutex mientras
  corre (`hold_app_mutex()`), de modo que el setup detecta si la app está
  en marcha al **actualizar** por encima de una instalación (con
  `CloseApplications=force`).
- Asistente en español; tareas opcionales de accesos directos (menú inicio
  + escritorio); ejecución opcional al terminar.
- Modo silencioso soportado: `NeverRed-setup-<v>.exe /VERYSILENT
  /SUPPRESSMSGBOXES /NORESTART`.

Limitación conocida (verificada en Windows real, Inno 6.7.3): el
**desinstalador** silencioso de Inno aborta `rc=1` sin tocar nada si la
app está en marcha — el desinstalador de Inno no aplica
`CloseApplications` ni `AppMutex` (solo el setup lo hace); los hooks
`[UninstallRun]`/`InitializeUninstall` con `taskkill` quedan como barra
de seguridad pero el aborto a nivel de engine ocurre antes. En la
práctica no afecta al usuario final: la app **autoapaga el servidor ~20 s
después de cerrar la última pestaña** y el asistente interactivo (vía
«Apps y características») espera al usuario con normalidad. La
desinstalación silenciosa funciona siempre que la app no esté en marcha
y preserva siempre los datos.

### Firma (paridad con dmg/AppImage)

Como en macOS y Linux, los binarios Windows van **sin firma de
plataforma** (sin Authenticode): la confianza la da la `.sig` Ed25519
del proyecto que se publica junto a cada asset (`docs/ops/releases.md`).
El `.exe` va en la Release **sin firmar Authenticode**: Windows puede
mostrar el aviso de SmartScreen («Windows protegió tu PC → Más
información → Ejecutar de todas formas»), análogo al «Abrir igualmente»
de Gatekeeper en el DMG. Tras firmar el asset con la clave Ed25519 del
proyecto:

```bash
ssh-keygen -Y sign -f packaging/release-key -n neverred-update NeverRed-setup-<v>.exe
gh release upload vX.Y.Z NeverRed-setup-<v>.exe.sig
```

Si algún día se usa un certificado Authenticode, configurar `SignTool` +
`SignedUninstaller=yes` en `installer.iss` (los desinstaladores sin
firmar pueden ser bloqueados por Defender ASR).

## Lo probado en Windows real (VM de pruebas Win11, build 10.0.26200)

Máquina: Windows 11 Pro con acceso por SSH (sshd de terceros, sesiones
cortas). Python **3.12** (winget `Python.Python.3.12`, instalación
per-usuario) + pip + PyInstaller 6.22.3. Se ha ejecutado `build.ps1` tal
cual y el instalador end-to-end. Hallazgos aplicados:

- `build.ps1` completo en Win11: PyInstaller 6 genera ambos `.exe`, los
  chequeos y los dos zips. Bugs pillados y arreglos:
  - `Copy-Item` con destino inexistente escribe el fichero COMO ese
    destino: sin `New-Item` previo del directorio `backend`, los módulos
    acababan en UN fichero llamado `backend` y el exe leía `--version →
    0.0.0`. Ahora el directorio se crea explícitamente antes.
  - El chequeo `& exe --version` en PowerShell no espera a exes GUI
    (`--noconsole`): cambiado a `Start-Process -Wait` con redirección a
    fichero.
  - Copias al `portable\packaging\windows` antes de crear el directorio
    (corte del primer intento de build; orden arreglado).
- **stdlib embebida**: en un exe congelado, PyInstaller solo embebe lo
  que el entry-point importa estáticamente; el hijo `--serve` carga
  `server.py` desde disco y fallaba con `ModuleNotFoundError: No module
  named 'json'`. El launcher ahora importa la unión de stdlib de los 9
  módulos backend (bloque documentado en `launcher.py`).
- Mutex de app (`ctypes` en launcher) para `AppMutex`/`CloseApplications`
  de Inno.
- Instalador Inno 6.7.3 (winget `JRSoftware.InnoSetup`): compilación con
  ISCC (~10–20 s) y ciclo completo verificado en Win11 (silencioso):
  install rc=0 → 14 entradas en `%LOCALAPPDATA%\NeverRed` →
  `--version` → 2.6.8 → ejecución real con `health` HTTP 200 y DB en
  `%APPDATA%\NeverRed` → uninstall rc=0 (dir, accesos y registro fuera,
  datos preservados). Reinstalación idempotente OK.
- Quirks del entorno SSH/cmd en Win11 (para quien repita pruebas):
  `%VAR%` no se expande en la misma línea de cmd; comandos que empiezan
  por entrecomillado disparan «etiqueta del volumen»; los exes GUI no se
  esperan ni desde cmd ni desde PowerShell sin `Start-Process -Wait`;
  para cualquier comprobación seria, usar Python (`subprocess.Popen` +
  `communicate`). Mejor no usar `reg query` en cascada: python `winreg`.

## Lo probado en Windows real (VM de pruebas XP)

Máquina de prueba: Windows XP SP3 (5.1.2600), Python 3.4.4 32-bit, pip 19.1.1,
PyInstaller 3.5 (el último compatible con 3.4). Hallazgos aplicados:

- `buildvm.py` (equivalente de build.ps1 sin PowerShell) genera ambos `.exe`
  en XP: `NeverRed.exe` (≈4,8 MB) y `NeverRed-Update.exe` (≈7,4 MB, Tcl/Tk
  incluido); ambos responden `--version` → la versión (rc=0).
- Bugs pillados y arreglados al probar en Windows real:
  - `time.strftime("%F")` no existe en el CRT de Windows → `ValueError` al
    escribir `update.log` en cualquier Windows. Arreglado con
    `%Y-%m-%d %H:%M:%S` (launcher.py y update_run.py).
  - `unskip_if_failed_swap` borraba `skipped_version` **dentro** del `with
    open(sp)` → `PermissionError` en Windows (no se puede borrar un fichero
    que el propio proceso tiene abierto). Arreglado: leer, cerrar y borrar.
  - La XP no tiene en su almacén los roots TLS de `api.github.com` →
    `CERTIFICATE_VERIFY_FAILED`. Fallback: `cacert.pem` junto al exe
    (`ca_context()` en update_run.py).
- `update_run.py --check` end-to-end en XP (exe congelado y Python plano):
  fetch de la release vía `cacert.pem`, «sin activo de Windows para v2.6.8»,
  rc=0; el `update.log` queda bien tarjado.
- Pip/PyPI con TLS 1.2 desde XP funciona vía OpenSSL 1.0.2d.
- Limitación conocida de la XP: el código del servidor (committeado) usa
  sintaxis/APIs de Python 3.5–3.7 (`{**data}` en server.py, literales 200_000
  en auth.py/market.py/telemetry_common.py, `ThreadingHTTPServer`, `secrets`,
  `capture_output`) y no arranca en su Python 3.4. Para soportar XP como
  máquina de *ejecución* haría falta un backport de compatibilidad en ficheros
  commiteados — decisión aparte.

## Pendiente de probar en Windows

- [x] `NeverRed.exe --version` y `NeverRed-Update.exe --version` en Windows
      real — XP (PyInstaller 3.5/buildvm.py) y Win11 (`build.ps1` completo,
      PyInstaller 6.22.3; incluye el paso del instalador si está Inno Setup).
- [x] Doble clic en `NeverRed.exe` → servidor + `health` HTTP 200 + datos en
      `%APPDATA%\NeverRed` (verificado en Win11 lanzando el exe instalado).
- [x] Instalador end-to-end en Win11 (Inno 6.7.3): install/desinstall
      silenciosos rc=0, datos preservados, reinstalación idempotente,
      ejecución real con `health` 200.
- [ ] ZIP portable: `NeverRed.bat` con Python instalado → también funciona.
- [ ] `NeverRed-stop.bat` detiene; y cerrar la última pestaña autoapaga.
- [ ] Firewall no salta (localhost) — y si salta, permitir una vez.
- [ ] Update end-to-end: con la app instalada y una release con zip firmado,
      comprobar el diálogo, descarga, firma y swap con relanza limpia
      (mirar `%APPDATA%\NeverRed\update.log`). El `--check` sin activo ya
      está probado en la XP (exe congelado y Python plano).
- [ ] Swap con rutas con espacios y con la instalación en `C:\Archivos de
      programa\` (el `ren` de cmd solo acepta un nombre, no ruta: por eso va
      suelto en `%~7` — verificarlo en un caso real).
- [ ] Diálogo tkinter en Windows (fuentes/escalado).

## Hooks de prueba

- `NEVERRED_APPDATA_DATA` — anula el directorio de datos (sandbox).
- `NEVERRED_NO_UPDATE=1` — sin comprobación de updates.
- `NEVERRED_BROWSER=none` — no abre navegador.
- `NEVERRED_UPDATE_CHOICE=install|skip|later` — respuesta fija del diálogo
  (no abre ventana; para automatizar).
- `NEVERRED_CA` — fuerza la ruta de un bundle de CAs para el TLS del
  actualizador (si no, usa `cacert.pem` junto al exe si existe).
- `python3 packaging/windows/selftest.py` — 23 unittests del paquete
  (en Windows sin OpenSSH: 22 + 1 skip).
