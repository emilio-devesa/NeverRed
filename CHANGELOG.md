# NeverRed — Historial de cambios

## Sin release (en `main`, sin etiquetar) — 2026-10-28

- **Monitor de telemetría: aprobación desde Safari.** El botón «Aprobar» del
  panel devolvía «Solicitud cruzada rechazada.» en Safari (modo privado),
  porque ese navegador manda `Origin: null` o un `Origin` sin puerto al
  enviar el formulario; la defensa anti-CSRF lo trataba como origen
  cruzado. `_misma_origen()` ahora tolera ambos casos cuando el `Host` es
  loopback y el token CSRF es válido (la barrera principal sigue siendo el
  token; el DNS-rebinding queda cubierto por la defensa de `Host`). La
  protección contra orígenes cruzados reales y el rebinding siguen activas
  (tests `test_05`/`test_06` en verde). No cambia el protocolo ni los datos;
  basta re-copiar `telemetry_sink.py` en
  `~/Library/Application Support/NeverRed-monitor/` y hacer
  `launchctl bootout`/`bootstrap`.

## v2.7.0 — consolidación tras la auditoría 2.6.9
Publicado el 2026-10-26. Esta versión recoge en un solo carril los tres
lotes (P0/P1/P2/P3) de la auditoría de la 2.6.9: seguridad y carreras en
el registro, higiene con impacto en el mercado y el borrado, y acabados
de usabilidad (modales propios, cabecera `Server:` discreta, tope del
registro interno). Sin migración de datos: las bases 2.6.9 siguen
funcionando tal cual (con una limpieza de índice automática al arrancar).

- **Registro robusto ante carreras (500 → 409):** el `IntegrityError` del
  índice UNIQUE del email ya no escapa como «Error interno.» cuando dos
  peticiones se cruzan (check-then-insert). Mismo 409 y mismo mensaje que
  el check secuencial. Blindaje idéntico aplicado al usuario demo
  (`/api/demo`) y al enrolamiento del monitor (`_enroll`), que es público
  vía funnel. `market_add` no lo necesitaba (ya usaba
  `ON CONFLICT(user_id, symbol) DO NOTHING`).
- **Panel del monitor blindado (anti-CSRF + anti-DNS-rebinding):** el
  dashboard de `telemetry_sink.py` (útil solo-lógicamente local) ahora
  exige, en sus tres rutas de control (panel, `/api/sink-summary` y
  `/api/sink-identity`):
  1. `Host` de loopback (mata el rebinding);
  2. `Origin`/`Referer` propios si el navegador los manda (rechaza
     formularios cruzados; el CLI sin navegador sigue admitido);
  3. token anti-CSRF (TTL 1 h, no de un solo uso: el panel comparte el
     token entre los formularios de la misma página), imprimido en el
     HTML como campo oculto y devuelto por `/api/sink-summary`.
  El aprobado por CLI pasa a dos pasos (comando listo en
  `docs/ops/telemetry.md`).
- **Cabeceras de privacidad en toda respuesta:** `Referrer-Policy:
  no-referrer` y `Permissions-Policy`
  `camera=(), microphone=(), geolocation=()`, inyectadas en un punto
  único (`end_headers()`) que cubre API, estáticos, OPTIONS y passthrough
  de relay, tanto en `server.py` como en `telemetry_sink.py`.
- **Tests:** backend 73 → 82 (carrera con 8 hilos; cabeceras en
  HTML/JSON/OPTIONS; 9 pruebas nuevas de defensas del panel). Frontal 42
  y selftest Windows 27 intactos.

### Lote P2 — higiene con impacto (2026-10-25)

- **Mercado honesto ante fallos de red:** un timeout o caída de Alpha
  Vantage ya no consume cuota del día, y ya no responde **404 «No se encontró el
  ticker»** (mentiroso): responde 429 «Alpha Vantage no responde…».
  `datetime.utcnow()` (deprecado en 3.12+) sustituido por
  `datetime.now(timezone.utc)`.
- **Borrar la cuenta deja cero estela en el registro interno:** `DELETE
  /api/account` anonimiza las filas de `audit_log` del usuario (el email
  del «register» incluido) conservando solo el contador de hechos.
- **Preflights CORS cacheados:** `Access-Control-Max-Age: 86400` — el
  navegador deja de repetir miles de preflights al día.
- **ETag más correcto:** la comparación de validadores débiles usa el
  prefijo real `W/` (el `lstrip` anterior casara cadenas imposibles como
  `WoW/…`).
- **Índice redundante eliminado:** `idx_sessions_token` fuera del esquema
  (token ya es PRIMARY KEY de `sessions`); migración automática
  `DROP INDEX IF EXISTS` para bases anteriores.
- **Código muerto fuera del relay:** el bloque roto-inerte
  (`do_POST.__wrapped__ if False else None` + `for _m… pass`) de
  `telemetry_relay.py` desaparece; queda el `setattr` real.
- **Empaquetado más seguro:** los tres build (`macos/build.sh`,
  `linux/build.sh`, `windows/build.ps1`) **fallan con error** si no
  deducen versión de git (antes caían a defaults antiguos 1.2.0/1.7.0/2.6.8
  que habrían etiquetado paquetes viejos).
- **Actualizadores más robustos:** Linux adopta el protocolo
  `update_pending`/`update_ok` de Windows — la «omitir versión» solo se
  marca si la AppImage nueva está viva (un fallo de arranque ya no
  descarta la versión para siempre). macOS limpia los restos
  `.nuevo.$$`/`.viejo.$$` de procesos muertos a mitad de swap. Los
  helpers `app_root`/`data_dir`/`ulog` del actualizador Windows se
  deduplican (fuente única: `launcher.py`, con respaldo local).
- **Docs de operación:** límite de confianza del `X-Forwarded-For` del
  relay y decisión Sybil del enroll público (riesgo aceptado, documentada).
- **Tests:** backend 86 (36 server + 20 market + 9 forward + 19 pairing +
  2 integration), frontal 42, selftest Windows 27 — todos en verde.

### Lote P3 — acabados y aceptaciones (2026-10-25)

- **Adiós a los `alert()`/`confirm()` nativos:** los 17 avisos y
  confirmaciones del frontal (eliminar asiento, borrar todo, eliminar
  cuenta, importar, trabajar sin conexión…) pasan a los modales propios de
  la app (`showAlert`/`showConfirm`, promesas sobre la infraestructura
  `.modal-backdrop`): mismo estilo que el resto de la interfaz, no cortan
  el hilo de render, heredan la trampa de foco (Tab) y se cierran con Esc
  (un confirm con Esc = «no»). Los destructivos van con botón rojo
  (`.btn.danger`) y los de información con el verde primario.
- **Cabecera `Server:` discreta:** `Server: NeverRed/2.6.9` — se deja de
  anunciar la versión de Python del sistema (y el espacio colgante del
  encabezado por defecto); mismo criterio ya aplicado en el relay.
- **IDs estándar en el frontal:** `uid()` usa `crypto.randomUUID()` cuando
  el contexto es seguro (https/localhost), con respaldo al generador
  anterior si la API no está (HTTP en LAN). Nunca fue un ID de seguridad;
  ahora es además el estándar.
- **Un solo `stop_server`:** el lanzador AppImage (AppRun) deja de llevar
  su copia inline de la función y carga la de `update-common.sh`, que ya
  viaja dentro del paquete (fuente única junto a los check-update de
  macOS/Linux; verificado con arnés: PID real de `server.py` detenido,
  pidfiles rancios y basura cubiertos).
- **`audit_log` con tope por tamaño:** además de la purga por antigüedad
  (90 días), la tabla no supera 5 000 filas (configurable con
  `NEVERRED_AUDIT_MAX`) — los `login_fail` no pueden engordar la BD sin
  límite entre purgas. Test nuevo.
- **Acuerdos documentados (sin código):** el triple escaneo del dashboard
  y la duplicación conceptual `seed_demo`/`demo_data` se aceptan tal cual
  hasta que el volumen de datos lo pida; la auditoría además dedujo mal el
  hallazgo F-7 (Esc y trampa de foco ya estaban implementados desde el
  27-29/09 — verificado con `git blame`).
- **Backlog del servidor más ancho (hallazgo nuevo durante el lote):** el
  `listen` usaba el backlog por defecto de TCPServer (5); una ráfaga de 8
  conexiones simultáneas dejaba fuera 2 con «Connection refused» sin rastro
  en el servidor (reproducido y capturado por el test de carrera del P1
  de forma intermitente). Ahora `request_queue_size = 128` y el test de
  carrera pasó 10/10 limpio.
- **Tests:** backend 86 → 88 (38 server: +tope de `audit_log`, +cabecera
  `Server`), frontal 42, selftest Windows 27 — todos en verde. La suite
  `test_server` usa ahora la clase de producción (`Server`) en vez de la
  base de stdlib.

## v2.6.9 — Debut en Windows y telemetría permanente
- **Instalador Windows (Inno Setup 6):** asistente en español que instala
  per-usuario y **sin permisos de administrador** en
  `%LOCALAPPDATA%\NeverRed` (el auto-update renombra su propio dir; en
  `Program Files` exigiría elevación constante), con tareas de accesos en
  menú inicio/escritorio y datos del usuario en `%APPDATA%\NeverRed` que
  **se conservan al desinstalar**. `AppMutex=NeverRedAppMutex` +
  `CloseApplications` cierra la app en el setup; el desinstalador
  silencioso con la app en marcha aborta a nivel de engine (limitación
  de Inno, documentada; con la app cerrada funciona siempre).
- **Launcher Windows:** mutex global (`NeverRedAppMutex`, ctypes) y el
  bloque de stdlib que el hijo `--serve` necesita cuando el exe está
  congelado (PyInstaller solo embebe los imports estáticos del
  entry-point: `ModuleNotFoundError: 'json'` sin él). Telemetría del
  launcher con paridad mac/Linux: entorno > `telemetry.conf` junto a los
  datos > destino por defecto del build.
- **Defecto de telemetría por plataforma:** `build.ps1` entra con el
  mismo receptor por defecto que `build.sh` (mac/Linux); un build sin
  destino se pide explícito (`-SinkUrl ''`). Antes el exe quedaba en modo
  local puro de serie («Sin receptor configurado»).
- **Relay permanente:** `backend/telemetry_relay.py`, punto de entrada
  del funnel público. Solo retransmite `ingest`/`enroll` al monitor
  (Authorization intacta; el monitor revalida) con rate-limit por IP
  (`X-Forwarded-For`, 60/600 s, fail-closed) y cuerpo ≤ 1 MB. El
  dashboard y `/api/sink-identity` (aprobar/vetar) nunca salen por el
  funnel: el proxy entra como `127.0.0.1` y «Solo local.»
  vendría de menos. Despliegue documentado como LaunchAgents con
  KeepAlive y funnel de Tailscale; end-to-end verificado desde Windows
  (`ingest` → 401 del monitor, `enroll` → `pending`).
- **Empaquetado Windows:** PyInstaller 6 + `build.ps1` con 3 bugs del
  build real arreglados (directorio `backend` creado antes de copiar;
  redirección con `Start-Process -Wait` para exes GUI; `New-Item` previo
  en el portable). `selftest.py` del paquete: 27 verificaciones (+4 del
  instalador).
- **CI:** `packaging-windows.yml` instala Inno Setup en el runner,
  ejecuta el selftest y adjunta a la Release los dos zips y el
  `NeverRed-setup-<v>.exe`. Más workflows de firma y SmartScreen
  documentados (`docs/ops/releases.md`): sin Authenticode a propósito,
  paridad con dmg/AppImage; la integridad la da la `.sig` Ed25519.
- **Documentación:** README con sección «Descargar para Windows», web
  (GitHub Pages) con instalación en Windows y cierre/actualizaciones
  generalizadas, y despliegue permanente de telemetría en
  `docs/ops/telemetry.md`.
- Service worker `v9`; versiones de la app en `config.py`, `app.js` y el
  cache-buster del `index.html` a `2.6.9`.

## v2.6.8 — Cuarta iteración de la auditoría (P3)
- **Backend modular:** `server.py` baja de 2.163 a ~1.400 líneas. Separados
  `config.py` (rutas/versión), `schema.py` (BD y migraciones), `auth.py`
  (contraseñas y validación), `static.py` (lista blanca/ETag/gzip),
  `market.py` (Alpha Vantage) y `telemetry.py` (store/forward/relay). Los
  empaquetados macOS/Linux copian los módulos nuevos.
- **Monitor de telemetría:** el dashboard escapa también comillas y atributos
  (además de `<`, `>` y `&`) en versiones, plataformas, IDs y leyendas SVG.
  Cierra el vector de XSS almacenado por una BD manipulada.
- **Limpieza:** fuera imports muertos, re-exports históricos de telemetría,
  el envoltorio `build_aggregates`, CSS `.demo-row`, una variable sin usar y
  la migración de `market.key` global.
- **Sesiones:** el error al cargar la lista usa el hueco reservado
  `#sessionsError`; solo cambia el estado de error, no el diseño normal.
- **Modo archivo:** `file://` usa siempre `http://127.0.0.1:8000`; se elimina
  la clave oculta `neverred_api` de `localStorage`.
- **Métricas:** con `NEVERRED_METRICS=1` (o log a fichero) cada petición deja
  una línea JSON con método, ruta, estado, latencia y `user_id`; sin cuerpos y
  sin los latidos `/api/ping`.
- **Forward automático:** el hilo periódico pasaba a `forward_once` sin el
  directorio de la clave de emparejamiento; el `TypeError` se tragaba el
  `except` y el envío no ocurría. Ahora lo recibe y hay test de regresión.
- **Tests:** nueva suite de integración HTTP (login → guardar → recargar →
  409 y persistencia tras logout/login); CI ejecuta 5 suites de backend.
- **Operación:** copias redactadas de telemetría y releases en `docs/ops/`.
- **Empaquetado:** `NEVERRED_BUILD_SINK=` (vacío) ahora deja la telemetría
  local; antes el `:=` de los scripts lo sustituía por el destino por defecto.
- **S-04 documentado:** `NEVERRED_TRUST_PROXY=1` sigue siendo una decisión de
  despliegue; no se cambia el comportamiento.
- Service worker `v8`; 115 tests en verde (32 backend + 19 mercado + 9 forward
  + 11 emparejamiento + 2 integración + 42 frontal).

## v2.6.7 — Tercera iteración de la auditoría (P2)
- **HTTP/1.1 de verdad:** keep-alive, `HEAD` con `Content-Length` y sin cuerpo,
  `OPTIONS` 204 sin cuerpo, gzip para estáticos y `ETag`/`Last-Modified` con
  `304`; los estáticos usan `no-cache` y la API `no-store`. Matriz de códigos
  (200/304/401/404/409/429/500) con framing correcto.
- **Errores controlados:** un fallo no controlado responde `500 JSON` con
  `Content-Length`; si ocurre después de enviar cabeceras, se cierra la conexión.
- **Timeouts:** 15 s por conexión (slowloris) y `daemon_threads`.
- **CSP estricta:** fuera `style-src 'unsafe-inline'`; los 7 estilos inline pasan
  a clases o CSSOM. **Sin cambios visuales:** 8 capturas antes/después idénticas.
- **Service worker:** caché `v7` acotada a 40 entradas, carcasa protegida, sin
  cachear `no-store` ni errores, CSS en red-primero y fallback con `ignoreSearch`.
- **Actualizadores:** lógica común en `packaging/update-common.sh` para macOS y
  Linux (firma, descarga, versiones, log). Corregido el camino Tk «Instalar» de
  Linux, que terminaba en «descárgala a mano».
- **MIME** ampliado (jpg/jpeg/webp/woff/woff2/txt/md).
- 110 tests en verde (31 backend + 19 mercado + 7 forward + 11 emparejamiento +
  42 frontal).

## v2.6.6 — Segunda iteración de la auditoría (P1)
- **Guardado más barato:** el ETag se calcula y serializa una sola vez por
  `PUT`, y la sesión se escribe como mucho cada 5 min (antes, 4 escrituras y
  un `COMMIT` por petición)
- **Integridad:** `PRAGMA foreign_keys=ON`; los `CASCADE` del esquema ahora se
  aplican de verdad
- **Fecha correcta:** `todayISO()` usa la fecha local; entre las 00:00 y las
  02:00 ya no propone el día anterior ni crea asientos en el mes equivocado
- **Rendimiento del frontal:** `Intl` memoizado, render solo de la vista activa
  y una sola descarga en vuelo por ticker de Mercado
- **Cambio de pestaña:** al entrar en otra pestaña la página empieza arriba,
  sin heredar el scroll de la anterior
- **Documentación veraz:** el pie dice que los datos viven en el servidor (con
  copia local), la serie de Mercado es «cierre diario sin ajustar», tests
  26/19/7/11, aviso TLS en Docker y consola invisible sin SMTP
- **Cadena de suministro:** `appimagetool` fijado a 1.9.1 y verificado con
  SHA-256; el binario descargado no se versiona
- 101 tests en verde

## v2.6.5 — Endurecimiento tras la auditoría (P0)
- **Privacidad:** el servidor sirve solo los archivos de la app (lista blanca).
  Informes privados, documentación, scripts de empaquetado y tests dejan de ser
  accesibles por HTTP y de viajar dentro de la imagen Docker
- **CORS:** solo se acepta el origen exacto del servidor (se acabó el
  `localhost:*`); el modo archivo (`file://`) sigue funcionando, sin cookies
- **Sin pérdida de datos:** si la carga inicial falla, la app reintenta 3 veces
  y ofrece trabajar sin conexión. No se sube nada hasta que el usuario decide
  explícitamente qué copia conservar, y esa decisión sobrevive a un recargado
- **Seguridad:** IDs escapados en el frontal y validados en el servidor; mapas
  de rate-limit y de pestañas con poda y tope; rate limit real en `/api/ping`
- **Actualizador macOS:** copia a un temporal, verifica la versión e instala
  con respaldo y rollback. Un fallo de copia ya no deja sin aplicación
- **CI:** se ejecutan las 4 suites de backend (18 tests de telemetría que antes
  no corrían) y los tests del frontal con un glob compatible con Node 22+
- **404 real** para recursos que no existen (antes devolvía la app con 200)
- Código muerto `_seed` eliminado; 99 tests en verde

## v2.6.4 — Tickers reordenables
- Los valores de Mercado se pueden **reordenar arrastrando** (o con los
  botones ↑/↓ en táctil): el orden es por usuario y se conserva al salir

## v2.6.3 — Mercado adaptado al plan gratuito actual
- Alpha Vantage ha movido la serie ajustada a premium: Mercado vuelve a
  la serie diaria gratuita (1 petición por ticker) sin cambios visibles
- Los errores de cuota ahora dicen la verdad: sin conexión, demasiadas
  peticiones seguidas o cuota diaria agotada, cada uno con su solución

## v2.6.2 — La sesión sobrevive al refresco
- Al recargar la página con sesión abierta ya no se vuelve al login:
  la app recupera la sesión de la cookie sin pedir credenciales

## v2.6.1 — Mercado ajustado, Diario paginado y aviso de copia
- Mercado con **precios ajustados** por splits y dividendos (adiós a los
  acantilados falsos) y **marcadores de dividendos y splits** en la
  gráfica y en el desplegable de cada ticker
- Las **cuentas archivadas** siguen contando en Balance, Evolución y
  Comprobación (archivadas se ocultan, el dinero no desaparece)
- **Diario paginado**: 50 asientos por tanda con botón "Mostrar más"
- **Aviso de copia**: la app te recuerda exportar si llevas más de
  30 días sin hacerlo
- La Demo vuelve a ser efímera: entrar restablece también sus tickers
- Importaciones y guardados grandes: tope ampliado de 2 a 10 MB

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
