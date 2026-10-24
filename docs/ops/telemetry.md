# Telemetría — operación (copia redactada)

> Copia pública y redactada de las notas privadas de operación. No contiene
> hostnames, URLs reales, tokens ni claves. La versión completa sigue fuera
> del repositorio.

## Arquitectura

- `backend/telemetry_common.py`: contrato compartido (catálogo, validadores,
  lectura de `telemetry.conf`, sobre canónico). Sin E/S ni BD.
- `backend/server.py`: store de la app, consentimiento y relay.
- `backend/telemetry.py`: conexión entre la infraestructura de envío y la BD
  de esta instalación.
- `backend/telemetry_forward.py`: forward, enrolamiento y firma Ed25519.
- `backend/telemetry_sink.py`: monitor; solo necesita `telemetry_common.py`.

## Configuración

La app es local pura salvo que exista `telemetry.conf` junto a la BD o que se
definan variables de entorno. Prioridad: entorno > `telemetry.conf` > valor
inyectado en el build.

```ini
# Nunca versionar valores reales.
NEVERRED_TELEMETRY_SINK=https://<tu-destino>
NEVERRED_TELEMETRY_TOKEN=<secreto-opcional>
```

Ubicaciones habituales:

- macOS: `~/Library/Application Support/NeverRed/telemetry.conf`
- Linux: `~/.local/share/NeverRed/telemetry.conf`
- Manual: junto a `NEVERRED_DB`

Sin destino configurado no sale nada del servidor.

## Emparejamiento asimétrico

- Primera ejecución con destino y sin token: la app genera
  `telemetry-key[.pub]` (Ed25519 con `ssh-keygen`, permisos `600`) junto a la
  BD y se enrola sola.
- Sin aprobar, el monitor responde `pending` y la app retiene los agregados
  (watermark intacto). Al aprobar, el siguiente envío los entrega.
- Aprobar, vetar o revocar:
  - desde el dashboard del monitor (los formularios llevan ya el token
    anti-CSRF oculto), o
  - por API en dos pasos (el endpoint exige token CSRF y valida Host de
    loopback + Origin/Referer propios, anti CSRF web y anti DNS-rebinding):

    ```bash
    CSRF=$(curl -s http://127.0.0.1:8140/api/sink-summary | python3 -c "import json,sys; print(json.load(sys.stdin)['csrf'])")
    curl -s -X POST http://127.0.0.1:8140/api/sink-identity \
      -H "Content-Type: application/json" \
      -d "{\"install_id\": \"...\",
           \"action\": \"approve\",      # reject | revoke
           \"csrf\": \"$CSRF\"}"
    ```
- El bearer compartido sigue disponible como modo legacy si el monitor define
  `NEVERRED_SINK_TOKEN`; el camino recomendado es la firma.

## Monitor y dashboard

```bash
python3 backend/telemetry_sink.py --host 127.0.0.1 --port 8140
```

El dashboard muestra totales, picos por día/hora, funciones, versiones,
plataformas, fallos medios e instalaciones pendientes/aprobadas/vetadas.
Debe escucharse en loopback o en una red privada, nunca expuesto
directamente.

**Hardening del panel (desde `d0e69ab`, 2026-10-24):** el panel no tiene
login, así que sus tres rutas de control (panel, `/api/sink-summary` y
`/api/sink-identity`) ejecutan tres defensas, por este orden:
1. **Host de loopback obligatorio** — mata el DNS-rebinding en todas las
   rutas de control (el rebinding pide por el dominio del atacante);
2. **`Origin`/`Referer` propios** si el navegador los manda — un
   formulario cruzado de otra web (CSRF a localhost) queda rechazado; el
   CLI sin cabeceras de navegador sigue admitido;
3. **Token anti-CSRF** (TTL 1 h, no de un solo uso, tope 1000 activos) —
   lo emite el propio HTML del panel (campo oculto `name=csrf` en
   approve/reject/revoke) y `/api/sink-summary`; el POST lo exige.

Además ambos servicios (app y monitor) mandan `Referrer-Policy:
no-referrer` y `Permissions-Policy`
`camera=(), microphone=(), geolocation=()` en toda respuesta.

Al actualizar la copia desplegada en
`~/Library/Application Support/NeverRed-monitor/`, re-copiar los cuatro
módulos (`telemetry_common.py`, `telemetry_forward.py`,
`telemetry_sink.py`, `telemetry_relay.py`) y `launchctl bootout` +
`bootstrap` de los dos agentes (la capa incluye este hardening).

## Despliegue permanente (monitor + relay + funnel)

Para recibir telemetría de máquinas externas sin depender de que una
instalación de la app esté abierta:

- **Monitor** (`telemetry_sink.py`, loopback 127.0.0.1:8140): la BD es
  `telemetry-sink.db` junto al propio script. Copia operativa en
  `~/Library/Application Support/NeverRed-monitor/` — los LaunchAgents
  no pueden leer `~/Documents` (TCC), así que allí se copian los módulos
  (`telemetry_common.py`, `telemetry_forward.py`, `telemetry_sink.py`,
  `telemetry_relay.py`) y la BD; el repo queda como fuente. Poner al día:
  re-copiar módulos + BD y `launchctl bootout`/`bootstrap`.
- **Relay público** (`telemetry_relay.py`, loopback 127.0.0.1:8082):
  servicio independiente, pequeño, que SOLO retransmite
  `POST /api/telemetry-ingest` y `/api/telemetry-enroll` al monitor
  (Authorization intacta; el monitor revalida todo). El dashboard y
  `/api/sink-identity` (aprobar/vetar) **no se exponen**: el proxy de
  Tailscale entra como 127.0.0.1 y el control «Solo local.» del monitor
  vendría de menos — por eso no sale por el funnel. Rate-limit por IP
  con `X-Forwarded-For` (60 en 600 s, `NEVERRED_RELAY_RATE_MAX/WINDOW`,
  fail-closed) y prefijo de camino por `NEVERRED_RELAY_PREFIX`
  (`/telemetry`).
- **LaunchAgents** (`~/Library/LaunchAgents/io.github.emilio-devesa.
  neverred.telemetry-{sink,relay}.plist`): RunAtLoad + KeepAlive, logs
  en `~/Library/Logs/NeverRed/`.
- **Funnel** (Tailscale): publicado en la raíz del host público sin que
  la app esté abierta. Servir/revisión:

  ```bash
  /Applications/Tailscale.app/Contents/MacOS/Tailscale funnel --bg http://127.0.0.1:8082/telemetry
  /Applications/Tailscale.app/Contents/MacOS/Tailscale funnel status
  ```

Los clientes apuntan `NEVERRED_TELEMETRY_SINK` a la URL pública del host
(sin sufijo). End-to-end verificado desde una máquina Windows externa:
`ingest` inválido → 401 del monitor, `enroll` válido →
`{"enroll": "pending"}`; el `approve` sigue siendo solo local.

Nota Sybil: el `enroll` público sigue aceptando altas pendientes sin
límite de registro (quedan en el dashboard para aprobar/vetar); nada se
almacena como telemetría hasta que la identidad esté aprobada y la firma
verificar. Es el «Pendiente de protocolo» de abajo.

## Relay

Las betas sin acceso directo al monitor pueden enviar a
`POST /api/telemetry-ingest` o `/api/telemetry-enroll`. Hay dos relays:
el de la app (`server.py`, vivo mientras la app corre) y el permanente
(`telemetry_relay.py`, el funnel). Ambos:

- valida tamaño y JSON,
- aplica rate-limit,
- reenvía al sink local fijo (`NEVERRED_SINK_LOCAL`, por defecto
  `http://127.0.0.1:8140`),
- conserva la autenticación para que el monitor la valide.

El destino no lo elige el cliente: no hay SSRF.

## Reintentos e idempotencia

- Backoff exponencial: 60 s, 120 s, 240 s… hasta 60 min.
- El monitor hace upsert por `(install_id, día, hora, evento)`.
- Solo se envían conteos agregados; nunca importes, textos ni datos
  contables.
- La cola no se pierde si el monitor no está disponible o una instalación
  está pendiente.

## Pendiente de protocolo

Ventanas de enrolamiento, límite de altas por periodo y alertas de lote para
mitigar Sybil si el endpoint público se expone. No cambia el formato actual.
