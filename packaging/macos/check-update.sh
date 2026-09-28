#!/bin/sh
# NeverRed — comprobación de actualizaciones (solo herramientas de macOS).
# Uso: check-update.sh <dir-MacOS> <dir-datos>
# Salida: 0 = seguir con el arranque normal; 42 = actualización instalada
# (la nueva app ya se ha abierto, hay que salir sin arrancar nada).
set -u
MACOS_DIR="$1"
DATA="$2"
REPO="emilio-devesa/NeverRed"
# Clave pública de release: solo se instala lo firmado con su privada
# (packaging/release-key, fuera del repo). Sin firma válida no hay update.
RELEASE_PUBKEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBXSqPW7x7HSHNj8akFRTv2dX7dUq1ZGnxwO5x3yRkCM"
SIGN_ID="neverred-release"
SIGN_NS="neverred-update"

verify_sig() { # $1=fichero $2=url-de-su-.sig — 0=válido, 1=rechazado
  [ -n "${2:-}" ] || return 1
  command -v ssh-keygen >/dev/null 2>&1 || return 1
  SIG="$DATA/.$(basename "$1").sig"
  curl -fsSL -m 60 -o "$SIG" "$2" 2>/dev/null || { rm -f "$SIG"; return 1; }
  ALLOW="$DATA/.allowed_signers"
  printf '%s %s\n' "$SIGN_ID" "$RELEASE_PUBKEY" > "$ALLOW"
  chmod 600 "$ALLOW"
  if ssh-keygen -Y verify -f "$ALLOW" -I "$SIGN_ID" -n "$SIGN_NS" \
      -s "$SIG" < "$1" >/dev/null 2>&1; then
    rm -f "$SIG" "$ALLOW"
    return 0
  fi
  rm -f "$SIG" "$ALLOW"
  return 1
}

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
sig = next((u for u in assets if u.endswith(".dmg.sig")), "")
body = str(rel.get("body") or "")[:900]
print(tag)
print(dmg)
print(sig)
print(body)
' <<<"$JSON")" || exit 0
REMOTE="$(printf '%s' "$EVAL" | sed -n 1p)"
ASSET="$(printf '%s' "$EVAL" | sed -n 2p)"
ASSET_SIG="$(printf '%s' "$EVAL" | sed -n 3p)"
NOTES="$(printf '%s' "$EVAL" | tail -n +4)"
[ -z "$REMOTE" ] || [ -z "$ASSET" ] && exit 0

mkdir -p "$DATA"
if [ -f "$DATA/skipped_version" ] && [ "$(cat "$DATA/skipped_version")" = "$REMOTE" ]; then exit 0; fi

# Versiones por entorno: el tag viene de la red y nunca se interpola en código.
NEWER="$(REMOTE="$REMOTE" LOCAL="$LOCAL" python3 -c '
import os
def v(s):
    return [int(x) for x in s.lstrip("v").split(".") if x.isdigit()]
try:
    print("1" if v(os.environ.get("REMOTE", "")) > v(os.environ.get("LOCAL", "")) else "0")
except Exception:
    print("0")
')"
[ "$NEWER" = "1" ] || exit 0

# Diálogo: pregunta separada del changelog (campo con scroll); si falla,
# se recurre al diálogo simple del sistema.
TKPY=""
for p in /usr/bin/python3 python3; do
  if $p -c 'import tkinter' 2>/dev/null; then TKPY=$p; break; fi
done
NOTES_FILE="$DATA/release-notes.txt"
printf '%s' "$NOTES" > "$NOTES_FILE"
CHOICE=""
if [ -n "$TKPY" ]; then
  CHOICE="$($TKPY "$MACOS_DIR/update-dialog.py" "$REMOTE" "$NOTES_FILE" 2>/dev/null)"
fi
case "$CHOICE" in
  install) CHOICE='Instalar' ;;
  skip) CHOICE='Omitir versión' ;;
  later) CHOICE='Más tarde' ;;
esac
if [ -z "$CHOICE" ]; then
  CHOICE="$(NOTES="$NOTES" REMOTE="$REMOTE" osascript -e '
set notes to system attribute "NOTES"
set ver to system attribute "REMOTE"
display dialog "Hay una nueva versión de NeverRed (" & ver & ") disponible." & return & return & notes buttons {"Omitir versión", "Más tarde", "Instalar"} default button "Instalar" cancel button "Más tarde" with title "Actualización de NeverRed" giving up after 120
' 2>/dev/null | sed -n 's/.*button returned:\(.*\)/\1/p')" || exit 0
fi

case "$CHOICE" in
  *Omitir*) echo "$REMOTE" > "$DATA/skipped_version"; exit 0 ;;
  *Instalar*) ;;
  *) exit 0 ;; # Más tarde, abandono o error: seguir normal
esac

# Descarga e instalación silenciosa, luego reabre la app nueva
DMG="$DATA/NeverRed-update.dmg"
curl -fsSL -m 120 -o "$DMG" "$ASSET" 2>/dev/null || exit 0
# Sin firma válida no se instala nada (ni se monta la imagen).
verify_sig "$DMG" "$ASSET_SIG" || { rm -f "$DMG"; exit 0; }
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
