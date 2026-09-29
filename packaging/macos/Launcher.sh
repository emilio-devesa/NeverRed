#!/bin/sh
# NeverRed — lanzador macOS. Arranca el servidor (o lo reutiliza) y abre la app.
# Sin dependencias: usa el python3 del sistema (solo stdlib) y el navegador.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
RES="$HERE/../Resources/app"
DATA="$HOME/Library/Application Support/NeverRed"
PORT="${PORT:-8000}"
URL="http://127.0.0.1:$PORT"
PIDF="$DATA/server.pid"
mkdir -p "$DATA"

# Actualizaciones: si instala una nueva, ya reabre la app y salimos
if [ -x "$HERE/check-update.sh" ]; then
  UPD=0
  "$HERE/check-update.sh" "$HERE" "$DATA" || UPD=$?
  if [ "$UPD" -eq 42 ]; then exit 0; fi
fi

if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null \
   && curl -sf -o /dev/null "$URL/api/health" 2>/dev/null; then
  /usr/bin/open "$URL"  # ya estaba en marcha: solo abre el navegador
  exit 0
fi

if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=/usr/bin/python3; fi
export NEVERRED_DB="$DATA/neverred.db" HOST=127.0.0.1 PORT="$PORT"
# Telemetría: destino por defecto inyectado al construir el paquete. En el
# primer arranque se escribe telemetry.conf junto a los datos para que el
# destino quede visible y editable. Prioridad: entorno > telemetry.conf >
# defecto del build. Sin destino: local pura, nada sale de este equipo.
_SINK_DEF="__NEVERRED_BUILD_SINK__"
_TOKEN_DEF="__NEVERRED_BUILD_TOKEN__"
case "$_SINK_DEF" in __*) _SINK_DEF="";; esac
case "$_TOKEN_DEF" in __*) _TOKEN_DEF="";; esac
if [ ! -f "$DATA/telemetry.conf" ]; then
  {
    echo "# Destino de telemetría (opcional). Sin destino, nada sale de este equipo."
    echo "# Prioridad: entorno > este fichero > defecto del build."
    if [ -n "$_SINK_DEF" ]; then echo "NEVERRED_TELEMETRY_SINK=$_SINK_DEF"; else echo "#NEVERRED_TELEMETRY_SINK=https://..."; fi
    if [ -n "$_TOKEN_DEF" ]; then echo "NEVERRED_TELEMETRY_TOKEN=$_TOKEN_DEF"; else echo "#NEVERRED_TELEMETRY_TOKEN=..."; fi
  } > "$DATA/telemetry.conf"
fi
if [ -z "${NEVERRED_TELEMETRY_SINK:-}" ] && [ -f "$DATA/telemetry.conf" ]; then
  NEVERRED_TELEMETRY_SINK="$(sed -n 's/^NEVERRED_TELEMETRY_SINK=//p' "$DATA/telemetry.conf" | head -1)"
  : "${NEVERRED_TELEMETRY_SINK:=$_SINK_DEF}"
  export NEVERRED_TELEMETRY_SINK
fi
if [ -z "${NEVERRED_TELEMETRY_TOKEN:-}" ] && [ -f "$DATA/telemetry.conf" ]; then
  NEVERRED_TELEMETRY_TOKEN="$(sed -n 's/^NEVERRED_TELEMETRY_TOKEN=//p' "$DATA/telemetry.conf" | head -1)"
  : "${NEVERRED_TELEMETRY_TOKEN:=$_TOKEN_DEF}"
  export NEVERRED_TELEMETRY_TOKEN
fi
export NEVERRED_QUIT_WHEN_IDLE=1 NEVERRED_IDLE_TIMEOUT=20
"$PY" "$RES/backend/server.py" >"$DATA/server.log" 2>&1 &
SRV=$!
echo $SRV > "$PIDF"
# Al salir de la app (Dock → Salir) se detiene el servidor: sin actividad oculta.
trap 'kill $SRV 2>/dev/null; rm -f "$PIDF"; exit 0' TERM INT
i=0
while [ $i -lt 20 ]; do
  curl -sf -o /dev/null "$URL/api/health" 2>/dev/null && break
  sleep 0.5; i=$((i + 1))
done
/usr/bin/open "$URL"
wait $SRV
