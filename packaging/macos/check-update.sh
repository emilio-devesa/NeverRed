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

# Diario de a bordo: cada arranque del actualizador deja rastro (la 2.3.3
# enseñó que un actualizador silencioso es imposible de diagnosticar).
ULOG="$DATA/update.log"
ulog() {
  mkdir -p "$DATA" 2>/dev/null
  printf '%s %s\n' "$(date '+%F %T')" "$*" >>"$ULOG" 2>/dev/null
  tail -n 200 "$ULOG" 2>/dev/null > "$ULOG.tmp" && mv "$ULOG.tmp" "$ULOG" 2>/dev/null
}

# Solo señaliza el PID si de verdad es nuestro server.py (un pidfile rancio
# podría apuntar a otro proceso por reutilización de PIDs).
stop_server() { # $1=pidfile
  [ -f "$1" ] || return 1
  PID="$(cat "$1" 2>/dev/null)"
  case "$PID" in ''|*[!0-9]*) rm -f "$1"; return 1 ;; esac
  if ps -p "$PID" -o command= 2>/dev/null | grep -q 'server\.py'; then
    kill "$PID" 2>/dev/null
  fi
  rm -f "$1"
}

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
JSON="$(curl -fsSL -m 15 "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null)" || { ulog "sin red (local=$LOCAL)"; exit 0; }

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
if [ -f "$DATA/skipped_version" ] && [ "$(cat "$DATA/skipped_version")" = "$REMOTE" ]; then ulog "ya instalada $REMOTE (local=$LOCAL)"; exit 0; fi

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
[ "$NEWER" = "1" ] || { ulog "al día ($LOCAL)"; exit 0; }
ulog "nueva $REMOTE (local=$LOCAL)"

# Diálogo: pregunta separada del changelog (campo con scroll); si falla,
# se recurre al diálogo simple del sistema.
TKPY=""
for p in /usr/bin/python3 python3; do
  if $p -c 'import tkinter' 2>/dev/null; then TKPY=$p; break; fi
done
NOTES_FILE="$DATA/release-notes.txt"
printf '%s' "$NOTES" | tr -d '\r' > "$NOTES_FILE"
ulog "notas: ${#NOTES} caracteres"
CHOICE=""
if [ -n "$TKPY" ]; then
  ulog "diálogo tkinter ($TKPY)"
  CHOICE="$($TKPY "$MACOS_DIR/update-dialog.py" "$REMOTE" "$NOTES_FILE" "$LOCAL" 2>/dev/null)"
  [ -n "$CHOICE" ] && ulog "diálogo tkinter: $CHOICE"
fi
case "$CHOICE" in
  install) CHOICE='Instalar' ;;
  skip) CHOICE='Omitir versión' ;;
  later) CHOICE='Más tarde' ;;
esac
if [ -z "$CHOICE" ]; then
  ulog "diálogo sistema"
  # Sin comillas ni retornos: romperían el AppleScript. Recorte a lo que cabe.
  SHORTNOTES="$(printf '%s' "$NOTES" | tr -d '"\r' | head -c 400)"
  CHOICE="$(SHORTNOTES="$SHORTNOTES" REMOTE="$REMOTE" LOCAL="$LOCAL" osascript -e '
set notes to system attribute "SHORTNOTES"
set ver to system attribute "REMOTE"
set loc to system attribute "LOCAL"
display dialog "NeverRed " & ver & " disponible (tienes " & loc & ")." & return & return & notes buttons {"Omitir versión", "Más tarde", "Instalar"} default button "Instalar" cancel button "Más tarde" with title "Actualización de NeverRed" giving up after 120
' 2>/dev/null | sed -n 's/.*button returned:\(.*\)/\1/p')" || exit 0
fi

case "$CHOICE" in
  *Omitir*) echo "$REMOTE" > "$DATA/skipped_version"; ulog "omitida por el usuario: $REMOTE"; exit 0 ;;
  *Instalar*) ;;
  *) ulog "pospuesta por el usuario: $REMOTE"; exit 0 ;; # Más tarde, abandono o error: seguir normal
esac

# Descarga e instalación silenciosa, luego reabre la app nueva
DMG="$DATA/NeverRed-update.dmg"
curl -fsSL -m 120 -o "$DMG" "$ASSET" 2>/dev/null || { ulog "falló descarga $REMOTE"; exit 0; }
# Sin firma válida no se instala nada (ni se monta la imagen).
verify_sig "$DMG" "$ASSET_SIG" || { rm -f "$DMG"; ulog "firma inválida $REMOTE"; exit 0; }
MNT="$(hdiutil attach -nobrowse -readonly "$DMG" 2>/dev/null | awk -F'\t' '/Volumes/ {print $3; exit}')"
[ -z "$MNT" ] || [ ! -d "$MNT/NeverRed.app" ] && { rm -f "$DMG"; ulog "montaje fallido $REMOTE"; exit 0; }
# Destino: el bundle que ejecuta este actualizador (respeta dónde lo puso
# el usuario en su día; antes iba fijo a /Applications).
TARGET="$(cd "$MACOS_DIR/../.." && pwd)"
rm -rf "$TARGET"
cp -R "$MNT/NeverRed.app" "$TARGET"
# Refresca Launch Services: sin esto el Finder sigue mostrando la versión
# vieja (misma ruta + mismo identificador = metadatos cacheados).
touch "$TARGET" 2>/dev/null
if /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$TARGET" >/dev/null 2>&1; then
  ulog "Launch Services re-registrado"
else
  ulog "aviso: lsregister falló"
fi
hdiutil detach "$MNT" >/dev/null 2>&1
rm -f "$DMG"
# Solo se marca y reabre si la copia instalada trae la versión esperada
# (si se ejecutaba desde el DMG u otro sitio no escribible, no se finge éxito).
INSTALLED="$(defaults read "$TARGET/Contents/Info.plist" CFBundleShortVersionString 2>/dev/null || echo ?)"
if [ "${REMOTE#v}" != "$INSTALLED" ]; then
  ulog "instalación no verificada (instalado=$INSTALLED remoto=$REMOTE)"
  exit 0
fi
ulog "instalada $REMOTE"
printf '%s\n' "$REMOTE" > "$DATA/skipped_version"
# Detiene el servidor viejo para que la app nueva arranque limpia
if [ -f "$DATA/server.pid" ]; then stop_server "$DATA/server.pid"; fi
sleep 1
open "$TARGET"
exit 42
