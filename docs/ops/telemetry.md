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
- Aprobar, vetar o revocar desde el dashboard del monitor o con
  `POST /api/sink-identity` (`approve`, `reject`, `revoke`).
- El bearer compartido sigue disponible como modo legacy si el monitor define
  `NEVERRED_SINK_TOKEN`; el camino recomendado es la firma.

## Monitor y dashboard

```bash
python3 backend/telemetry_sink.py --host 127.0.0.1 --port 8140
```

El dashboard muestra totales, picos por día/hora, funciones, versiones,
plataformas, fallos medios e instalaciones pendientes/aprobadas/vetadas.
Debe escucharse en loopback o en una red privada, nunca expuesto directamente.

## Relay

Las betas sin acceso directo al monitor pueden enviar a
`POST /api/telemetry-ingest` o `/api/telemetry-enroll` de la app. El relay:

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
