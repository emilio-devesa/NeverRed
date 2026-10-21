---
layout: page
title: Desarrollo
permalink: /desarrollo/
---

# Desarrollo

Sin dependencias de terceros: Python stdlib en el backend y JS vainilla en el frontal.

## Estructura

- `app.js` + `lib/contabilidad.js` (lógica pura compartida con los tests)
- `index.html`, `styles.css`, `icon.svg`, PWA (`manifest.webmanifest`, `sw.js`)
- `backend/`:
  - `server.py` — API HTTP (Handler, rutas, sesiones) y arranque
  - `config.py` — rutas, directorios y la **versión única** (ahora `2.6.8`)
  - `schema.py` — conexión SQLite (WAL, FKs), esquema y migraciones
  - `auth.py` — contraseñas (PBKDF2), usuarios y validación
  - `static.py` — lista blanca, ETag/gzip/304, CSP
  - `market.py` — proxy Alpha Vantage y caché por símbolo
  - `telemetry.py`, `telemetry_forward.py`, `telemetry_sink.py` — telemetría opt-in (store, envío firmado y dashboard)
  - `backup.py`, `seed_demo.py`, `tests/`
- `frontend/tests/` — tests node de la lógica pura (contabilidad, mercado, simuladores y SW)
- `packaging/macos/` (`.app` + `.dmg` universal), `packaging/linux/` (AppImage) y `packaging/update-common.sh` (firma Ed25519, verificación y actualización comunes)
- `Dockerfile`, `compose.yaml`, `Caddyfile`; CI y workflows de release en `.github/workflows/`
- `docs/ops/` — operación redactada de telemetría y releases (sin secretos)

## Tests

```bash
python3 backend/tests/test_server.py               # 32
python3 backend/tests/test_market.py               # 19
python3 backend/tests/test_telemetry_forward.py    # 9
python3 backend/tests/test_telemetry_pairing.py    # 11
python3 backend/tests/test_integration.py          # 2 (integración HTTP)
node --test frontend/tests/*.test.js               # 42
```

Total: **115 tests**. El glob `*.test.js` es necesario: `node --test frontend/tests/` falla en Node ≥ 22. CI en cada push/PR (`.github/workflows/ci.yml`).

## Releases

Cada tag `v*` lanza CI, publica la imagen Docker en GHCR, genera el `.dmg` universal de macOS y el AppImage de Linux, y crea la Release con las notas del `CHANGELOG.md`:

```bash
git tag -a v2.6.8 -m "NeverRed v2.6.8" && git push origin v2.6.8
```

Tras publicar, firma los assets (`ssh-keygen -Y sign`, Ed25519) y sube sus `.sig` a la Release: sin firma la app no instala actualizaciones (falla cerrado). El procedimiento completo está en `docs/ops/releases.md` del repositorio.

Código en [github.com/emilio-devesa/NeverRed](https://github.com/emilio-devesa/NeverRed).
