# NeverRed — Informe del proyecto

**Aplicación de contabilidad personal por partida doble · Versión 2.0.0**
**Fecha del informe:** 29 de septiembre de 2026
**Repositorio:** github.com/emilio-devesa/NeverRed

> Cada euro tiene dos caras: de dónde viene y a dónde va. Si debe = haber, nunca estarás en rojo sin saberlo.

---

## Índice

1. [Descripción breve](#1-descripción-breve)
2. [Características](#2-características)
3. [Arquitectura, diseño y tecnologías](#3-arquitectura-diseño-y-tecnologías)
4. [Funcionamiento detallado](#4-funcionamiento-detallado)
5. [Análisis de ciberseguridad](#5-análisis-de-ciberseguridad)
6. [Análisis de experiencia de usuario (UX)](#6-análisis-de-experiencia-de-usuario-ux)
7. [Análisis de ingeniería de software](#7-análisis-de-ingeniería-de-software)
8. [Alternativas en el mercado](#8-alternativas-en-el-mercado)
9. [Análisis DAFO](#9-análisis-dafo)
10. [Guía de puesta en marcha](#10-guía-de-puesta-en-marcha)
11. [Conclusiones, extensibilidad y escalabilidad](#11-conclusiones-extensibilidad-y-escalabilidad-futuras)

---

## 1. Descripción breve

NeverRed es una aplicación de contabilidad personal basada en el sistema de **partida doble**, el mismo método que utilizan contables y empresas desde hace siglos: cada movimiento registra simultáneamente de dónde sale el dinero y a dónde va, de modo que el descuadre es matemáticamente imposible sin que la aplicación lo detecte y lo señale.

Funciona como aplicación de escritorio nativa en **macOS y Linux** (con instaladores de un clic y actualizaciones automáticas firmadas), y también como servicio web autohospedado mediante **Docker** o en un **VPS** propio. El usuario es el dueño absoluto de sus datos: todo se almacena en local, no hay nube obligatoria, no hay suscripción y no hay terceros con acceso a la información financiera.

Su propuesta de valor se resume en tres pilares:

- **Rigor contable real**, no un simple registro de gastos: plan de cuentas, libro diario, libro mayor, balance de comprobación, cuenta de pérdidas y ganancias y balance general.
- **Privacidad por construcción**: cero dependencias externas, telemetría opcional limitada a conteos agregados, y código auditable al completo.
- **Coste operativo nulo**: sin servidores propios que mantener, sin servicios de terceros, sin licencias; un solo fichero de base de datos SQLite por instalación.

---

## 2. Características

### Contabilidad

- **Libro diario**: registro cronológico de asientos con búsqueda por texto y filtro por rango de fechas; duplicado de asientos, plantillas mensuales y eliminación con confirmación.
- **Asientos por partida doble con validación estricta**: cada asiento exige descripción, un mínimo de dos líneas, importes exclusivamente en debe o en haber por línea, y cuadratura exacta (total debe = total haber > 0), verificada en vivo mientras se escribe y de nuevo en el servidor.
- **Libro mayor**: saldo corrido por cuenta con signo según su naturaleza contable (las cuentas de activo y gasto suman en debe; las de pasivo, patrimonio e ingreso, en haber), orden cronológico y filtro de texto.
- **Plan de cuentas editable**: 13 cuentas base inspiradas en el Plan General Contable español (caja, bancos, tarjeta, capital, sueldos, alquiler, comida, transporte, ocio, salud…), agrupadas en las cinco familias (Activo, Pasivo, Patrimonio, Ingreso, Gasto). Las cuentas sin movimientos se eliminan; las que tienen historial se archivan sin perder datos.
- **Informes**: balance de comprobación (exportable a CSV), cuenta de pérdidas y ganancias y balance general, todos calculados a partir de los mismos asientos validados.
- **Presupuestos**: límites de gasto por cuenta con alertas en el panel principal al alcanzar el 80 % (aviso) y al superar el 100 % (exceso), con barra de progreso por categoría.
- **Movimientos recurrentes**: plantillas mensuales (p. ej. el alquiler del día 5) que generan los asientos del mes de forma idempotente, sin duplicados aunque se ejecuten dos veces.
- **Importación de extractos bancarios (CSV)**: hasta 500 filas por fichero, con detección de formato de fecha e importe español, clasificación automática de cobros y pagos, deduplicación y vista previa antes de contabilizar.
- **Exportación e importación JSON**: copia completa de la contabilidad en un fichero portable, con restauración automática si la importación falla.

### Producto y plataforma

- **Panel de inicio**: saludo personalizado, cuatro indicadores (patrimonio, activo, pasivo, resultado del mes), gráfico de flujo de seis meses, últimos movimientos, estado de la ecuación contable fundamental y alertas de presupuesto.
- **Asistente de bienvenida**: crea el asiento de apertura (caja, banco, deudas iniciales, capital) guiando al usuario en su primer minuto.
- **Modo demostración**: datos de ejemplo de seis meses generados de forma determinista, accesibles con un clic y restablecibles en cualquier momento.
- **Multiusuario local y multisessión**: registro e inicio de sesión por correo, sesiones simultáneas visibles y revocables, recuerdo de sesión opcional y usuarios conocidos con avatar en la pantalla de acceso.
- **Recuperación de contraseña** por correo con enlace de un solo uso y caducidad de una hora.
- **Instaladores nativos de un clic** para macOS (.dmg universal Intel + Apple Silicon) y Linux (.AppImage), con **actualización automática firmada criptográficamente**.
- **Telemetría beta opcional**: medición de uso agregada y anónima (qué funciones se usan, nunca importes ni textos), con panel de transparencia propio y revocación que borra el historial.

---

## 3. Arquitectura, diseño y tecnologías

### Visión de conjunto

NeverRed es una aplicación **cliente-servidor local** de un solo proceso: el mismo programa sirve la interfaz web y la API de datos, y el lanzador nativo lo arranca, abre el navegador y lo detiene al salir. No existe infraestructura en la nube del proveedor: cada instalación es soberana.

```
┌─────────────────────────────────────────────────┐
│  Lanzador nativo (Launcher.sh / AppRun)         │
│  · comprueba actualizaciones firmadas           │
│  · arranca / detiene el servidor                │
│  · abre el navegador en 127.0.0.1:8000         │
└──────────────────────┬──────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────┐
│  backend/server.py — único proceso (stdlib)     │
│  · sirve index.html, app.js, estilos, PWA       │
│  · API JSON (/api/*)                            │
│  · SQLite por instalación (WAL)                 │
└──────┬──────────────────────────────┬───────────┘
       ▼                              ▼
 Interfaz (JS vainilla)        Telemetría (opt-in)
 PWA instalable + offline      store → forward → sink
```

### Pila tecnológica

| Capa | Tecnología | Notas |
|---|---|---|
| Backend | Python 3.12, **solo biblioteca estándar** | `http.server`, `sqlite3`, `hashlib`, `smtplib`; cero dependencias que auditar o actualizar |
| Persistencia | SQLite en modo WAL | Un fichero por instalación, permisos 600, copia con `sqlite3.backup()` |
| Frontend | HTML + CSS + JavaScript vainilla | Sin frameworks, sin paso de compilación, sin `node_modules` |
| Lógica contable compartida | `lib/contabilidad.js` (pura, sin E/S) | Reutilizada por la interfaz y por los tests de Node |
| Offline / PWA | Service Worker propio | Carcasa en red-primero con revalidación de versión; API siempre en red |
| Empaquetado macOS | `.dmg` universal, solo herramientas del sistema | Usa el `python3` del equipo; vale para Intel y Apple Silicon |
| Empaquetado Linux | `.AppImage` x86_64 | Ejecutable portable, sin instalación ni dependencias |
| Despliegue servidor | Docker (`python:3.12-slim`, usuario no root) + Caddy | Imagen publicada en GHCR en cada release |
| CI/CD | GitHub Actions | Tests en cada cambio; DMG + AppImage + Docker + Release en cada tag `v*` |
| Actualizaciones | Firma Ed25519 con `ssh-keygen` | Verificación obligatoria antes de instalar nada |
| Telemetría | Protocolo propio + SQLite | Agregados por (instalación, día, hora, evento); dashboard en el receptor |

### Decisiones de diseño destacables

- **Cero dependencias de terceros** en tiempo de ejecución: la superficie de ataque de la cadena de suministro es inexistente y el coste de mantenimiento de dependencias es cero.
- **Validación en dos niveles**: el cliente valida para una respuesta inmediata; el servidor revalida todo (tipos, formatos, cuadratura, existencia de cuentas) porque el cliente nunca es de fiar.
- **Concurrencia optimista con ETag**: si dos pestañas o dispositivos editan a la vez, el segundo recibe un conflicto 409 con ambas versiones en lugar de sobrescribir en silencio.
- **Borrado en cascada real**: eliminar la cuenta de usuario borra sesiones, datos, consentimiento y telemetría; revocar el consentimiento de telemetría borra su historial.

---

## 4. Funcionamiento detallado

### 4.1. Backend

El backend (`backend/server.py`, unos 1.400 líneas) es un servidor HTTP multihilo sin frameworks. Al arrancar, inicializa la base de datos con siete tablas: usuarios, sesiones, datos contables (un documento JSON validado por usuario), tokens de recuperación, registro de auditoría, eventos de telemetría y estado del envío de telemetría.

**Autenticación y sesiones.** Las contraseñas se protegen con PBKDF2-HMAC-SHA256 a 600.000 iteraciones con sal aleatoria, y la comparación se hace en tiempo constante con un señuelo para no revelar por tiempos si un correo existe. La sesión viaja en cookie `HttpOnly` con `SameSite=Lax` (inaccesible desde JavaScript), caduca a los 30 días —o 1 día si no se marcó "recuérdame"— y expira por 7 días de inactividad; cada usuario puede tener hasta 10 sesiones activas, visibles y revocables desde la interfaz. El cambio de contraseña cierra las demás sesiones; la recuperación usa un token del que solo se almacena su hash SHA-256, con una hora de vida y un único token vivo por usuario.

**API.** Una veintena de endpoints cubren registro, acceso, perfil, sesiones, datos contables con control de versiones, salud del servicio, demostración, borrado de cuenta y el subsistema de telemetría. Las respuestas incluyen cabeceras de seguridad (anti-MIME-sniffing, anti-clickjacking, sin caché, política de scripts restrictiva en las páginas) y un CORS limitado al mismo origen. Un limitador de intentos por dirección IP protege el registro, el acceso y la recuperación (10 intentos cada 10 minutos).

**Protección de ficheros.** El servidor de estáticos bloquea rutas con `..`, el directorio `backend/`, `.git`, ficheros ocultos y extensiones sensibles (`.db`, `.pem`, `.key`, `.env`, temporales de SQLite), de modo que la base de datos nunca es descargable por HTTP.

**Robustez operativa.** Acepta TLS nativo con certificado propio, respeta `X-Forwarded-For` solo tras proxy de confianza explícito, rota sus ficheros de log, registra una auditoría de eventos de seguridad con retención de 90 días y puede apagarse solo cuando no quedan pestañas abiertas (latido cada 10 segundos), comportamiento que usan los lanzadores de escritorio para no dejar procesos ocultos.

### 4.2. Frontend

La interfaz (`app.js`, unas 1.400 líneas sobre `index.html`) es una aplicación de página única con cinco vistas —Inicio, Diario, Mayor, Cuentas e Informes— más ocho diálogos modales accesibles (cierre con Escape, trampa de foco, barra de conflicto de sincronización).

El flujo típico es: pantalla de acceso (con usuarios conocidos y avatar), asistente de bienvenida o demo con datos, panel con indicadores y gráfico de seis meses, registro de asientos con totales en vivo, mayor por cuenta, e informes con exportación a CSV. Cada cambio se guarda en local al instante y se sincroniza con el servidor con un retardo de menos de un segundo, mostrando el estado (guardando, sincronizado, conflicto) en la barra superior.

La lógica contable —naturaleza de cuentas, saldos, cuadratura de asientos, ecuación fundamental— vive en un módulo puro compartido con los tests, de modo que lo que ve el usuario y lo que verifican las pruebas es exactamente el mismo código. El modo sin conexión funciona gracias al Service Worker: la carcasa sigue disponible y los cambios pendientes se sincronizan al recuperar la red.

### 4.3. Telemetría

El sistema de telemetría beta responde a una pregunta legítima de producto —¿qué funciones usan realmente los instaladores?— sin comprometer la privacidad. Su diseño es *privacy-by-construction*:

1. **Apagada por defecto.** En el primer arranque se pregunta una sola vez, en un diálogo no bloqueante, y la decisión queda registrada.
2. **Catálogo cerrado de 14 eventos.** Solo se pueden registrar acciones predefinidas (apertura, visitas a cada vista, asiento creado/eliminado, importaciones, exportaciones, conflictos). Es técnicamente imposible enviar importes, textos o datos personales: el servidor rechaza cualquier evento fuera del catálogo y cualquier propiedad que no sea un entero acotado.
3. **Almacén local (store).** Los eventos se acumulan en la base de datos del propio usuario, visibles en su panel de transparencia con el agregado de 30 días.
4. **Reenvío agregado (forward).** Cada 15 minutos, si hay receptor configurado, el servidor comprime los eventos en conteos por (instalación, día, hora, evento) y los envía con reintentos exponenciales. La instalación se identifica con un seudónimo aleatorio, sin vínculo con la persona.
5. **Receptor (sink).** Un microservicio hermano autentica cada lote con token, lo integra de forma idempotente y lo presenta en un dashboard con totales, picos de uso y reparto por función.
6. **Revocación con borrado.** Desactivar la telemetría elimina el historial local; los datos agregados caducan a los 90 días.

Para las instalaciones beta sin red privada, la propia app actúa como **relay**: reenvía los lotes hacia el receptor local del coordinador sin exponerlo a internet.

### 4.4. Empaquetado

Los scripts de `packaging/` construyen los instaladores copiando los mismos ficheros web y el mismo `server.py`, sin compilar nada:

- **macOS**: monta `NeverRed.app` (lanzador, verificador de actualizaciones, icono generado con herramientas del sistema, requisito mínimo macOS 11) y la comprime en `.dmg` con enlace a Aplicaciones. Al no incluir binarios, un único DMG sirve para Intel y Apple Silicon usando el Python del sistema.
- **Linux**: monta el directorio `AppDir` (aplicación, lanzador, icono, descriptor de escritorio) y lo convierte en `.AppImage` x86_64 con `appimagetool`. Doble clic tras dar permiso de ejecución; no instala nada.
- **Docker**: imagen mínima con usuario no root, volumen para la base de datos y chequeo de salud; publicitada en el registro de GitHub en cada versión.

Los lanzadores guardan los datos fuera del paquete (en la carpeta de soporte de la plataforma), reutilizan el servidor si ya está en marcha, abren el navegador automáticamente y detienen todo al salir o con `--stop` en Linux.

### 4.5. Auto-actualización

Cada arranque, el lanzador consulta la última release en GitHub y, si hay versión nueva, muestra el diálogo de actualización con los cambios. El mecanismo de confianza es estricto:

1. Se descargan el instalador nuevo **y su firma separada** (`.sig`, generada manualmente con la clave privada del proyecto tras cada release).
2. La firma se verifica con la **clave pública embebida** en el verificador, usando el esquema de firma de `ssh-keygen` (Ed25519) con identidad y espacio de nombres fijos del proyecto.
3. **Sin firma válida no se instala nada**: el fichero se descarta y se arranca la versión actual. El diseño es *fail-closed* por defecto, y el usuario puede omitir versiones concretas.

---

## 5. Análisis de ciberseguridad

### Fortalezas verificadas

| Área | Medida | Valoración |
|---|---|---|
| Contraseñas | PBKDF2-SHA256 600k iteraciones + sal + re-hash oportunista | Nivel sectorial (OWASP) |
| Sesiones | Cookie HttpOnly + SameSite, caducidad dual (absoluta e inactividad), tope de 10, revocación | Sólido; resiste robo por XSS y secuestro prolongado |
| Recuperación | Solo hash del token, TTL 1 h, token único, sin enumeración de usuarios | Bien diseñado |
| Autenticación | Tiempo constante + señuelo anti-enumeración, rate-limit por IP | Resiste fuerza bruta casual y enumeración |
| Borrado | Exige contraseña aun con sesión; cascada completa incl. telemetría | Cumple minimización y derecho de supresión |
| Cabeceras web | CSP restrictiva, anti-framing, no-sniff, sin caché en API | Buena higiene HTTP |
| Ficheros | Blocklist de extensiones y rutas sensibles; permisos 600 + umask | La BD no es servible por HTTP |
| Actualizaciones | Firma Ed25519 obligatoria, fail-closed | Modelo de confianza correcto |
| Cadena de suministro | Cero dependencias de ejecución; CI con versiones fijadas | Superficie mínima |
| Auditoría | Log de eventos de seguridad con retención de 90 días | Trazabilidad básica |
| Privacidad | Telemetría opt-in, agregada, revocable con borrado, retención 90 días | Compatible con RGPD por diseño |

El proyecto ha superado dos rondas internas de endurecimiento (versiones 1.8.0 y 1.8.1) que corrigieron una docena de hallazgos —respaldo inseguro de la BD, verificación de firmas débil, restos de sesión en cliente, hash con pocas iteraciones, sesiones sin expiración por inactividad, entre otros— con 27 pruebas automatizadas que los cubren.

### Riesgos y limitaciones honestas

1. **Sin verificación externa.** No hay auditoría independiente ni programa de recompensas; la afirmación de seguridad descansa en revisión propia y tests. Una auditoría externa puntual sería la inversión con mejor retorno en confianza.
2. **Cifrado en reposo ausente.** La base de datos está protegida por permisos del sistema operativo, no cifrada. En equipos compartidos o robados, los datos son legibles. Mitigación futura: SQLCipher o cifrado a nivel de documento con clave derivada de la contraseña.
3. **Sin segundo factor (2FA).** La cuenta se protege solo con contraseña. Es la mejora de autenticación más demandable a medio plazo.
4. **Demo con credenciales públicas.** La cuenta de demostración es accesible por cualquiera que alcance el servidor; es un riesgo aceptado y documentado, pero en despliegues públicos multiusuario la demo debería poder desactivarse con una variable de entorno.
5. **HTTP local por defecto.** En escritorio el tráfico queda en loopback (aceptable); en VPS el TLS depende de que el operador lo configure con el Caddy incluido. El instalador de servidor debería advertirlo o automatizarlo.
6. **Rate-limit en memoria.** Un reinicio lo vacía y no se comparte entre réplicas; suficiente para el modelo actual de instancia única, a revisar si se escala horizontalmente.
7. **Clave de firmado única.** La seguridad de las actualizaciones descansa en una sola clave privada; se recomienda custodia con copia de respaldo cifrada y plan de rotación documentado.

**Conclusión de seguridad:** para una aplicación de instancia única con datos locales, el nivel es alto y supera al de la mayoría de proyectos comparables de su tamaño. Los pendientes son conocidos, acotados y planificables; ninguno bloquea una presentación ante inversores, pero el cifrado en reposo y el 2FA deberían figurar en la hoja de ruta pública como compromiso.

---

## 6. Análisis de experiencia de usuario (UX)

### Aciertos

- **Primer minuto cuidado**: instalador de un clic, apertura automática del navegador, asistente de apertura o demo con un clic. Un evaluador puede ver valor en menos de 60 segundos.
- **Lenguaje cercano**: "Nunca en rojo", ecuación explicada en 30 segundos, acciones rápidas con ejemplos concretos (nómina, comida, transferencia al ahorro).
- **Prevención de errores contables**: la validación en vivo del asiento enseña partida doble sin manual; el duplicado y las plantillas reducen el trabajo repetitivo.
- **Transparencia radical**: panel de telemetría propio, estado de sincronización visible, ecuación contable siempre a la vista como "semáforo" de integridad.
- **Accesibilidad razonable**: modales con Escape y trampa de foco, tablas de datos para el gráfico, contraste del tema oscuro, formato de moneda localizado.

### Fricciones y carencias

1. **Sin aplicación móvil.** El uso es de escritorio/navegador; el registro del gasto en el momento (el caso de uso más frecuente en finanzas personales) hoy no existe. Es la carencia de producto más importante.
2. **Sin sincronización bancaria.** Todo es manual o por CSV. La competencia comercial ofrece PSD2; integrarlo choca con el modelo de privacidad, pero un importador programado de CSV sería un paso intermedio barato.
3. **Curva de la partida doble.** Para el público no contable, debe/haber intimida al principio; el asistente y los ejemplos lo mitigan, pero falta un modo "simple" (ingreso/gasto) que genere los asientos por debajo.
4. **macOS sin notarizar.** La primera apertura exige clic derecho → Abrir; fricción conocida de distribución fuera del App Store que conviene explicar en la web.
5. **Recuperación dependiente de SMTP.** Sin correo configurado no hay reseteo de contraseña; necesario un flujo alternativo local (pregunta de seguridad o código impreso) para instalaciones domésticas.
6. **Idioma único.** Interfaz solo en español; la internacionalización (i18n) es requisito para escalar mercado.

**Conclusión UX:** el producto es usable y autocontenido para su público inicial (personas ordenadas, pequeños autónomos, entusiastas de la privacidad), con un onboarding mejor que el de la mayoría del software contable. El salto a gran público exige móvil + modo simple + i18n, en ese orden.

---

## 7. Análisis de ingeniería de software

### Lo que está bien resuelto

- **Simplicidad radical como estrategia**: ~3.300 líneas de código propio, cero dependencias de ejecución, sin compilación. Cada línea la puede leer una persona en una tarde; eso es una ventaja competitiva en auditabilidad y mantenimiento.
- **Separación de responsabilidades**: lógica contable pura y testeable aparte de la E/S; validación duplicada cliente/servidor; telemetría como subsistema aislado con contrato cerrado.
- **Contratos y versionado**: ETag para concurrencia, `NEVERRED_BUILD` sincronizado con la versión del servidor y guardián anti-carcasa obsoleta, migraciones de esquema incrementales.
- **Automatización**: CI con tests de backend y frontend, releases reproducibles desde un tag, suites que cubren seguridad (timing, rate-limit, firmas, borrado) y negocio (cuadratura, presupuestos, recurrentes, CSV).
- **Documentación viva**: README operativo, web con guía/API/despliegue/desarrollo y CHANGELOG por versión.

### Deuda técnica y límites arquitectónicos

1. **Escalabilidad vertical del modelo de datos**: un documento JSON por usuario es elegante hasta cierto tamaño; con decenas de miles de asientos, la carga completa en cada guardado se volverá lenta. El camino natural es migrar asientos a filas relacionales manteniendo la API.
2. **SQLite + instancia única**: perfecto hoy (WAL, backup en caliente), incompatible con réplicas concurrentes. Escalar horizontalmente exigiría PostgreSQL y sesiones externas; no se necesita hasta tener tracción multiusuario real.
3. **Servidor HTTP de la stdlib**: suficiente para localhost y VPS modestos, sin las protecciones de un servidor de producción (límites de conexión, slow-loris). Tras proxy inverso en despliegues públicos es aceptable; conviene documentarlo como requisito.
4. **Frontend monolítico**: `app.js` supera las 1.400 líneas; con el modo simple, el móvil o i18n pedirá modularizar por vistas. La lógica pura ya extraída marca el patrón a seguir.
5. **Tests de interfaz limitados**: la lógica contable tiene tests en Node, pero los flujos de UI no tienen pruebas end-to-end (Playwright/Cypress). Añadir un puñado de *smoke tests* E2E es barato y sube mucho la confianza de release.
6. **Observabilidad mínima**: logs rotados y auditoría básica; sin métricas ni alertas. Aceptable en local; necesario en cuanto haya VPS multiusuario de pago.

**Conclusión técnica:** arquitectura honesta y proporcional a su fase: barata de mantener, fácil de auditar, con los límites conocidos y con rutas de evolución claras que no exigen reescrituras.

---

## 8. Alternativas en el mercado

El mercado se divide en tres familias: nube comercial española (facturación + fiscalidad), finanzas personales por suscripción, y autoalojado de código abierto. NeverRed compite directamente con la tercera y se posiciona como alternativa privada a la segunda.

| Alternativa | Modelo | Precio orientativo 2026 | Diferencia frente a NeverRed |
|---|---|---|---|
| **Firefly III** | Autoalojado, código abierto (AGPL), partida doble | Gratis (coste del servidor) | El rival directo: más maduro (desde 2014, ~24k estrellas), con API amplia y 2FA; pero exige PHP + MySQL + Docker, curva técnica alta y sin instalador de escritorio de un clic |
| **Actual Budget** | Autoalojado, código abierto, presupuesto por sobres | Gratis (servidor propio) | Excelente UX de presupuestos y sincronización bancaria opcional; no es contabilidad por partida doble ni genera balances formales |
| **YNAB** | Nube por suscripción, método de sobres | ~110–150 €/año | Líder en educación financiera y móvil; datos en su nube, sin partida doble, coste recurrente de por vida |
| **Holded** | Nube ERP español (Visma) | 7,50–99,50 €/mes + IVA | Contabilidad oficial, facturación Verifactu, CRM e inventario; orientado a empresas, precio recurrente y datos en terceros |
| **Cuéntica** | Nube española con asesoría | 9–29 €/mes + IVA | Planes tutelados que presentan impuestos; nicho autónomo, sin control local de datos |
| **Quipu / Sage / Anfix** | Nube contable/fiscal | 14–50 €/mes | Cobertura fiscal española completa (modelos, Verifactu); coste y complejidad de pyme, no finanzas personales |
| **GnuCash / hledger** | Escritorio / texto plano, abiertos | Gratis | Rigor contable máximo y privacidad total; UX de otra época (GnuCash) o solo para programadores (hledger) |
| **Hojas de cálculo** | Manual | Gratis | Flexibilidad total, cero rigor: sin cuadratura, sin informes, errores silenciosos |

**Hueco de mercado de NeverRed**: rigor de partida doble + instalador de un clic + privacidad total + coste cero, en español y con curva de aprendizaje asistida. Ningún competidor reúne las cuatro a la vez: los abiertos exigen perfil técnico, los fáciles cobran suscripción y custodian tus datos, y los españoles apuntan a fiscalidad de empresa, no a finanzas personales.

---

## 9. Análisis DAFO

| | **Positivo** | **Negativo** |
|---|---|---|
| **Interno** | **Fortalezas** — Partida doble real con validación estricta; cero dependencias y código auditable en una tarde; privacidad por construcción (opt-in, agregados, borrado); instaladores de un clic + auto-actualización firmada; coste operativo nulo; tests de seguridad y negocio; documentación completa | **Debilidades** — Sin app móvil; sin sincronización bancaria; curva inicial de la partida doble; interfaz solo en español; sin 2FA ni cifrado en reposo; equipo de una persona; sin auditoría externa; frontend monolítico; macOS sin notarizar |
| **Externo** | **Oportunidades** — Fatiga de suscripciones y desconfianza hacia la nube financiera; soberanía digital europea como viento de cola; Verifactu/facturación electrónica como puerta al autónomo; modo simple + móvil como multiplicadores de mercado; telemetría agregada como brújula de producto sin traicionar la privacidad; posible capa de servicios (despliegue gestionado, soporte) sobre base gratuita | **Amenazas** — Firefly III y Actual Budget ocupan el nicho abierto con comunidad; los ERP españoles bajan precios y añaden IA; cambios normativos (fiscalidad, PSD3) pueden exigir integraciones costosas; dependencia de GitHub Actions y del ecosistema Python/Apple para distribuir; clonación al ser código abierto sin modelo de defensa comercial aún definido |

---

## 10. Guía de puesta en marcha

Requisito común: abrir siempre la aplicación en `http://127.0.0.1:8000` (o la dirección del servidor), nunca como fichero local. Credenciales de demostración: `demo@neverred.local` / `DemoNeverRed2026`.

### 10.1. macOS (usuario final, 2 minutos)

1. Descargar `NeverRed-macOS-2.0.0.dmg` desde la página de Releases del repositorio.
2. Abrir el DMG y arrastrar `NeverRed.app` a Aplicaciones.
3. Primera apertura: clic derecho sobre la app → Abrir → Abrir (solo la primera vez, al no estar notarizada por Apple).
4. La app comprueba si hay actualizaciones, arranca su servidor y abre el navegador. Al salir de la app, todo se detiene solo.
5. Registrarse con el correo propio o entrar a la demo para explorar con datos de ejemplo.

Datos guardados en `~/Library/Application Support/NeverRed/`.

### 10.2. Linux (usuario final, 2 minutos)

1. Descargar `NeverRed-2.0.0-x86_64.AppImage` desde Releases.
2. Dar permiso de ejecución: clic derecho → Propiedades → "Permitir ejecutar", o en terminal `chmod +x NeverRed-*.AppImage`.
3. Doble clic: verifica actualizaciones, arranca y abre el navegador.
4. Para detenerlo por completo: `./NeverRed-*.AppImage --stop`.

Datos guardados en `~/.local/share/NeverRed/`.

### 10.3. Docker (autoalojado, 5 minutos)

1. Instalar Docker y ejecutar:
   `docker run -d --name neverred -p 8000:8000 -v neverred-data:/data --restart unless-stopped ghcr.io/emilio-devesa/neverred:2.0.0`
   (o `docker compose up -d --build` con el `compose.yaml` incluido).
2. Abrir `http://localhost:8000`, registrarse y trabajar. Los datos persisten en el volumen `neverred-data`.
3. Copia de seguridad programada (cada noche a las 3:00):
   `0 3 * * * docker exec neverred python3 backend/backup.py --db /data/neverred.db`
   El script conserva las últimas 14 copias.

### 10.4. VPS con dominio propio (despliegue público, 20 minutos)

1. Alquilar un VPS mínimo (1 vCPU, 1 GB de RAM sobra) con Ubuntu LTS y apuntar el DNS del dominio al servidor.
2. Instalar el proxy con TLS automático usando el `Caddyfile` incluido, que obtiene el certificado y redirige al puerto 8000.
3. Lanzar el servidor escuchando en todas las interfaces con `python3 backend/server.py --host 0.0.0.0` y activar `NEVERRED_TRUST_PROXY=1` para que el rate-limit vea la IP real tras el proxy.
4. Configurar el correo saliente (`NEVERRED_SMTP_HOST/PORT/USER/PASS/FROM` y `NEVERRED_APP_URL`) para que funcione la recuperación de contraseña.
5. Programar la copia diaria con `backend/backup.py` (retención de 14 días) y descargar periódicamente una copia fuera del servidor.
6. Endurecimiento recomendado: firewall con solo 80/443 abiertos, actualizaciones automáticas del sistema, y valorar desactivar la demo pública si el registro es abierto.

### 10.5. Desde código fuente (desarrollo)

1. Clonar el repositorio; no hay nada que instalar (Python 3.12 ya basta).
2. `python3 backend/server.py` y abrir `http://127.0.0.1:8000`.
3. `python3 backend/tests/test_server.py` (backend) y `node --test frontend/tests/` (lógica contable) para verificar.
4. `python3 backend/seed_demo.py` para cargar la demo local.

---

## 11. Conclusiones, extensibilidad y escalabilidad futuras

### Estado del programa

NeverRed 2.0.0 es un producto **completo y coherente en su ámbito**: una persona puede instalarlo, llevar su contabilidad con rigor profesional, importar sus extractos, presupuestar, informar y mantenerlo actualizado sin pagar ni exponer sus datos. La base técnica es sólida —seguridad por encima de su categoría, tests que cubren negocio y amenazas, documentación que permite operarlo sin su autor— y la telemetría opt-in le da, por primera vez, un instrumento para decidir con datos sin traicionar su promesa de privacidad.

### Extensibilidad (qué se puede construir encima, de menor a mayor coste)

1. **Modo simple ingreso/gasto** que genere asientos por debajo: abre el producto al público no contable sin tocar el motor.
2. **Internacionalización y multidivisa real**: la moneda ya es un campo por usuario; falta el catálogo de textos y tipos de cambio.
3. **2FA (TOTP) y cifrado en reposo**: las dos deudas de seguridad, bien acotadas.
4. **Importadores programados y reglas de categorización**: el 80 % del valor de la sincronización bancaria sin su coste regulatorio.
5. **App móvil complementaria** (captura del gasto + consulta) contra la misma API, que ya existe y está autenticada.
6. **Facturación Verifactu para autónomos**: la puerta al mercado español de pago; el motor de asientos ya sabe representar facturas.
7. **Multiempresa y roles** (familiar, asesor): el aislamiento por usuario ya existe; es extender el modelo, no reinventarlo.

### Escalabilidad (cuánto puede crecer sin reescribirse)

- **Hoy**: miles de asientos por usuario en instancia única con coste marginal cero; el cuello de botella teórico es la carga del documento JSON completo.
- **Siguiente orden de magnitud** (decenas de miles de asientos, VPS multiusuario de pago): migrar asientos a filas relacionales, paginar diario y mayor, y añadir pruebas end-to-end; todo ello sin cambiar la API ni la interfaz.
- **Escala horizontal real** (SaaS multi-tenant): PostgreSQL, sesiones externas, rate-limit distribuido y observabilidad; es la única fase que exige cambios arquitectónicos, y solo tiene sentido con tracción que la pague.

### Mensaje final para la mesa

NeverRed no compite en features con un ERP de 100 €/mes ni pretende hacerlo: compite en **confianza**. En un mercado donde cada app financiera cobra una suscripción a cambio de custodiar tus datos en su nube, NeverRed ofrece rigor contable, privacidad demostrable y coste cero, con un camino técnico claro hacia móvil, facturación y servicios de pago opcional. La pregunta para esta mesa no es si el producto funciona —funciona, está auditado a nivel interno y empaquetado— sino **a qué velocidad quieren que recorra esa hoja de ruta**.

---

*Informe generado a partir del código fuente (v2.0.0), la documentación del proyecto y análisis de mercado de septiembre de 2026. Precios de terceros orientativos y verificados en sus webs oficiales; pueden variar.*
