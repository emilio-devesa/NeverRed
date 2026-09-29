#!/bin/sh
# Construye NeverRed-<versión>-x86_64.AppImage (en CI sobre Ubuntu).
# Uso: ./packaging/linux/build.sh [versión]   (por defecto: último tag sin la v)
set -eu
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [ "${1:-}" != "" ]; then VERSION="$1";
elif git describe --tags --abbrev=0 >/dev/null 2>&1; then VERSION="$(git describe --tags --abbrev=0 | sed 's/^v//')";
else VERSION="1.7.0"; fi

DIR="$ROOT/dist/linux/NeverRed.AppDir"
rm -rf "$DIR"
mkdir -p "$DIR/app/backend" "$DIR/app/lib"
cp index.html styles.css app.js icon.svg manifest.webmanifest sw.js "$DIR/app/"
cp -R assets "$DIR/app/assets"
cp lib/contabilidad.js "$DIR/app/lib/"
cp backend/server.py backend/telemetry_common.py backend/telemetry_forward.py "$DIR/app/backend/"
cp packaging/linux/AppRun packaging/linux/check-update.sh "$DIR/"
cp packaging/update-dialog.py "$DIR/"
cp packaging/linux/NeverRed.desktop "$DIR/"
# Icono PNG para el lanzador (rsvg-convert; si no está, el SVG como respaldo)
if command -v rsvg-convert >/dev/null 2>&1; then
  rsvg-convert -w 256 -h 256 icon.svg -o "$DIR/neverred.png"
  cp "$DIR/neverred.png" "$DIR/.DirIcon"
else
  cp icon.svg "$DIR/.DirIcon"
fi
cp icon.svg "$DIR/neverred.svg"
echo "$VERSION" > "$DIR/VERSION"
chmod +x "$DIR/AppRun" "$DIR/check-update.sh"

TOOL="${APPIMAGETOOL:-./appimagetool}"
if [ ! -x "$TOOL" ]; then
  echo "Descargando appimagetool…"
  curl -fsSL -o ./appimagetool \
    https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
  chmod +x ./appimagetool
fi
mkdir -p "$ROOT/dist"
ARCH=x86_64 "$TOOL" "$DIR" "$ROOT/dist/NeverRed-$VERSION-x86_64.AppImage"
echo "OK: dist/NeverRed-$VERSION-x86_64.AppImage"
