#!/bin/sh
# NeverRed — comprobación de actualizaciones (solo herramientas de macOS).
# Uso: check-update.sh <dir-MacOS> <dir-datos>
# Salida: 0 = seguir con el arranque normal; 42 = actualización instalada
# (la nueva app ya se ha abierto, hay que salir sin arrancar nada).
set -u
MACOS_DIR="$1"
DATA="$2"
REPO="emilio-devesa/NeverRed"

[ "${NEVERRED_NO_UPDATE:-0}" = "1" ] && exit 0
command -v python3 >/dev/null 2>&1 || exit 0

LOCAL="$(defaults read "$MACOS_DIR/../Info.plist" CFBundleShortVersionString 2>/dev/null || echo 0.0.0)"
JSON="$(curl -fsSL -m 15 "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null)" || exit 0

EVAL="$(python3 -c '
import json, sys
try:
    rel = json.loads(sys.stdin.read())
except Exception:
    sys.exit(1)
tag = str(rel.get("tag_name") or "")
assets = [a.get("browser_download_url", "") for a in rel.get("assets", [])]
dmg = next((u for u in assets if u.endswith(".dmg")), "")
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

# Diálogo nativo de macOS: Instalar / Más tarde / Omitir versión
CHOICE="$(NOTES="$NOTES" REMOTE="$REMOTE" osascript -e '
set notes to system attribute "NOTES"
set ver to system attribute "REMOTE"
display dialog "Hay una nueva versión de NeverRed (" & ver & ") disponible." & return & return & notes buttons {"Omitir versión", "Más tarde", "Instalar"} default button "Instalar" cancel button "Más tarde" with title "Actualización de NeverRed" giving up after 120
' 2>/dev/null | sed -n 's/.*button returned:\(.*\)/\1/p')" || exit 0

case "$CHOICE" in
  *Omitir*) echo "$REMOTE" > "$DATA/skipped_version"; exit 0 ;;
  *Instalar*) ;;
  *) exit 0 ;; # Más tarde, abandono o error: seguir normal
esac

# Descarga e instalación silenciosa, luego reabre la app nueva
DMG="$DATA/NeverRed-update.dmg"
curl -fsSL -m 120 -o "$DMG" "$ASSET" 2>/dev/null || exit 0
MNT="$(hdiutil attach -nobrowse -readonly "$DMG" 2>/dev/null | awk -F'\t' '/Volumes/ {print $3; exit}')"
[ -z "$MNT" ] || [ ! -d "$MNT/NeverRed.app" ] && { rm -f "$DMG"; exit 0; }
TARGET="/Applications/NeverRed.app"
rm -rf "$TARGET"
cp -R "$MNT/NeverRed.app" "$TARGET"
hdiutil detach "$MNT" >/dev/null 2>&1
rm -f "$DMG"
printf '%s\n' "$REMOTE" > "$DATA/skipped_version"
# Detiene el servidor viejo para que la app nueva arranque limpia
if [ -f "$DATA/server.pid" ]; then kill "$(cat "$DATA/server.pid")" 2>/dev/null; rm -f "$DATA/server.pid"; fi
sleep 1
open "$TARGET"
exit 42
