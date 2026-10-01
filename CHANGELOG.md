# NeverRed — Historial de cambios

## v2.6.0 — Herramientas: Mercado y simuladores
- Nueva pestaña **Herramientas** con tres utilidades
- **Mercado**: añade tickers (AAPL, GOOG, AIR.MC…) y ve su gráfica de
  los últimos meses en verde/rojo según vaya al alza o a la baja, con
  máximos, mínimos y variación del periodo; cada ticker enlaza a su ficha
  de Yahoo Finanzas. Necesita tu clave gratuita de Alpha Vantage (cada
  usuario guarda la suya; la app te dice dónde conseguirla y cuánta cuota
  diaria te queda)
- **Simulador de préstamos**: cuota mensual, total e intereses de un
  préstamo francés, con gráfica anual y tabla año por año
- **Simulador de rendimientos**: interés compuesto con aportaciones
  mensuales, con gráfica anual y tabla año por año
- Informes reordenados: Balance y PyG arriba lado a lado; evoluciones
  y comprobación plegables para una página más recogida

## v2.5.0 — Evolución de los pasivos y gráficas compactas
- Nueva tarjeta en Informes: saldo diario de cada pasivo en 180 días más
  el total de pasivos, con valor al pasar el puntero y tabla de saldos
  a fin de mes (igual que la de activos)
- Gráficas de evolución a mitad de alto para que ambas tarjetas quepan
  en pantalla sin desplazar tanto

## v2.4.1 — Evolución de los activos
- Nueva tarjeta en Informes: saldo diario de cada activo en 180 días más
  patrimonio total (activos − pasivos), con valor al pasar el puntero
  y tabla de saldos a fin de mes

## v2.4.0 — Actualizador que respeta tu instalación
- Actualiza la copia que estés usando (antes forzaba /Applications)
  y solo da por buena la instalación si trae la versión esperada
- Diario del actualizador más claro ("ya instalada", nº de notas, diálogo usado)
- Notas sin retornos Windows que podían vaciar el diálogo
- La cuenta de resultados vuelve a ser una sola tarjeta con enlace
  **Vista mensual / Volver a la vista acumulada** (igual que el Mayor)
- Fuera restos del lanzador nativo (ObjC/Swift): el ejecutable es el
  script clásico y el primer arranque es el de siempre
- El Mayor tiene **Vista mensual / Vista global**: enlace sobre la tabla
  con la misma navegación por meses (anterior/siguiente, volver al actual)
  y fila de **saldo inicial** con el arrastre anterior
- El ejecutable de macOS vuelve a ser el script clásico (flujo Gatekeeper
  con Abrir igualmente); el lanzador nativo queda guardado en el repo
- La actualización muestra qué trae la versión (antes enseñaba las
  instrucciones de descarga); las releases traen "Novedades" del historial
- El diálogo dice qué versión tienes y cuál hay, y tranquiliza: los datos
  se conservan y la descarga se verifica con firma
- "Ya instalada" en el diario cuando no hay nada que hacer (antes "omitida")
- El lanzador es Objective-C universal (Intel + Apple Silicon) y el bundle
  lleva firma ad-hoc: Gatekeeper vuelve al flujo normal (Abrir igualmente)
  en vez de decir "está dañado" sin apelación (2.3.4–2.3.5)
- El actualizador escribe `update.log`: cada paso deja rastro con su motivo
- Nueva tarjeta "PyG del mes" encima de la acumulada, con la misma
  navegación del Diario (anterior/siguiente, mes centrado, volver al actual)
- Balance de comprobación y Balance general siguen acumulados (son stock)
- Nuevo lanzador nativo (Swift, sin dependencias): icono con punto de
  activa, sin rebote eterno; Salir desde el Dock detiene el servidor y
  cerrar todas las pestañas cierra la app sola
- El actualizador escribe `update.log` en la carpeta de datos (fin de
  los fallos silenciosos: cada paso deja rastro con su motivo)
- Diario: el mes/rango queda centrado también al ocultar los laterales
- Elegir Desde/Hasta activa un modo propio sobre todo el histórico
  (aunque cruce meses): oculta Mes anterior/Siguiente y el título
  muestra el rango (p. ej. 01/09/2026 - 10/10/2026)
- "Volver al mes en curso" desactiva el filtrado y devuelve el mes actual
- La hoja de estilos lleva la versión en la URL: cada release invalida
  la caché del Service Worker y los cambios visuales aparecen a la primera
- Corregía el caso real de la 2.3.1 (HTML nuevo + CSS viejo: botones
  apilados, grises y enlace sin estilo)
- Anterior a la izquierda, mes centrado, Siguiente a la derecha (rejilla real)
- Botones deshabilitados en gris para que se entienda el límite
- Enlace "Volver al mes en curso" bajo el mes cuando no es el actual
- Los asientos se ven mes a mes, con "Mes anterior / Mes siguiente" encima
  de la lista (anterior se deshabilita sin historial; siguiente, en el mes actual)
- Desde/Hasta quedan como refinamiento por día dentro del mes visible
- Crear, importar o generar salta al mes del asiento; la búsqueda convive igual
- El actualizador de macOS re-registra la app en Launch Services tras
  instalarla: el Finder ya no se queda mostrando la versión vieja
  (el contenido sí se actualizaba; eran los metadatos cacheados)
- Sin servidor, la app de escritorio ahora dice "vuelve a abrir NeverRed"
  en vez de mandar ejecutar `python3 backend/server.py` (era texto de desarrollo)
- El lanzador (macOS y Linux) elimina el `server.pid` rancio tras el
  autoapagado por inactividad, sin arrastrar estado muerto
- Causa del reporte: al cerrar la última pestaña todo se apaga solo en
  ~20 s (diseño); volver por el navegador sin reabrir la app dejaba la
  cáscara en caché sin servidor detrás
- La demo fallaba los días 1–5 de cada mes (`randint` con rango vacío):
  los días aleatorios ahora parten del día 1 (afectaba a `/api/demo` y
  a `seed_demo.py`)
- La app genera un par Ed25519 en el primer arranque y se enrola sola
- Ingesta firmada; el coordinador aprueba/veta desde el dashboard
- Sin aprobar no se almacena nada (la app retiene todo sin pérdidas)
- Releases con `telemetry.conf` por defecto hacia el receptor
- Guía de primera apertura en macOS (Gatekeeper) con capturas
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
