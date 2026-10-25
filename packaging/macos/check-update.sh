#!/bin/sh
# NeverRed — comprobación de actualizaciones (solo herramientas de macOS).
# Uso: check-update.sh <dir-MacOS> <dir-datos>
# Salida: 0 = seguir con el arranque normal; 42 = actualización instalada
# (la nueva app ya se ha abierto, hay que salir sin arrancar nada).
set -u
MACOS_DIR="$1"
DATA="$2"
# Funciones comunes (constantes de release, firma, descarga, versiones).
. "$(dirname "$0")/update-common.sh" 2>/dev/null || exit 0

[ "${NEVERRED_NO_UPDATE:-0}" = "1" ] && exit 0
command -v python3 >/dev/null 2>&1 || exit 0

LOCAL="$(defaults read "$MACOS_DIR/../Info.plist" CFBundleShortVersionString 2>/dev/null || echo 0.0.0)"
JSON="$(curl -fsSL -m 15 "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null)" || { ulog "sin red (local=$LOCAL)"; exit 0; }

EVAL="$(printf '%s' "$JSON" | release_eval .dmg)" || exit 0
REMOTE="$(printf '%s' "$EVAL" | sed -n 1p)"
ASSET="$(printf '%s' "$EVAL" | sed -n 2p)"
ASSET_SIG="$(printf '%s' "$EVAL" | sed -n 3p)"
NOTES="$(printf '%s' "$EVAL" | tail -n +4)"
[ -z "$REMOTE" ] || [ -z "$ASSET" ] && exit 0

mkdir -p "$DATA"
if is_skipped "$REMOTE"; then ulog "ya instalada $REMOTE (local=$LOCAL)"; exit 0; fi

NEWER="$(version_newer "$REMOTE" "$LOCAL")"
[ "$NEWER" = "1" ] || { ulog "al día ($LOCAL)"; exit 0; }
ulog "nueva $REMOTE (local=$LOCAL)"

# Diálogo: pregunta separada del changelog (campo con scroll); si falla,
# se recurre al diálogo simple del sistema.
NOTES_FILE="$DATA/release-notes.txt"
printf '%s' "$NOTES" | tr -d '\r' > "$NOTES_FILE"
ulog "notas: ${#NOTES} caracteres"
CHOICE="$(ask_tk "$MACOS_DIR/update-dialog.py" "$REMOTE" "$NOTES_FILE" "$LOCAL")"
[ -n "$CHOICE" ] && ulog "diálogo tkinter: $CHOICE"
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
  *Omitir*) mark_skipped "$REMOTE"; ulog "omitida por el usuario: $REMOTE"; exit 0 ;;
  *Instalar*) ;;
  *) ulog "pospuesta por el usuario: $REMOTE"; exit 0 ;; # Más tarde, abandono o error: seguir normal
esac

# Descarga e instalación silenciosa, luego reabre la app nueva
DMG="$DATA/NeverRed-update.dmg"
if ! download_signed "$ASSET" "$DMG" "$ASSET_SIG" 120; then
  if [ "$DOWNLOAD_ERR" = "signature" ]; then ulog "firma inválida $REMOTE"; else ulog "falló descarga $REMOTE"; fi
  exit 0
fi
MNT="$(hdiutil attach -nobrowse -readonly "$DMG" 2>/dev/null | awk -F'\t' '/Volumes/ {print $3; exit}')"
[ -z "$MNT" ] || [ ! -d "$MNT/NeverRed.app" ] && { rm -f "$DMG"; ulog "montaje fallido $REMOTE"; exit 0; }
# Destino: el bundle que ejecuta este actualizador (respeta dónde lo puso
# el usuario en su día; antes iba fijo a /Applications).
TARGET="$(cd "$MACOS_DIR/../.." && pwd)"
PARENT="$(dirname "$TARGET")"
BASE="$(basename "$TARGET")"
# Restos huérfanos de un intento anterior cuyo proceso murió a mitad de
# swap (el rm -rf por $$ de cada ejecución solo cubre su propio PID).
rm -rf "$PARENT/.$BASE.nuevo."* "$PARENT/.$BASE.viejo."* 2>/dev/null
STAGE="$PARENT/.$BASE.nuevo.$$"
OLD="$PARENT/.$BASE.viejo.$$"

# 1) Copia aparte y verificada. Si algo falla aquí, la app actual no se toca:
#    antes un `rm -rf` previo dejaba al usuario sin aplicación.
rm -rf "$STAGE"
if ! cp -R "$MNT/NeverRed.app" "$STAGE" 2>/dev/null; then
  rm -rf "$STAGE"
  hdiutil detach "$MNT" >/dev/null 2>&1
  rm -f "$DMG"
  ulog "copia fallida (destino=$PARENT); se conserva la versión actual"
  exit 0
fi
STAGED="$(defaults read "$STAGE/Contents/Info.plist" CFBundleShortVersionString 2>/dev/null || echo ?)"
hdiutil detach "$MNT" >/dev/null 2>&1
rm -f "$DMG"
if [ "${REMOTE#v}" != "$STAGED" ]; then
  rm -rf "$STAGE"
  ulog "copia no verificada (copiado=$STAGED remoto=$REMOTE); se conserva la actual"
  exit 0
fi

# 2) Intercambio con vuelta atrás: apartar la vieja, poner la nueva y solo
#    entonces borrar la vieja. Si el segundo paso falla, se restaura.
rm -rf "$OLD"
if ! mv "$TARGET" "$OLD" 2>/dev/null; then
  rm -rf "$STAGE"
  ulog "no se pudo apartar la app actual (¿permisos en $PARENT?)"
  exit 0
fi
if ! mv "$STAGE" "$TARGET" 2>/dev/null; then
  mv "$OLD" "$TARGET" 2>/dev/null
  rm -rf "$STAGE"
  ulog "intercambio fallido; restaurada la versión anterior"
  exit 0
fi
rm -rf "$OLD"
# Refresca Launch Services: sin esto el Finder sigue mostrando la versión
# vieja (misma ruta + mismo identificador = metadatos cacheados).
touch "$TARGET" 2>/dev/null
if /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$TARGET" >/dev/null 2>&1; then
  ulog "Launch Services re-registrado"
else
  ulog "aviso: lsregister falló"
fi
# Solo se marca y reabre si la copia instalada trae la versión esperada
# (si se ejecutaba desde el DMG u otro sitio no escribible, no se finge éxito).
INSTALLED="$(defaults read "$TARGET/Contents/Info.plist" CFBundleShortVersionString 2>/dev/null || echo ?)"
if [ "${REMOTE#v}" != "$INSTALLED" ]; then
  ulog "instalación no verificada (instalado=$INSTALLED remoto=$REMOTE)"
  exit 0
fi
ulog "instalada $REMOTE"
mark_skipped "$REMOTE"
# Detiene el servidor viejo para que la app nueva arranque limpia
if [ -f "$DATA/server.pid" ]; then stop_server "$DATA/server.pid"; fi
sleep 1
open "$TARGET"
exit 42
