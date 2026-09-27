#!/bin/sh
# NeverRed — comprobación de actualizaciones en Linux.
# Uso: check-update.sh <dir-app> <dir-datos>
# Salida: 0 = seguir normal; 42 = actualización instalada (salir).
# Diálogo con zenity si existe; si no, avisa por consola y sigue.
set -u
APP_DIR="$1"
DATA="$2"
REPO="emilio-devesa/NeverRed"

[ "${NEVERRED_NO_UPDATE:-0}" = "1" ] && exit 0
command -v python3 >/dev/null 2>&1 || exit 0
command -v curl >/dev/null 2>&1 || exit 0

LOCAL="$(cat "$APP_DIR/VERSION" 2>/dev/null || echo 0.0.0)"
JSON="$(curl -fsSL -m 15 "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null)" || exit 0

EVAL="$(python3 -c '
import json, sys
try:
    rel = json.loads(sys.stdin.read())
except Exception:
    sys.exit(1)
tag = str(rel.get("tag_name") or "")
dmg = next((a.get("browser_download_url", "") for a in rel.get("assets", [])
            if a.get("browser_download_url", "").endswith(".AppImage")), "")
body = str(rel.get("body") or "")[:900]
print(tag)
print(dmg)
print(body)
' <<<"$JSON")" || exit 0
REMOTE="$(printf '%s' "$EVAL" | sed -n 1p)"
ASSET="$(printf '%s' "$EVAL" | sed -n 2p)"
NOTES="$(printf '%s' "$EVAL" | tail -n +3)"
[ -z "$REMOTE" ] || [ -z "$ASSET" ] && exit 0

mkdir -p "$DATA"
if [ -f "$DATA/skipped_version" ] && [ "$(cat "$DATA/skipped_version")" = "$REMOTE" ]; then exit 0; fi

NEWER="$(python3 -c '
def v(s):
    return [int(x) for x in s.lstrip("v").split(".") if x.isdigit()]
try:
    print("1" if v("'"$REMOTE"'") > v("'"$LOCAL"'") else "0")
except Exception:
    print("0")
')"
[ "$NEWER" = "1" ] || exit 0

CHOICE=""
if command -v zenity >/dev/null 2>&1 && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
  CHOICE="$(zenity --question --title="Actualización de NeverRed" \
    --text="Hay una nueva versión ($REMOTE) disponible.\n\n$NOTES" \
    --ok-label="Instalar" --cancel-label="Más tarde" \
    --extra-button="Omitir versión" 2>/dev/null; echo "rc=$?")"
  case "$CHOICE" in
    *Omitir*) echo "$REMOTE" > "$DATA/skipped_version"; exit 0 ;;
    *"rc=0"*) ;; # Instalar
    *) exit 0 ;; # Más tarde o cerrado
  esac
else
  echo "[neverred] Hay actualización ($REMOTE). Descárgala de https://github.com/$REPO/releases"
  exit 0
fi

# Descarga la nueva AppImage, la abre y salimos
NEW="$DATA/$(basename "$ASSET")"
curl -fsSL -m 300 -o "$NEW" "$ASSET" 2>/dev/null || exit 0
chmod +x "$NEW"
printf '%s\n' "$REMOTE" > "$DATA/skipped_version"
if [ -f "$DATA/server.pid" ]; then kill "$(cat "$DATA/server.pid")" 2>/dev/null; rm -f "$DATA/server.pid"; fi
sleep 1
"$NEW" &
exit 42
