# Publicación de releases (copia redactada)

> Copia pública y redactada del procedimiento privado. No contiene claves,
> tokens ni URLs de infraestructura. La versión completa sigue fuera del
> repositorio.

## Checklist

1. **Versión en los tres sitios:**
   - `VERSION` en `backend/config.py`
   - `NEVERRED_BUILD` en `app.js`
   - `styles.css?v=X.Y.Z` en `index.html`

   Si cambia la carcasa o el service worker, subir también `CACHE` en
   `sw.js` (por ejemplo `neverred-v8`). Sin esto se puede servir CSS viejo
   con HTML nuevo.

2. **CHANGELOG:** añadir una entrada `## vX.Y.Z — resumen` en
   `CHANGELOG.md`. El workflow de release la usa para las notas.

3. **Tests (5 suites backend + frontal):**

   ```bash
   python3 backend/tests/test_server.py
   python3 backend/tests/test_market.py
   python3 backend/tests/test_telemetry_forward.py
   python3 backend/tests/test_telemetry_pairing.py
   python3 backend/tests/test_integration.py
   node --test frontend/tests/*.test.js
   ```

   Estado actual: 38 + 20 + 9 + 19 + 2 backend y 42 frontal = **130**.

4. **Commit y tag:**

   ```bash
   git add -A
   git commit -m "vX.Y.Z: resumen"
   git push origin main
   git tag -a vX.Y.Z -m "NeverRed vX.Y.Z: resumen"
   git push origin vX.Y.Z
   ```

5. **Workflows del tag:** CI, DMG macOS, AppImage Linux, Release y Windows.
   Comprobar que los cinco terminan en `success` y que la Release adjunta el
   `.dmg`, el `.AppImage`, los zips de Windows y el instalador
   `NeverRed-setup-<v>.exe` (más sus `.sig`, añadidos a mano tras firmar).

## Firma de los assets

La app solo instala actualizaciones con firma Ed25519 válida. Tras publicar
la Release, firmar cada asset y subir el `.sig`:

```bash
# El asset exacto depende de la versión: dmg, AppImage, ambos zips de
# Windows y el instalador NeverRed-setup-<v>.exe. Para cada uno:
ssh-keygen -Y sign -f packaging/release-key -n neverred-update <asset>
gh release upload vX.Y.Z <asset>.sig
```

- La clave privada no está en git; la pública sí y va embebida en los
  scripts de actualización.
- Guardar una copia cifrada de la privada fuera del equipo.
- Si se pierde, no se pueden firmar más actualizaciones. Si se filtra, rotar
  el par y mantener ambas firmas durante una versión de gracia.
- Sin `.sig` no hay auto-update: el actualizador falla cerrado.

### Sin firma de plataforma (codesign / Authenticode)

Decisión deliberada, paridad con el DMG (no codesign): los binarios
Windows van **sin Authenticode**. El equivalente Windows del "Abrir
igualmente" de Gatekeeper es el aviso de SmartScreen
("Windows protegió tu PC → Más información → Ejecutar de todas formas"),
que se atenua con la prevalencia del binario. Si algún día se compra un
certificado Authenticode, configurarlo en `installer.iss` con `SignTool`
y **`SignedUninstaller=yes`** (los desinstaladores/temporales sin firmar
pueden ser bloqueados por el Attack Surface Reduction de Defender). La
confianza la sigue dando la `.sig` Ed25519, no el certificado.

## Destino de telemetría del build

`packaging/macos/build.sh`, `packaging/linux/build.sh` y
`packaging/windows/build.ps1` aceptan `NEVERRED_BUILD_SINK` y
`NEVERRED_BUILD_TOKEN` (token vacío por defecto). Sustituyen los marcadores
en `Launcher.sh` / `AppRun` / `launcher.py`; el lanzador escribe
`telemetry.conf` en el primer arranque. Para una release sin destino:

```bash
NEVERRED_BUILD_SINK= ./packaging/macos/build.sh X.Y.Z
NEVERRED_BUILD_SINK= ./packaging/linux/build.sh X.Y.Z
```

En Windows, si no vienen ni por parámetro ni por entorno se usa el mismo
destino default que mac/Linux; un build sin destino se pide explícito
(PowerShell no distingue entorno vacío de no definido):

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -SinkUrl ''
```

Verificar en un paquete instalado que `telemetry.conf` contiene lo esperado.

## Límites conocidos de este equipo

- Docker no está instalado: la imagen y `.dockerignore` no se construyen aquí.
- El AppImage necesita Linux; en macOS solo se valida `sh -n` y los stubs.
- La instalación real de una actualización macOS requiere una Release firmada
  y la clave privada; la prueba positiva usa una clave temporal.
