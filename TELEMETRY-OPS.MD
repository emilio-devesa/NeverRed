# Telemetría — notas del coordinador (PRIVADO, no subir a git)

Movido del README el 2026-09-29: el usuario final solo debe saber que
existe la opción, qué se recopila y cómo activarla/desactivarla.
Todo lo de abajo es operación interna del programa beta.

## Forward store-and-forward

- `NEVERRED_TELEMETRY_SINK` (URL del receptor) + `NEVERRED_TELEMETRY_TOKEN`
  (secreto compartido) → un hilo envía cada 15 min (`NEVERRED_FORWARD_EVERY`)
  solo agregados `(instalación, día, hora, evento, conteo)` + versión,
  plataforma (solo el SO) y contadores de intentos.
- Sin receptor a la vista: backoff (1, 2, 4… máx. 60 min); el receptor hace
  upsert idempotente.
- Panel del coordinador: `telemetry_sink.py`, endpoint `/` (totales, picos
  día/hora, tartas funciones/versiones/plataformas, fallos medios).

## Receptor mínimo (Mac, red Tailscale)

```bash
NEVERRED_SINK_TOKEN=<secreto> python3 backend/telemetry_sink.py --host 0.0.0.0 --port 8140
```

## Exposición (solo cabe un funnel público por máquina)

- Funnel público → app: `tailscale funnel 8000 &` (CLI en
  `/Applications/Tailscale.app/Contents/MacOS/Tailscale`).
- Sink solo en localhost; betas sin tailnet llegan por el relay
  `POST /api/telemetry-ingest` de la propia app (pública) → reenvía al sink
  local tras revalidar tamaño y rate-limit. Con tailnet vale el sink directo
  por `serve` (`--https=8443`).
- En cada beta: `NEVERRED_TELEMETRY_SINK=https://<tu-maquina>.ts.net
  NEVERRED_TELEMETRY_TOKEN=<secreto>`.

## Programa beta actual (v2.0.0)

- Builds beta traen receptor y token horneados en los lanzadores
  (`Launcher.sh` / `AppRun`): sink `https://neverred-dev.tail2f2ed4.ts.net`,
  token `JU_D135s5vVbPZWdqkkETQSLkizfKT46Hqc8nUgUxoI` (público por diseño,
  rotar por release).
- El receptor debe arrancar con ese mismo token.
- Endpoints internos (no documentar al usuario): `GET /api/telemetry-summary`,
  `POST /api/telemetry-forward`, `GET /api/telemetry-forward-status`,
  `POST /api/telemetry-ingest`.
