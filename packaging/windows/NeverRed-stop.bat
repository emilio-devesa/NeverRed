@echo off
rem NeverRed (Windows portable) — detiene el servidor si sigue en marcha.
rem En el modo .exe no hace falta: al cerrar la ventana, todo se apaga; y el
rem servidor se autoapaga solo 20 s después de cerrar la última pestaña.
powershell -NoProfile -Command ^
  "$d=Join-Path $env:APPDATA 'NeverRed'; $f=Join-Path $d 'server.pid';" ^
  "if (Test-Path $f) {$p=Get-Content $f | Select-Object -First 1;" ^
  "if ($p -match '^[0-9]+$') {" ^
  "  try {$proc=Get-Process -Id $p -ErrorAction Stop;" ^
  "      if ($proc.Name -match '^(py|python)') { Stop-Process -Id $p -Force } } catch {}" ^
  "} ; Remove-Item $f -ErrorAction SilentlyContinue;" ^
  "Write-Host 'Servidor de NeverRed detenido.'}" ^
  "else { Write-Host 'NeverRed no estaba en marcha (o ya se cerró).'}"
