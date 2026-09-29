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
| POST | `/api/ping` | `{tab}` latido de pestaña para el autoapagado (sin auth) |
| GET | `/api/health` | `200 {ok: true, version}` |
| POST | `/api/telemetry` | Lote `{events}` de telemetría opt-in (catálogo cerrado, 403 sin consentimiento) |
| GET | `/api/telemetry-consent` | `{enabled, asked}` estado del opt-in |
| PUT | `/api/telemetry-consent` | `{enabled}` activa/revoca (revocar borra lo acumulado) |
| GET | `/api/telemetry-summary` | Agregado local de 30 días |
| POST | `/api/telemetry-forward` | Disparo manual del envío al receptor |
| GET | `/api/telemetry-forward-status` | Estado del forward (receptor, fallos, medias) |
| POST | `/api/telemetry-ingest` | Relay público hacia el sink local (betas sin tailnet) |
