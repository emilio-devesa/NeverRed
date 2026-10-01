---
layout: page
title: API
permalink: /api/
---

# API

Sesión en cookie `HttpOnly` (`Bearer` solo para el modo archivo local). CORS restringido al mismo origen.

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/register` | `{name, email, password}` → `201 {token, user}` (409 si existe) |
| POST | `/api/login` | `{email, password}` → `200 {token, user}` (401 si falla, 429 con rate-limit) |
| POST | `/api/logout` | Cierra la sesión actual |
| POST | `/api/password` | `{current, new}` cambia la clave y cierra otras sesiones |
| POST | `/api/reset-request` | `{email}` envía enlace de recuperación (1 h) |
| POST | `/api/reset-confirm` | `{token, new}` completa la recuperación |
| POST | `/api/demo` | Crea o restablece la demo y abre sesión (sin registro) |
| DELETE | `/api/account` | Elimina el usuario y todos sus datos |
| GET | `/api/me` | Usuario actual |
| GET | `/api/sessions` | Sesiones propias |
| POST | `/api/sessions/rotate` | Cierra las demás sesiones |
| GET | `/api/data` | Contabilidad del usuario |
| PUT | `/api/data` | Guarda `{accounts, entries, seq, currency, budgets, recurring}` (validado: asientos cuadrados) |
| GET | `/api/market/status` | ¿Hay clave propia? + nº de tickers + cuota diaria restante |
| POST | `/api/market/key` | `{key}` guarda tu clave personal de Alpha Vantage (solo vive en el servidor) |
| DELETE | `/api/market/key` | Olvida tu clave de Alpha Vantage |
| GET | `/api/market/tickers` | Tus tickers con último cierre y tendencia (de caché, sin gastar cuota) |
| POST | `/api/market/tickers` | `{symbol}` añade un ticker (valida y descarga su serie) |
| DELETE | `/api/market/tickers?symbol=X` | Quita un ticker |
| GET | `/api/market/history?symbol=X` | Serie (~100 sesiones) + mín/máx/variación (`&refresh=1` fuerza descarga) |
| POST | `/api/ping` | `{tab}` latido de pestaña para el autoapagado (sin auth) |
| GET | `/api/health` | `200 {ok: true, version}` |
| POST | `/api/telemetry` | Envío de telemetría opt-in (solo conteos, 403 sin consentimiento) |
| GET | `/api/telemetry-consent` | Estado de la opción de telemetría |
| PUT | `/api/telemetry-consent` | Activa o desactiva (desactivar borra lo acumulado) |
