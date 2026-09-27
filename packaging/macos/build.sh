#!/bin/sh
# Construye NeverRed.app + NeverRed-macOS-<versión>.dmg solo con herramientas de macOS.
# Uso: ./packaging/macos/build.sh [versión] [sufijo]   (p. ej. 1.2.0 -arm64)
# Por defecto: último tag git sin la v, sin sufijo.
set -eu
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [ "${1:-}" != "" ]; then VERSION="$1";
elif git describe --tags --abbrev=0 >/dev/null 2>&1; then VERSION="$(git describe --tags --abbrev=0 | sed 's/^v//')";
else VERSION="1.2.0"; fi
SUFFIX="${2:-}"
DMG="NeverRed-macOS-$VERSION$SUFFIX.dmg"

STAGE="$ROOT/dist/macos"
APP="$STAGE/NeverRed.app"
RES="$APP/Contents/Resources/app"
rm -rf "$STAGE"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$RES/backend" "$RES/lib"

# Ficheros de la app (solo lo necesario para correr)
cp index.html styles.css app.js icon.svg manifest.webmanifest sw.js "$RES/"
cp -R assets "$RES/assets"
cp lib/contabilidad.js "$RES/lib/"
cp backend/server.py "$RES/backend/"
cp packaging/macos/Launcher.sh "$APP/Contents/MacOS/NeverRed"
cp packaging/macos/check-update.sh "$APP/Contents/MacOS/check-update.sh"
cp packaging/update-dialog.py "$APP/Contents/MacOS/update-dialog.py"
chmod +x "$APP/Contents/MacOS/NeverRed" "$APP/Contents/MacOS/check-update.sh"

# Icono .icns a partir del SVG (QuickLook + iconutil, todo de serie)
ICONSET="$STAGE/AppIcon.iconset"
mkdir -p "$STAGE/ql"
if qlmanage -t -s 1024 -o "$STAGE/ql" icon.svg >/dev/null 2>&1; then
  SRC="$(ls "$STAGE"/ql/*.png | head -1)"
  mkdir -p "$ICONSET"
  sips -z 16 16     "$SRC" --out "$ICONSET/icon_16x16.png" >/dev/null
  sips -z 32 32     "$SRC" --out "$ICONSET/icon_16x16@2x.png" >/dev/null
  sips -z 32 32     "$SRC" --out "$ICONSET/icon_32x32.png" >/dev/null
  sips -z 64 64     "$SRC" --out "$ICONSET/icon_32x32@2x.png" >/dev/null
  sips -z 128 128   "$SRC" --out "$ICONSET/icon_128x128.png" >/dev/null
  sips -z 256 256   "$SRC" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
  sips -z 256 256   "$SRC" --out "$ICONSET/icon_256x256.png" >/dev/null
  sips -z 512 512   "$SRC" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
  sips -z 512 512   "$SRC" --out "$ICONSET/icon_512x512.png" >/dev/null
  sips -z 1024 1024 "$SRC" --out "$ICONSET/icon_512x512@2x.png" >/dev/null
  iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
  rm -rf "$STAGE/ql" "$ICONSET"
  ICONKEY="  <key>CFBundleIconFile</key><string>AppIcon</string>"
else
  echo "Aviso: no se pudo generar el icono, se sigue sin él."
  ICONKEY=""
fi

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>NeverRed</string>
  <key>CFBundleIdentifier</key><string>com.neverred.app</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleExecutable</key><string>NeverRed</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
$ICONKEY
</dict></plist>
PLIST

# DMG arrastrable (app + enlace a Aplicaciones)
ln -s /Applications "$STAGE/Aplicaciones"
mkdir -p "$ROOT/dist"
hdiutil create -volname NeverRed -srcfolder "$STAGE" -ov -format UDZO \
  "$ROOT/dist/$DMG" >/dev/null
rm "$STAGE/Aplicaciones"
echo "OK: dist/$DMG"
