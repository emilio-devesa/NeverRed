#!/bin/sh
# NeverRed — utilidades comunes de los actualizadores macOS/Linux.
# Se copia junto a check-update.sh dentro del paquete. No se ejecuta suelto.
# Requiere que DATA esté definido antes de cargarlo.

REPO="emilio-devesa/NeverRed"
# Clave pública de release: solo se instala lo firmado con su privada
# (packaging/release-key, fuera del repo). Sin firma válida no hay update.
RELEASE_PUBKEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBXSqPW7x7HSHNj8akFRTv2dX7dUq1ZGnxwO5x3yRkCM"
SIGN_ID="neverred-release"
SIGN_NS="neverred-update"

# Diario de a bordo: cada arranque del actualizador deja rastro (la 2.3.3
# enseñó que un actualizador silencioso es imposible de diagnosticar).
ulog() {
  [ -n "${DATA:-}" ] || return 0
  mkdir -p "$DATA" 2>/dev/null
  printf '%s %s\n' "$(date '+%F %T')" "$*" >>"$DATA/update.log" 2>/dev/null
  tail -n 200 "$DATA/update.log" 2>/dev/null > "$DATA/update.log.tmp" \
    && mv "$DATA/update.log.tmp" "$DATA/update.log" 2>/dev/null
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

# JSON de la última release → tag, URL del activo y URL de su .sig, notas.
# $1 = sufijo del activo (.dmg o .AppImage). Lee el JSON por stdin.
release_eval() {
  RELEASE_SUFFIX="$1" python3 -c '
import json, os, sys
try:
    rel = json.loads(sys.stdin.read())
except Exception:
    sys.exit(1)
tag = str(rel.get("tag_name") or "")
assets = [a.get("browser_download_url", "") for a in rel.get("assets", [])]
suffix = os.environ.get("RELEASE_SUFFIX", "")
asset = next((u for u in assets if u.endswith(suffix)), "")
sig = next((u for u in assets if u.endswith(suffix + ".sig")), "")
body = str(rel.get("body") or "")[:900]
print(tag)
print(asset)
print(sig)
print(body)
'
}

# ¿La versión remota es mayor que la local? Imprime 1 o 0.
version_newer() { # $1=remota $2=local
  REMOTE="$1" LOCAL="$2" python3 -c '
import os
def v(s):
    return [int(x) for x in s.lstrip("v").split(".") if x.isdigit()]
try:
    print("1" if v(os.environ.get("REMOTE", "")) > v(os.environ.get("LOCAL", "")) else "0")
except Exception:
    print("0")
'
}

is_skipped() { # $1=versión remota
  [ -f "$DATA/skipped_version" ] && [ "$(cat "$DATA/skipped_version" 2>/dev/null)" = "$1" ]
}

mark_skipped() { # $1=versión
  printf '%s\n' "$1" > "$DATA/skipped_version"
}

# Descarga y verifica la firma; si algo falla, no deja el archivo a medias.
download_signed() { # $1=url $2=destino $3=url-firma $4=timeout
  DOWNLOAD_ERR=""
  curl -fsSL -m "$4" -o "$2" "$1" 2>/dev/null || { DOWNLOAD_ERR="download"; return 1; }
  if ! verify_sig "$2" "$3"; then
    rm -f "$2"
    DOWNLOAD_ERR="signature"
    return 1
  fi
  return 0
}

# Diálogo Tkinter compartido: imprime install/skip/later (o nada si no hay Tk).
ask_tk() { # $1=update-dialog.py $2=remota $3=notas $4=local
  for p in /usr/bin/python3 python3; do
    if $p -c 'import tkinter' 2>/dev/null; then
      "$p" "$1" "$2" "$3" "$4" 2>/dev/null
      return 0
    fi
  done
  return 1
}
