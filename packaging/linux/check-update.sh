#!/bin/sh
# NeverRed — comprobación de actualizaciones en Linux.
# Uso: check-update.sh <dir-app> <dir-datos>
# Salida: 0 = seguir normal; 42 = actualización instalada (salir).
# Diálogo con changelog desplazable (Tkinter); respaldo con zenity;
# si no hay entorno gráfico, avisa por consola y sigue.
set -u
APP_DIR="$1"
DATA="$2"
# Funciones comunes (constantes de release, firma, descarga, versiones).
. "$(dirname "$0")/update-common.sh" 2>/dev/null || exit 0

[ "${NEVERRED_NO_UPDATE:-0}" = "1" ] && exit 0
command -v python3 >/dev/null 2>&1 || exit 0
command -v curl >/dev/null 2>&1 || exit 0

LOCAL="$(cat "$APP_DIR/VERSION" 2>/dev/null || echo 0.0.0)"
JSON="$(curl -fsSL -m 15 "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null)" || exit 0

EVAL="$(printf '%s' "$JSON" | release_eval .AppImage)" || exit 0
REMOTE="$(printf '%s' "$EVAL" | sed -n 1p)"
ASSET="$(printf '%s' "$EVAL" | sed -n 2p)"
ASSET_SIG="$(printf '%s' "$EVAL" | sed -n 3p)"
NOTES="$(printf '%s' "$EVAL" | tail -n +4)"
[ -z "$REMOTE" ] || [ -z "$ASSET" ] && exit 0

mkdir -p "$DATA"

# Protocolo del intercambio (portado de Windows): si un intento anterior
# quedó a medias (.update_pending sin .update_ok), se borra la marca de
# «omitir versión» para que esa versión vuelva a ofertarse. Nota: en Linux
# «instalada» significa «la AppImage nueva arrancó» (la original no se
# sustituye en disco), así que .update_ok guarda la versión lanzada.
PEND="$DATA/.update_pending"
OKF="$DATA/.update_ok"
if [ -f "$OKF" ]; then
  rm -f "$PEND"
elif [ -f "$PEND" ]; then
  echo "[neverred] intercambio anterior no concluido: se reofrece $REMOTE" >&2
  rm -f "$DATA/skipped_version" "$PEND"
fi
if is_skipped "$REMOTE"; then exit 0; fi

NEWER="$(version_newer "$REMOTE" "$LOCAL")"
[ "$NEWER" = "1" ] || exit 0

CHOICE=""
# 1) Diálogo propio con changelog desplazable (un Python con Tkinter)
NOTES_FILE="$DATA/release-notes.txt"
printf '%s' "$NOTES" > "$NOTES_FILE"
TK="$(ask_tk "$(dirname "$0")/update-dialog.py" "$REMOTE" "$NOTES_FILE" "$LOCAL")"
case "$TK" in
  install) CHOICE='Instalar' ;;
  skip) mark_skipped "$REMOTE"; exit 0 ;;
  later) exit 0 ;;
esac
# 2) Respaldo con zenity si existe entorno gráfico
if [ -z "$CHOICE" ]; then
  if command -v zenity >/dev/null 2>&1 && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    CHOICE="$(zenity --question --title="Actualización de NeverRed" \
      --text="NeverRed ($REMOTE) disponible (tienes $LOCAL).\n\n$NOTES" \
      --ok-label="Instalar" --cancel-label="Más tarde" \
      --extra-button="Omitir versión" 2>/dev/null; echo "rc=$?")"
    case "$CHOICE" in
      *Omitir*) mark_skipped "$REMOTE"; exit 0 ;;
      *"rc=0"*) ;; # Instalar
      *) exit 0 ;; # Más tarde o cerrado
    esac
  else
    echo "[neverred] Hay actualización ($REMOTE). Descárgala de https://github.com/$REPO/releases"
    exit 0
  fi
fi

# Descarga la nueva AppImage, verifica su firma, la arranca y se retira.
# Solo se marca «omitir versión» cuando la nueva está viva: si el arranque
# falla, la versión vuelve a ofrecerse en la próxima comprobación (antes se
# marcaba antes de arrancar y un fallo la descartaba para siempre).
NEW="$DATA/$(basename "$ASSET")"
download_signed "$ASSET" "$NEW" "$ASSET_SIG" 300 || exit 0
chmod +x "$NEW"
printf '%s\n' "$REMOTE" > "$PEND"
if [ -f "$DATA/server.pid" ]; then stop_server "$DATA/server.pid"; fi
sleep 1
"$NEW" &
NPID=$!
sleep 4
if kill -0 "$NPID" 2>/dev/null; then
  mark_skipped "$REMOTE"
  printf '%s\n' "$REMOTE" > "$OKF"
  exit 42
fi
# La nueva murió al arrancar: sin marca; la vieja sigue intacta en disco.
rm -f "$PEND"
echo "[neverred] la nueva AppImage no arrancó; se conserva la $LOCAL" >&2
exit 0
