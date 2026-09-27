# NeverRed — Historial de cambios

## v1.7.5 — Carcasa siempre fresca
- El SW ya no cachea errores y fuerza actualización en cada arranque
- Guardián de versión frontal/servidor con autocorrección

## v1.7.4 — Caché bajo control
- Al salir se anula el service worker y se borran cachés
- Recarga automática ante versión nueva del frontal

## v1.7.3 — Seis bugfixes
- Icono propio en el AppImage de Linux (PNG vía rsvg)
- Auto-update en Linux también al reabrir; Python con Tk garantizado
- Diálogo macOS siempre con changelog desplazable
- Versión visible en el pie; avatares incluidos en los paquetes
- Login sin recorte superior en pantallas bajas

## v1.7.2 — Pulido del acceso
- Avatares en imagen por letra, correos enmascarados y scroll propio del acceso
- El servidor decodifica rutas con caracteres especiales (p. ej. ñ)

## v1.7.1 — Acceso y actualizaciones
- Login estilo macOS: usuarios conocidos con avatar, solo-contraseña y Recuérdame
- Demo siempre última en la lista, con entrada directa e idempotente
- Diálogo de actualización unificado (macOS y Linux) con changelog desplazable
- AppImage de Linux sin dependencias ni instalación

## v1.7.0 — Acceso renovado
- Login primero estilo macOS: usuarios conocidos con avatar y solo-contraseña
- Casilla Recuérdame (sesión corta o 30 días) y botón Probar sin registrarse
- Salir de la app detiene el servidor; autoapagado al cerrar la última pestaña

## v1.6.0 — Actualización automática
- La app macOS comprueba versiones al abrir: diálogo nativo con changelog
  e Instalar (descarga, sustituye y reabre) / Omitir / Más tarde
- Demo documentada: `backend/seed_demo.py` → demo@neverred.local

## v1.5.0 — Robustez y producto
- SQLite en WAL + busy_timeout, índices y tope de 10 sesiones
- Sincronización con ETag: aviso y resolución de conflictos, insignia de estado
- CSV sin duplicados; presupuestos con semáforo en Inicio
- Tablas con scroll en móvil, foco en modales, gráfico con tabla de datos
- Demo dinámica (últimos 6 meses + presupuestos + recurrente), DMG universal

## v1.4.0 — Cierre limpio
- Salir de la app detiene el servidor; autoapagado al cerrar la última pestaña

## v1.3.0 — macOS de doble clic
- `.app` + `.dmg` sin dependencias, datos en Application Support

## v1.2.0 — Seguridad y cuenta
- Rate-limit, cambio y recuperación de contraseña, CORS restringido, TLS,
  sesiones HttpOnly con rotación, auditoría, borrado de cuenta

## v1.1.0 — Usuarios y base de datos
- Registro/login con SQLite, contabilidad aislada por usuario

## v1.0.0 — Primera versión
- Doble partida, diario, mayor, cuentas, informes y panel
