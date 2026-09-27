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
- `backend/server.py` (API + SQLite + estáticos), `backup.py`, `seed_demo.py`, `tests/`
- `packaging/macos/` (`.app` + `.dmg` universal) y `packaging/linux/` (AppImage), `Dockerfile`, `compose.yaml`, `Caddyfile`

## Tests

```bash
python3 backend/tests/test_server.py   # 14 tests
node --test frontend/tests/            # lógica contable
```

Hay CI en cada push/PR (`.github/workflows/ci.yml`).

## Releases

Cada tag `v*` verifica todo, publica la imagen Docker en GHCR, genera el `.dmg` universal de macOS y el AppImage de Linux, y crea la Release:

```bash
git tag -a v1.3.0 -m "NeverRed v1.3.0" && git push origin v1.3.0
```

Código en [github.com/emilio-devesa/NeverRed](https://github.com/emilio-devesa/NeverRed).
