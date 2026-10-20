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

   Estado actual: 32 + 19 + 9 + 11 + 2 backend y 42 frontal = **115**.

4. **Commit y tag:**

   ```bash
   git add -A
   git commit -m "vX.Y.Z: resumen"
   git push origin main
   git tag -a vX.Y.Z -m "NeverRed vX.Y.Z: resumen"
   git push origin vX.Y.Z
   ```

5. **Workflows del tag:** CI, DMG macOS, AppImage Linux y Release. Comprobar
   que los cuatro terminan en `success` y que la Release adjunta el `.dmg`,
   el `.AppImage` y sus `.sig`.

## Firma de los assets

La app solo instala actualizaciones con firma Ed25519 válida. Tras publicar
la Release, firmar cada asset y subir el `.sig`:

```bash
ssh-keygen -Y sign -f packaging/release-key -n neverred-update <asset>
gh release upload vX.Y.Z <asset>.sig
```

- La clave privada no está en git; la pública sí y va embebida en los
  scripts de actualización.
- Guardar una copia cifrada de la privada fuera del equipo.
- Si se pierde, no se pueden firmar más actualizaciones. Si se filtra, rotar
  el par y mantener ambas firmas durante una versión de gracia.
- Sin `.sig` no hay auto-update: el actualizador falla cerrado.

## Destino de telemetría del build

`packaging/macos/build.sh` y `packaging/linux/build.sh` aceptan
`NEVERRED_BUILD_SINK` y `NEVERRED_BUILD_TOKEN` (token vacío por defecto).
Sustituyen los marcadores en `Launcher.sh` / `AppRun`; el lanzador escribe
`telemetry.conf` en el primer arranque. Para una release sin destino:

```bash
NEVERRED_BUILD_SINK= ./packaging/macos/build.sh X.Y.Z
NEVERRED_BUILD_SINK= ./packaging/linux/build.sh X.Y.Z
```

Verificar en un paquete instalado que `telemetry.conf` contiene lo esperado.

## Límites conocidos de este equipo

- Docker no está instalado: la imagen y `.dockerignore` no se construyen aquí.
- El AppImage necesita Linux; en macOS solo se valida `sh -n` y los stubs.
- La instalación real de una actualización macOS requiere una Release firmada
  y la clave privada; la prueba positiva usa una clave temporal.
