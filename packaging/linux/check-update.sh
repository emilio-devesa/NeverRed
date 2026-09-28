#!/bin/sh
# NeverRed — comprobación de actualizaciones en Linux.
# Uso: check-update.sh <dir-app> <dir-datos>
# Salida: 0 = seguir normal; 42 = actualización instalada (salir).
# Diálogo con changelog desplazable (Tkinter); respaldo con zenity;
# si no hay entorno gráfico, avisa por consola y sigue.
set -u
APP_DIR="$1"
DATA="$2"
REPO="emilio-devesa/NeverRed"
# Clave pública de release: solo se ejecuta lo firmado con su privada
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
sig = next((a.get("browser_download_url", "") for a in rel.get("assets", [])
            if a.get("browser_download_url", "").endswith(".AppImage.sig")), "")
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

CHOICE=""
# 1) Diálogo propio con changelog desplazable (un Python con Tkinter)
TKPY=""
for p in /usr/bin/python3 python3; do
  if $p -c 'import tkinter' 2>/dev/null; then TKPY=$p; break; fi
done
if [ -n "$TKPY" ]; then
  NOTES_FILE="$DATA/release-notes.txt"
  printf '%s' "$NOTES" > "$NOTES_FILE"
  TK="$($TKPY "$(dirname "$0")/update-dialog.py" "$REMOTE" "$NOTES_FILE" 2>/dev/null)"
else
  TK=""
fi
case "$TK" in
  install) CHOICE='Instalar' ;;
  skip) CHOICE='Omitir versión'; echo "$REMOTE" > "$DATA/skipped_version"; exit 0 ;;
  later) exit 0 ;;
esac
# 2) Respaldo con zenity si existe entorno gráfico
if [ -z "$CHOICE" ] && command -v zenity >/dev/null 2>&1 && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
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

# Descarga la nueva AppImage, verifica su firma, la abre y salimos
NEW="$DATA/$(basename "$ASSET")"
curl -fsSL -m 300 -o "$NEW" "$ASSET" 2>/dev/null || exit 0
# Sin firma válida no se ejecuta nada.
verify_sig "$NEW" "$ASSET_SIG" || { rm -f "$NEW"; exit 0; }
chmod +x "$NEW"
printf '%s\n' "$REMOTE" > "$DATA/skipped_version"
if [ -f "$DATA/server.pid" ]; then kill "$(cat "$DATA/server.pid")" 2>/dev/null; rm -f "$DATA/server.pid"; fi
sleep 1
"$NEW" &
exit 42
