# NeverRed — Historial de cambios

## v2.1.3 — Destino por defecto en build
- El destino de telemetría se inyecta al construir el paquete
  (prioridad: entorno > telemetry.conf > defecto del build)
- Los lanzadores usan `${VAR:-}` (el `set -u` tumbaba la app sin variables)
- Los instaladores incluyen los nuevos módulos de telemetría (la 2.1.0 no arrancaba)
- Nuevo `telemetry_common.py`: contrato compartido (catálogo + validadores)
- Nuevo `telemetry_forward.py`: forward y relay fuera de `server.py`
- El monitor se despliega con dos ficheros (ya no importa la app)
- Nuevo `telemetry.conf`: destino de telemetría externo, sin nada horneado
- El botón "Enviar ahora" informa el éxito real del envío
- README y web: telemetría y firmado solo a nivel usuario (detalles de coordinador fuera)
- Panel de telemetría simplificado a interruptor sí/no + "Enviar ahora" discreto
- Registro de uso con opt-in (catálogo cerrado, solo conteos, purga 90 días)
- Forward store-and-forward con backoff, relay público e idempotencia
- Dashboard del coordinador: totales, picos, tartas y medias de reintentos
- Diálogo de primer arranque; receptor y token beta por defecto en lanzadores

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
