---
layout: page
title: Despliegue
permalink: /despliegue/
---

# Despliegue

## Docker (recomendado)

```bash
docker run -d --name neverred -p 8000:8000 \
  -v neverred-data:/data --restart unless-stopped \
  ghcr.io/emilio-devesa/neverred:latest
```

O con el `compose.yaml` incluido: `docker compose up -d --build`. La base de datos persiste en el volumen.

## VPS clásico

```bash
python3 backend/server.py --host 0.0.0.0 --port 8000
```

Sirve siempre por **HTTPS**: tras Caddy con TLS automático (ejemplo en `Caddyfile`) o TLS directo con `NEVERRED_TLS_CERT`/`NEVERRED_TLS_KEY`.

## Variables de entorno

| Variable | Defecto | Para qué |
|---|---|---|
| `HOST`, `PORT` | `127.0.0.1`, `8000` | Escucha |
| `NEVERRED_DB` | `backend/neverred.db` | Ruta de la BD (`/data/neverred.db` en Docker) |
| `NEVERRED_TLS_CERT`, `NEVERRED_TLS_KEY` | — | HTTPS directo |
| `NEVERRED_SESSION_DAYS` | `30` | Caducidad de sesiones |
| `NEVERRED_QUIT_WHEN_IDLE` | — | `1` = apaga el servidor al cerrar la última pestaña (modo `.app` macOS) |
| `NEVERRED_IDLE_TIMEOUT` | `20` | Segundos sin latidos antes de apagar |
| `NEVERRED_RATE_MAX`, `NEVERRED_RATE_WINDOW` | `10`, `600` | Anti fuerza bruta |
| `NEVERRED_SMTP_HOST/PORT/USER/PASS/FROM` | — | Correos de recuperación |
| `NEVERRED_APP_URL` | `http://127.0.0.1:8000` | Enlaces del correo |
| `NEVERRED_VERSION` | `1.0.0` | Versión en `/api/health` |

## Copias de seguridad

```bash
python3 backend/backup.py   # backend/backups/, conserva las 14 últimas
```

Automatízala por cron cada noche (ver cabecera del script). La app también exporta JSON manualmente.
