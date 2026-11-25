@echo off
title Configurar PC de Cajero - La Colonial
color 0A
echo ============================================================
echo   CONFIGURACION UNICA - PC DE CAJERO (SIN .BAT DIARIO)
echo ============================================================
echo.
echo Este instalador hace 3 cosas una sola vez en esta PC:
echo   1. Guarda la IP de la Caja central.
echo   2. Deja el agente de impresora USB en inicio automatico
echo      de Windows (invisible, sin ventana negra).
echo   3. Crea el acceso directo "La Colonial POS" en el Escritorio.
echo.
cd /d "%~dp0"
set "PY=%~dp0runtime\python.exe"
set "PYW=%~dp0runtime\pythonw.exe"
if not exist "%PY%" set "PY=python"
if not exist "%PYW%" set "PYW=pythonw"

set /p "CAJA_IP=Escribe la IP de la Caja central (ej. 192.168.1.50): "
if "%CAJA_IP%"=="" (
  echo.
  echo Debes escribir la IP de la Caja central. Cancelado.
  pause >nul
  exit /b 1
)

"%PY%" -c "import json; ip='%CAJA_IP%'.strip().replace('http://','').split(':')[0]; json.dump({'server_url': f'http://{ip}:8090', 'printer_name': ''}, open('impresora_pc.json', 'w', encoding='utf-8'), indent=2); print('Configuracion guardada para http://' + ip + ':8090')"
if errorlevel 1 (
  echo Error guardando configuracion.
  pause >nul
  exit /b 1
)

echo.
echo Creando inicio automatico silencioso en Windows...
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $startup = [Environment]::GetFolderPath('Startup'); $s = $ws.CreateShortcut((Join-Path $startup 'POS_Impresora_Local.lnk')); $s.TargetPath = '%~dp0runtime\pythonw.exe'; $s.Arguments = '\"%~dp0impresora_local.py\"'; $s.WorkingDirectory = '%~dp0'; $s.WindowStyle = 7; $s.Description = 'Agente silencioso de impresora USB - La Colonial'; $s.Save(); $desk = [Environment]::GetFolderPath('Desktop'); $d = $ws.CreateShortcut((Join-Path $desk 'La Colonial POS.lnk')); $d.TargetPath = 'http://127.0.0.1:8099/pos'; $d.Description = 'Abrir Caja / Despacho La Colonial'; $d.Save()"
if errorlevel 1 (
  echo No se pudieron crear los accesos directos.
  pause >nul
  exit /b 1
)

echo.
echo Iniciando agente silencioso en segundo plano...
start "" "%PYW%" "%~dp0impresora_local.py"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8099/pos"

echo.
echo ============================================================
echo   LISTO! Esta PC ya quedo configurada.
echo   A partir de ahora el cajero solo abre "La Colonial POS"
echo   desde el Escritorio (no necesita ejecutar ningun .bat).
echo ============================================================
echo.
pause
