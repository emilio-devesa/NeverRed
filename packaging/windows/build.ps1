# NeverRed — construcción de la versión de Windows (PyInstaller + ZIP portable).
# Ejecutar en Windows 10/11 con Python 3.12+ en el PATH:
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 [-Version 2.6.8]
# Produce en dist/windows/:
#   NeverRed-windows-<v>.zip            — NeverRed.exe + NeverRed-Update.exe + app
#   NeverRed-windows-<v>-portable.zip   — arranque con el Python instalado
# Tras publicar, firmar cada ZIP y subir sus .sig (docs/ops/releases.md).
param(
    [string]$Version = "",
    # Destino de telemetría por defecto del build (vacío = telemetría local, sin
    # salida). Para builds coordinados: NEVERRED_BUILD_SINK / NEVERRED_BUILD_TOKEN
    # por entorno, igual que en los builds de macOS y Linux.
    [string]$SinkUrl = "",
    [string]$SinkToken = "",
    # Omite los .exe (solo el ZIP portable).
    [switch]$SkipExe = $false
)

# ── Cuerpo ────────────────────────────────────────────────────────────────
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Push-Location $Root
try {
    if (-not $Version) {
        $Version = (& git describe --tags --abbrev=0 2>$null) -replace '^v', ''
    }
    if (-not $Version) { $Version = "2.6.8" }
    Write-Host "== NeverRed Windows $Version =="

    $Dist     = Join-Path $Root "dist\windows"
    $Stage    = Join-Path $Dist "stage"
    $ExeDir   = Join-Path $Dist "NeverRed"      # raíz del paquete .exe
    $Portable = Join-Path $Dist "portable"      # raíz del ZIP portable
    $Work     = Join-Path $Dist "build"
    Remove-Item -Recurse -Force $Stage, $ExeDir, $Portable, $Work -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $Stage, $ExeDir, $Portable, $Work | Out-Null

    # Copia del launcher al stage para sustituir marcadores
    # (__NEVERRED_BUILD_SINK__/__NEVERRED_BUILD_TOKEN__); el source del repo queda intacto.
    Copy-Item (Join-Path $PSScriptRoot "launcher.py") (Join-Path $Stage "launcher.py")
    if (-not $SkipExe) {
        Copy-Item (Join-Path $PSScriptRoot "update_run.py") $Stage
    }
    if ($SinkUrl -or $SinkToken) {
        $p = Join-Path $Stage "launcher.py"
        $c = Get-Content $p -Raw
        $c = $c.Replace("__NEVERRED_BUILD_SINK__", $SinkUrl)
        $c = $c.Replace("__NEVERRED_BUILD_TOKEN__", $SinkToken)
        Set-Content -Path $p -Value $c -Encoding UTF8
    }

    # ── Ficheros de la app (misma estructura que los builds de macOS/Linux) ──
    $AppFiles = @("index.html","styles.css","app.js","icon.svg","manifest.webmanifest","sw.js")
    foreach ($f in $AppFiles) { Copy-Item (Join-Path $Root $f) $ExeDir }
    Copy-Item (Join-Path $Root "assets") $ExeDir -Recurse
    New-Item -ItemType Directory -Force (Join-Path $ExeDir "lib") | Out-Null
    Copy-Item (Join-Path $Root "lib\contabilidad.js") (Join-Path $ExeDir "lib")
    # El directorio backend hay que crearlo: Copy-Item con destino inexistente
    # escribiría los módulos como UN FICHERO llamado "backend" (tipo file, no
    # dir) y el exe leería versión 0.0.0. (Pillado probando en Windows real.)
    New-Item -ItemType Directory -Force (Join-Path $ExeDir "backend") | Out-Null
    $BackMods = "server.py","auth.py","config.py","market.py","schema.py","static.py","telemetry.py","telemetry_common.py","telemetry_forward.py"
    foreach ($m in $BackMods) { Copy-Item (Join-Path $Root ("backend\" + $m)) (Join-Path $ExeDir "backend") }
    # Bundle de CAs para el actualizador: en Windows sin roots TLS modernos
    # (p. ej. XP) verifica contra este fichero junto al exe; en 10/11 el
    # almacén del sistema basta pero tenerlo no molesta (firma igual de válida).
    Copy-Item (Join-Path $PSScriptRoot "cacert.pem") $ExeDir

    # ── ZIP portable: mismos ficheros, sin los .exe, con scripts y .bat en la raíz ──
    Copy-Item -Path "$ExeDir\*" -Destination $Portable -Recurse -Force
    Copy-Item (Join-Path $Stage "launcher.py") $Portable
    Copy-Item (Join-Path $PSScriptRoot "NeverRed.bat") $Portable
    Copy-Item (Join-Path $PSScriptRoot "NeverRed-stop.bat") $Portable
    New-Item -ItemType Directory -Force (Join-Path $Portable "packaging\windows") | Out-Null
    if (Test-Path (Join-Path $Stage "update_run.py")) {
        Copy-Item (Join-Path $Stage "update_run.py") (Join-Path $Portable "packaging\windows\update_run.py") -Force
    }
    Copy-Item (Join-Path $Stage "launcher.py") (Join-Path $Portable "packaging\windows\launcher.py")
    $PortTarget = Join-Path $Dist "NeverRed-windows-$Version-portable.zip"
    if (Test-Path $PortTarget) { Remove-Item $PortTarget }
    Compress-Archive -Path "$Portable\*" -DestinationPath $PortTarget

    # ── .exe: PyInstaller ─────────────────────────────────────────────────
    if (-not $SkipExe) {
        & python -m pip show pyinstaller *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "PyInstaller no está instalado: pip install pyinstaller"
            throw "pip install pyinstaller"
        }
        $iconFlag = @()
        if (Test-Path (Join-Path $Root "assets\icon.ico")) { $iconFlag = @("--icon", (Join-Path $Root "assets\icon.ico")) }
        & python -m PyInstaller --noconfirm --clean --onefile --noconsole `
            @iconFlag --name NeverRed `
            --distpath $ExeDir --workpath (Join-Path $Work "main") --specpath $Stage `
            (Join-Path $Stage "launcher.py")
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló en el paquete principal" }
        & python -m PyInstaller --noconfirm --clean --onefile --noconsole `
            --collect-submodules tkinter `
            @iconFlag --name NeverRed-Update `
            --distpath $ExeDir --workpath (Join-Path $Work "upd") --specpath $Stage `
            (Join-Path $Stage "update_run.py")
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló en el actualizador" }
        # Chequeo de versión del .exe (debe leer backend/config.py de su lado).
        # Con --noconsole el exe es GUI-subsystem y PowerShell no espera a los
        # exes GUI: Start-Process -Wait + redirección a fichero garantiza
        # capturar la salida y el código de salida.
        $verFile = Join-Path $Stage "ver.txt"
        $proc = Start-Process -FilePath (Join-Path $ExeDir "NeverRed.exe") `
            -ArgumentList "--version" -Wait -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput $verFile -RedirectStandardError (Join-Path $Stage "ver.err")
        $exeVer = (Get-Content $verFile -Raw -ErrorAction SilentlyContinue)
        if ($null -ne $exeVer) { $exeVer = $exeVer.Trim() }
        if ($proc.ExitCode -ne 0 -or -not $exeVer) { throw "El .exe no responde --version" }
        Write-Host "NeverRed.exe --version -> $exeVer"
    }

    # ── Instalador (Inno Setup 6) ────────────────────────────────────────
    # Requiere Inno Setup 6 (winget: JRSoftware.InnoSetup); lo empaqueta en
    # un solo setup.exe instalable por el usuario final (per-usuario en
    # %LOCALAPPDATA%). Si no está instalado, se avisa y se sigue con el resto
    # de entregables (los zips ya estarán listos).
    if (-not $SkipExe) {
        $IssPath = Join-Path $PSScriptRoot "installer.iss"
        if (Test-Path $IssPath) {
            $IsccCandidates = @(
                "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                "C:\Program Files\Inno Setup 6\ISCC.exe",
                (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
            )
            $Iscc = $IsccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
            if ($Iscc) {
                & $Iscc "/DAppVersion=$Version" "/DPayload=$ExeDir" $IssPath
                if ($LASTEXITCODE -ne 0) { throw "ISCC falló compilando installer.iss" }
                # Publicar el setup junto a los zips (CI: files: dist/windows/*.exe)
                Copy-Item (Join-Path (Split-Path $IssPath) ("Output\NeverRed-setup-" + $Version + ".exe")) $Dist
                Write-Host "OK: instalador en dist\windows\NeverRed-setup-$Version.exe"
            } else {
                Write-Warning "Inno Setup 6 no encontrado: sin instalador. Instálalo con: winget install --id JRSoftware.InnoSetup -e"
            }
        }
    }

    # ── Empaquetado final ────────────────────────────────────────────────
    if (-not $SkipExe) {
        $ExeTarget = Join-Path $Dist "NeverRed-windows-$Version.zip"
        if (Test-Path $ExeTarget) { Remove-Item $ExeTarget }
        Compress-Archive -Path "$ExeDir\*" -DestinationPath $ExeTarget
        Write-Host "OK: NeverRed-windows-$Version.zip"
    }
    Write-Host "OK: $PortTarget"
} finally {
    Pop-Location
}
