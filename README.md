# NeverRed 🟢

Contabilidad personal con el sistema de **doble partida**. Simple para empezar, rigurosa para mantener.

> Cada euro tiene dos caras: de dónde viene y a dónde va. Si **debe = haber**, nunca estarás en rojo sin saberlo.

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
- Las sesiones usan token aleatorio con caducidad de 30 días.
- Cada guardado en la app se sincroniza con la BD (con copia local por usuario como caché).
- **Cada usuario tiene su propia contabilidad aislada**: al registrar una cuenta nueva se parte del plan base vacío; al cerrar sesión se limpia el estado en memoria y nadie hereda los datos de otro usuario.

> Sin el servidor en marcha (p. ej. abriendo `index.html` directamente), la pantalla de acceso avisará de que no hay conexión.

## Qué incluye

- **Asistente de inicio**: crea tu asiento de apertura (efectivo + banco − deudas = capital inicial).
- **Libro diario**: asientos con N líneas, validación `Debe = Haber`, edición, borrado, filtros y búsqueda.
- **Libro mayor**: movimientos y saldo acumulado por cuenta, con naturaleza deudora/acreedora.
- **Plan de cuentas**: 5 familias (Activo, Pasivo, Patrimonio, Ingreso, Gasto), crear/editar/archivar.
- **Informes**: balance de comprobación (con CSV), cuenta de resultados (PyG) y balance general.
- **Panel**: patrimonio neto, ecuación fundamental en vivo, gráfico ingresos vs gastos 6 meses, accesos rápidos (sueldo, gasto, transferencia).
- **Datos locales**: todo se guarda en `localStorage`. Exporta/importa JSON para copia de seguridad.

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
backend/server.py — API (registro/login/sesiones/datos) + SQLite + servidor estático
backend/neverred.db — base de datos (se crea al arrancar; no versionar con datos reales)
```

## API

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/register` | `{name, email, password}` → `201 {token, user}` (409 si el correo existe) |
| POST | `/api/login` | `{email, password}` → `200 {token, user}` (401 si falla) |
| POST | `/api/logout` | Invalida el token (cabecera `Authorization: Bearer …`) |
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

Para producción, ponlo detrás de un proxy inverso con HTTPS (Caddy, Nginx) y
supervísalo con systemd o similar. Copia de seguridad: basta con respaldar
`backend/neverred.db` (o usar Exportar JSON desde la app).

## Releases

1. Deja el árbol limpio (`git status`) y actualiza este README si hay cambios visibles.
2. Crea el tag de versión (semántico: `vMAYOR.menor.parche`):
   ```bash
   git tag -a v1.0.0 -m "NeverRed v1.0.0: primer release"
   git push origin main --tags
   ```
3. En GitHub → Releases → Draft a new release → elige el tag y pega las notas
   (qué incluye, credenciales demo si aplica, cómo desplegar con Docker).
4. Para regenerar el usuario de demostración en cualquier entorno:
   ```bash
   python3 backend/seed_demo.py   # demo@neverred.local / DemoNeverRed2026
   ```
