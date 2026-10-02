# NeverRed 🟢

Contabilidad personal con el sistema de **doble partida**. Simple para empezar, rigurosa para mantener.

> Cada euro tiene dos caras: de dónde viene y a dónde va. Si **debe = haber**, nunca estarás en rojo sin saberlo.

📖 **Documentación web**: [emilio-devesa.github.io/NeverRed](https://emilio-devesa.github.io/NeverRed/) (guía de uso, despliegue, API y desarrollo).

- [Descargar para macOS](#descargar-para-macos-sin-instalar-nada) · [Puesta en marcha](#puesta-en-marcha-con-usuarios-y-base-de-datos) · [Qué incluye](#qué-incluye) · [Capturas](#capturas) · [Reglas contables](#reglas-contables-aplicadas) · [Estructura](#estructura) · [API](#api) · [Despliegue](#despliegue) · [Releases](#releases-automáticas-con-github-actions)

## Descargar para macOS (sin instalar nada)

1. Descarga el `.dmg` desde **Releases** en GitHub (vale para Intel
   y Apple Silicon).
2. Ábrelo y arrastra **NeverRed** a **Aplicaciones**.
3. Abre el **Terminal** (está en Aplicaciones → Utilidades) y ejecuta
   esta orden una sola vez:

   ```bash
   xattr -cr /Applications/NeverRed.app
   ```

   (macOS marca las descargas de internet; sin este paso dice que la
   app "está dañada". No vuelve a pedirlo nunca más).
4. Doble clic en NeverRed: se abre en tu navegador. Listo.

No pide instalar Python ni nada más (usa lo que ya trae macOS). Tus datos
quedan en tu Mac (`~/Library/Application Support/NeverRed`).
Las siguientes actualizaciones llegan solas desde la propia app
y no necesitan repetir este paso.

## Descargar para Linux (sin instalar nada)

1. Descarga el `.AppImage` desde **Releases** en GitHub.
2. Dale permiso de ejecución (clic derecho → Propiedades → Permisos, o
   `chmod +x NeverRed-*.AppImage`) y haz doble clic.

Tus datos quedan en `~/.local/share/NeverRed`. Para detenerlo del todo:
`./NeverRed-*.AppImage --stop`. Al abrir comprueba actualizaciones
(diálogo con zenity si lo hay; si no, avisa por consola y sigue).

La app comprueba actualizaciones al abrirse: si hay versión nueva muestra
un diálogo con los cambios y opciones de **Instalar** (descarga, sustituye
y reabre sola), **Omitir versión** o **Más tarde**.

NeverRed vive en el Dock mientras está abierta: al salir de ella
(cmd+Q o clic derecho → Salir) se detiene el servidor y no queda
actividad en segundo plano. Volver a abrirla reutiliza el servidor si
ya estaba en marcha. Y si cierras su última pestaña en el navegador,
todo se apaga solo en unos 20 segundos (las demás pestañas no se
ven afectadas).

> La app no está firmada con certificado Apple: la primera vez macOS la
> bloquea ("No se ha abierto NeverRed.app"). Pulsa **Aceptar**, abre
> **Ajustes → Privacidad y seguridad**, baja hasta el aviso y pulsa
> **Abrir igualmente**, y confirma una vez más. Solo pasa la primera vez
> ([paso a paso con capturas](https://emilio-devesa.github.io/NeverRed/guia#instalar-en-macos-primera-vez)).

## Puesta en marcha (con usuarios y base de datos)

La app requiere registrarse con correo y contraseña antes de usarla. Los usuarios y sus datos contables se guardan en una base de datos SQLite a través del servidor incluido (solo biblioteca estándar de Python, sin dependencias):

```bash
python3 backend/server.py
```

Abre entonces **http://127.0.0.1:8000** y entra: lo primero es el inicio
de sesión (con casilla **Recuérdame** y lista de usuarios de ese
navegador: avatar, nombre y correo enmascarado; un clic y solo pide la
contraseña, al estilo macOS).
Opciones del servidor: `python3 backend/server.py --port 8001 --host 0.0.0.0`.

Para verla con datos de ejemplo, entra como **Demo** (último en la lista
de acceso) —o por terminal—:

```bash
python3 backend/seed_demo.py   # demo@neverred.local / DemoNeverRed2026
```

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
- **Autoapagado** (modo `.app`): `NEVERRED_QUIT_WHEN_IDLE=1` apaga el
  servidor al cerrar la última pestaña (`NEVERRED_IDLE_TIMEOUT`, 20 s).
- **Auditoría**: `audit_log` registra accesos, cambios de clave, resets y borrados.
- **Rate-limit**: máx. 10 intentos de login/registro por IP cada 10 min (`NEVERRED_RATE_MAX/WINDOW`).
  Tras un proxy (Caddy, Docker) todos los clientes se ven como una sola IP:
  activa `NEVERRED_TRUST_PROXY=1` **solo** si el proxy es de tu confianza
  (Caddy ya envía `X-Forwarded-For`) para que el límite distinga clientes.
- **CORS restringido** al mismo origen y al modo archivo local.
- **HTTPS**: con `NEVERRED_TLS_CERT` + `NEVERRED_TLS_KEY` el servidor habla TLS. En producción, sírvelo siempre por HTTPS (directo o tras Caddy; ver `Caddyfile`).
- Cada guardado en la app se sincroniza con la BD (con copia local por usuario como caché).
- **Cada usuario tiene su propia contabilidad aislada**: al registrar una cuenta nueva se parte del plan base vacío; al cerrar sesión se borran el estado en memoria y su copia local, y nadie hereda los datos de otro usuario. Los tickers de Mercado y la clave de Alpha Vantage también son por usuario.
- **Tu cuenta es tuya**: puedes cambiar la contraseña (cierra las demás sesiones) o eliminar tu cuenta y todos tus datos desde el pie de la app.
- **Demo**: siempre la última en la lista de acceso; entrar crea o restablece
  sus 6 meses de datos sin pedir contraseña ni tocar a otros usuarios.

> Sin el servidor en marcha (p. ej. abriendo `index.html` directamente), la pantalla de acceso avisará de que no hay conexión.

## Qué incluye

- **Asistente de inicio**: crea tu asiento de apertura (efectivo + banco − deudas = capital inicial).
- **Libro diario**: asientos con N líneas, validación `Debe = Haber`, edición, borrado, filtros y búsqueda (paginado de 50 en 50).
- **Libro mayor**: movimientos y saldo acumulado por cuenta, con naturaleza deudora/acreedora.
- **Plan de cuentas**: 5 familias (Activo, Pasivo, Patrimonio, Ingreso, Gasto), crear/editar/archivar (archivar oculta la cuenta pero su saldo sigue contando).
- **Informes**: balance general y PyG lado a lado, evolución diaria de activos
  y pasivos (con gráficas plegables), balance de comprobación (con CSV).
- **Herramientas**: Mercado (gráficas de tickers con tu clave gratuita de
  Alpha Vantage y cuota visible),
  simulador de préstamos y simulador de rendimientos.
- **Panel**: patrimonio neto, ecuación fundamental en vivo, gráfico ingresos vs gastos 6 meses, accesos rápidos (sueldo, gasto, transferencia).
- **Presupuestos**: límite mensual por gasto con avisos al 80 % y al superar.
- **Recurrentes**: plantillas mensuales (nómina, alquiler) con generación sin duplicados.
- **Importar CSV** del banco (`fecha;descripción;importe`) como asientos cuadrados.
- **PWA**: instalable y carcasa offline (la API necesita red).
- **Tus datos**: viven en la base de datos del servidor y se sincronizan con
  una caché local por usuario. Exporta/importa JSON para copia o traslado
  (la app te avisa si llevas más de 30 días sin exportar).

## Capturas

![Acceso](docs/NeverRed-Login.jpeg)
![Inicio](docs/NeverRed-Inicio.jpeg)
![Libro diario](docs/NeverRed-Diario.jpeg)
![Informes](docs/NeverRed-Informes.jpeg)
![Herramientas](docs/NeverRed-Herramientas.jpeg)

## Reglas contables aplicadas

- Todo asiento exige ≥ 2 líneas, importes > 0 y `Σ Debe = Σ Haber`.
- Ninguna línea mezcla debe y haber.
- Activo y Gasto son de naturaleza **deudora** (suben por el debe); Pasivo, Patrimonio e Ingreso, **acreedora** (suben por el haber).
- Ecuación verificada en vivo: `Activo = Pasivo + Patrimonio + (Ingresos − Gastos)`.

## Estructura

```
index.html                — vistas + modales + acceso + registro del SW
styles.css                — tema oscuro/claro, responsive
app.js                    — estado, render y sincronización (sin librerías)
lib/contabilidad.js       — lógica contable pura (navegador y tests node)
icon.svg                  — icono pixelado estilo panel de bolsa
manifest.webmanifest, sw.js — PWA instalable con carcasa offline
backend/server.py         — API + SQLite + estáticos (solo stdlib)
backend/backup.py         — copias de la BD con retención
backend/seed_demo.py      — usuario demo con 6 meses de movimientos
backend/tests/            — suites unittest (server, market, telemetry…)
backend/neverred.db       — base de datos (se crea al arrancar; no se versiona)
frontend/tests/           — tests node de la lógica pura (contabilidad, mercado, simuladores)
packaging/macos/          — Launcher.sh + build.sh (.app + .dmg sin dependencias)
Dockerfile, compose.yaml, Caddyfile — despliegue
.github/workflows/       — ci (push/PR: backend + market + node), release + packaging-macos/-linux (tags v*)
docs/                     — capturas para este README
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
| GET | `/api/me` | Usuario de la sesión actual + `last_export` (última copia) |
| POST | `/api/export-log` | Marca una exportación manual (para el aviso de copia) |
| GET | `/api/sessions` | Sesiones propias (sin exponer tokens) |
| POST | `/api/sessions/rotate` | Cierra todas las sesiones salvo la actual |
| GET | `/api/data` | Datos contables del usuario |
| PUT | `/api/data` | Guarda `{accounts, entries, seq, currency, budgets, recurring}` (validado) |
| GET | `/api/market/status` | ¿Hay clave propia? + nº de tickers + cuota diaria restante |
| POST | `/api/market/key` | `{key}` guarda tu clave personal de Alpha Vantage (solo servidor) |
| DELETE | `/api/market/key` | Olvida tu clave de Alpha Vantage |
| GET | `/api/market/tickers` | Tus tickers con último cierre y tendencia (de caché, sin gastar cuota) |
| POST | `/api/market/tickers` | `{symbol}` añade un ticker (valida y descarga su serie) |
| DELETE | `/api/market/tickers?symbol=X` | Quita un ticker (la serie en caché se conserva) |
| GET | `/api/market/history?symbol=X` | Serie ajustada (~100 sesiones) + mín/máx/variación + dividendos/splits (`&refresh=1` fuerza descarga) |
| POST | `/api/ping` | `{tab}` latido de pestaña para el autoapagado (sin auth) |
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
- Backup externo del contenedor (cron del host):
  ```bash
  0 3 * * * docker exec neverred python3 backend/backup.py --db /data/neverred.db --dest /data/backups
  ```

### Opción B — Servidor/VPS clásico

```bash
python3 backend/server.py --host 0.0.0.0 --port 8000
```

Para producción, sirve siempre por **HTTPS**: tras un proxy con TLS automático
(Caddy, con ejemplo listo en `Caddyfile`) o TLS directo con
`NEVERRED_TLS_CERT`/`NEVERRED_TLS_KEY`, y supervísalo
con systemd o similar. Tras Caddy, exporta `NEVERRED_TRUST_PROXY=1` para que
el rate-limit vea la IP real de cada cliente. Copias de seguridad:

```bash
python3 backend/backup.py              # backend/backups/, conserva las 14 últimas
```

o por cron cada noche (ver cabecera del script). La app también permite
Exportar JSON manual. Tests:

```bash
python3 backend/tests/test_server.py   # backend: 20 tests (solo stdlib)
python3 backend/tests/test_market.py    # mercado: 17 tests (sin red, con mock)
python3 backend/tests/test_telemetry_forward.py   # forward: 7 tests
python3 backend/tests/test_telemetry_pairing.py   # emparejamiento: 11 tests
node --test frontend/tests/            # frontal: contabilidad, mercado y simuladores (sin dependencias)
```

## Releases (automáticas con GitHub Actions)

Publicar un tag `v*` dispara los workflows, que verifican todo (suite Python +
tests node), construyen la imagen Docker (→ `ghcr.io/emilio-devesa/neverred`
con tags `X.Y.Z`, `X.Y` y `latest`), generan los `.dmg` de macOS (Intel y ARM)
y crean la Release con notas y comandos de despliegue:

```bash
git tag -a v1.3.0 -m "NeverRed v1.3.0" && git push origin v1.3.0
```

Antes del tag: deja el árbol limpio (`git status`) y actualiza este README si
hay cambios visibles. Al subir versión, actualiza **los dos sitios**:
`VERSION` en `backend/server.py` y `NEVERRED_BUILD` en `app.js` (si no
coinciden, el frontal se autorrefresca en bucle).

### Telemetría de uso (beta, opcional)

La app puede ayudar a mejorar registrando **qué funciones se usan**. Está
**apagada por defecto**: en el primer arranque se pregunta una sola vez
(Sí / Ahora no).

**Qué se recopila:** solo conteos anónimos —qué vistas se abren y cuántas
veces se usa cada acción (crear un asiento, importar un CSV…). Nunca
importes, textos, nombres ni ningún dato personal o contable.

**Cómo cambiarlo:** en cualquier momento, pie de la app → **Telemetría**,
donde puedes activar o desactivar el envío. Al desactivarlo, se borra el
historial acumulado; también se borra al eliminar la cuenta. Los datos se
guardan en tu propio servidor con purga automática a los 90 días.
