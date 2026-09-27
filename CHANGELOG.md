# NeverRed — Historial de cambios

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
