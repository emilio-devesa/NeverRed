# NeverRed 🟢

Contabilidad personal con el sistema de **doble partida**. Simple para empezar, rigurosa para mantener.

> Cada euro tiene dos caras: de dónde viene y a dónde va. Si **debe = haber**, nunca estarás en rojo sin saberlo.

## Descargar para macOS (sin instalar nada)

1. Descarga el `.dmg` de tu Mac (Intel o Apple Silicon) desde
   **Releases** en GitHub.
2. Ábrelo y arrastra **NeverRed** a **Aplicaciones**.
3. Doble clic en NeverRed: se abre en tu navegador. Listo.

No pide instalar Python ni nada más (usa lo que ya trae macOS). Tus datos
quedan en tu Mac (`~/Library/Application Support/NeverRed`).

## Puesta en marcha (con usuarios y base de datos)

La app requiere registrarse con correo y contraseña antes de usarla. Los usuarios y sus datos contables se guardan en una base de datos SQLite a través del servidor incluido (solo biblioteca estándar de Python, sin dependencias):

```bash
python3 backend/server.py
```

Abre entonces **http://127.0.0.1:8000** y crea tu cuenta. Opciones: `python3 backend/server.py --port 8001 --host 0.0.0.0`.

> ⚠️ Importante: abre la app desde **http://127.0.0.1:8000**, no con doble clic en `index.html`.
> El archivo abierto directamente (`file://`) no puede guardar en la base de datos; la propia app
> te avisa y te ofrece el enlace correcto más un botón de reintento.

- La BD se crea sola en `backend/neverred.db` (tablas `users`, `sessions`, `user_data`).
- Las contraseñas **nunca** se guardan en texto plano: hash PBKDF2-HMAC-SHA256 con sal aleatoria.
- Las sesiones usan token aleatorio con caducidad de 30 días, servido en
  **cookie `HttpOnly` + `SameSite=Lax`** (`Secure` con TLS). El `Bearer` solo
  se usa en el modo archivo local.
- **Recuperar contraseña**: enlace de un solo uso (1 h) por correo con SMTP
  estándar (`NEVERRED_SMTP_HOST/PORT/USER/PASS/FROM`, `NEVERRED_APP_URL`).
  Sin SMTP, el enlace sale por la consola (desarrollo).
- **Sesiones**: listado propio y rotación (`GET /api/sessions`,
  `POST /api/sessions/rotate`); caducidad con `NEVERRED_SESSION_DAYS`.
- **Auditoría**: `audit_log` registra accesos, cambios de clave, resets y borrados.
- **Rate-limit**: máx. 10 intentos de login/registro por IP cada 10 min (`NEVERRED_RATE_MAX/WINDOW`).
- **CORS restringido** al mismo origen y al modo archivo local.
- **HTTPS**: con `NEVERRED_TLS_CERT` + `NEVERRED_TLS_KEY` el servidor habla TLS. En producción, usa TLS (directo o tras Caddy/Nginx).
- Cada guardado en la app se sincroniza con la BD (con copia local por usuario como caché).
- **Cada usuario tiene su propia contabilidad aislada**: al registrar una cuenta nueva se parte del plan base vacío; al cerrar sesión se limpia el estado en memoria y nadie hereda los datos de otro usuario.
- **Tu cuenta es tuya**: puedes cambiar la contraseña (cierra las demás sesiones) o eliminar tu cuenta y todos tus datos desde el pie de la app.

> Sin el servidor en marcha (p. ej. abriendo `index.html` directamente), la pantalla de acceso avisará de que no hay conexión.

## Qué incluye

- **Asistente de inicio**: crea tu asiento de apertura (efectivo + banco − deudas = capital inicial).
- **Libro diario**: asientos con N líneas, validación `Debe = Haber`, edición, borrado, filtros y búsqueda.
- **Libro mayor**: movimientos y saldo acumulado por cuenta, con naturaleza deudora/acreedora.
- **Plan de cuentas**: 5 familias (Activo, Pasivo, Patrimonio, Ingreso, Gasto), crear/editar/archivar.
- **Informes**: balance de comprobación (con CSV), cuenta de resultados (PyG) y balance general.
- **Panel**: patrimonio neto, ecuación fundamental en vivo, gráfico ingresos vs gastos 6 meses, accesos rápidos (sueldo, gasto, transferencia).
- **Presupuestos**: límite mensual por gasto con avisos al 80 % y al superar.
- **Recurrentes**: plantillas mensuales (nómina, alquiler) con generación sin duplicados.
- **Importar CSV** del banco (`fecha;descripción;importe`) como asientos cuadrados.
- **PWA**: instalable y carcasa offline (la API necesita red).
- **Datos locales**: todo se guarda en `localStorage`. Exporta/importa JSON para copia de seguridad.

## Capturas

![Vista general de la contabilidad](docs/neverred.jpeg)
![Libro diario](docs/diario.jpeg)
![Libro mayor](docs/mayor.jpeg)

## Reglas contables aplicadas

- Todo asiento exige ≥ 2 líneas, importes > 0 y `Σ Debe = Σ Haber`.
- Ninguna línea mezcla debe y haber.
- Activo y Gasto son de naturaleza **deudora** (suben por el debe); Pasivo, Patrimonio e Ingreso, **acreedora** (suben por el haber).
- Ecuación verificada en vivo: `Activo = Pasivo + Patrimonio + (Ingresos − Gastos)`.

## Estructura

```
index.html  — vistas: inicio, diario, mayor, cuentas, informes + modales y acceso
styles.css  — tema oscuro/claro, responsive
app.js      — estado, lógica contable, render, sincronización con la API (sin librerías)
icon.svg    — icono pixelado estilo panel de bolsa
backend/server.py — API (registro/login/sesiones/datos) + SQLite + servidor estático
backend/seed_demo.py — genera el usuario demo con 6 meses de movimientos
backend/neverred.db — base de datos (se crea al arrancar; no versionar con datos reales)
Dockerfile, compose.yaml, .dockerignore — empaquetado Docker
.github/workflows/release.yml — release automática al publicar tags v*
```

## API

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/register` | `{name, email, password}` → `201 {token, user}` (409 si el correo existe) |
| POST | `/api/login` | `{email, password}` → `200 {token, user}` (401 si falla) |
| POST | `/api/logout` | Invalida el token (cabecera `Authorization: Bearer …`) |
| POST | `/api/password` | `{current, new}` cambia la contraseña y cierra otras sesiones |
| POST | `/api/reset-request` | `{email}` envía enlace de recuperación (1 h, sin filtrar usuarios) |
| POST | `/api/reset-confirm` | `{token, new}` completa la recuperación |
| DELETE | `/api/account` | Elimina el usuario y todos sus datos |
| GET | `/api/me` | Usuario de la sesión actual |
| GET | `/api/data` | Datos contables del usuario |
| GET | `/api/health` | Salud del servicio → `200 {ok: true, version}` (usado por Docker) |

## Despliegue

### Opción A — Docker (recomendada)

```bash
docker build -t neverred .
docker run -d --name neverred -p 8000:8000 -v neverred-data:/data --restart unless-stopped neverred
```

O con Compose (incluido `compose.yaml`):

```bash
docker compose up -d --build
```

- La BD vive en el volumen (`/data/neverred.db` vía `NEVERRED_DB`) y sobrevive a rebuilds.
- Variables: `HOST` (por defecto `0.0.0.0` en Docker), `PORT`, `NEVERRED_DB`.
- La imagen usa `python:3.12-slim`, usuario no-root y healthcheck contra `/api/health`.

### Opción B — Servidor/VPS clásico

```bash
python3 backend/server.py --host 0.0.0.0 --port 8000
```

Para producción, sirve siempre por **HTTPS**: tras un proxy con TLS automático
(Caddy, con ejemplo listo en `Caddyfile`) o TLS directo con
`NEVERRED_TLS_CERT`/`NEVERRED_TLS_KEY`, y supervísalo
con systemd o similar. Copias de seguridad:

```bash
python3 backend/backup.py              # backend/backups/, conserva las 14 últimas
```

o por cron cada noche (ver cabecera del script). La app también permite
Exportar JSON manual. Tests:

```bash
python3 backend/tests/test_server.py   # backend: 12 tests (solo stdlib)
node --test frontend/tests/            # frontal: lógica contable (sin dependencias)
```

## Releases (automáticas con GitHub Actions)

Publicar un tag `v*` dispara el workflow `Release`, que verifica el backend
(health + registro + guardado de datos), construye la imagen Docker, la sube a
`ghcr.io/emilio-devesa/neverred` (tags `X.Y.Z`, `X.Y` y `latest`) y crea la
Release con notas y comandos de despliegue:

```bash
git tag -a v1.1.0 -m "NeverRed v1.1.0" && git push origin v1.1.0
```

Antes del tag: deja el árbol limpio (`git status`) y actualiza este README si
hay cambios visibles. Para regenerar el usuario de demostración:
```bash
python3 backend/seed_demo.py   # demo@neverred.local / DemoNeverRed2026
```
