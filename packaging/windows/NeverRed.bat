@echo off
rem NeverRed (Windows portable) — arranca el launcher con un Python sin consola.
rem Requiere Python 3.12+ instalado; el .exe de PyInstaller no lo necesita.
cd /d "%~dp0"
where pyw >nul 2>nul
if not errorlevel 1 (
  start "NeverRed" pyw packaging\windows\launcher.py
  exit /b 0
)
where pythonw >nul 2>nul
if not errorlevel 1 (
  start "NeverRed" pythonw packaging\windows\launcher.py
  exit /b 0
)
rem Último recurso: consola visible del launcher.
start "NeverRed" py packaging\windows\launcher.py
