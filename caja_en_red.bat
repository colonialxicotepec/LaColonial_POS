@echo off
title Caja en RED local - La Colonial
color 0A
echo ============================================================
echo   CAJA (PUNTO DE VENTA) ACCESIBLE DESDE LA RED LOCAL
echo ============================================================
echo.
echo Esta caja ADMITIRA equipos de la misma red local.
echo Cada equipo necesita el PIN de un usuario para entrar.
echo.
echo   Desde otro equipo:  http://%COMPUTERNAME%.local:8090/pos
echo   (o usando la IP que aparece abajo)
echo.
echo Deja esta ventana abierta mientras cobras.
echo.
cd /d "%~dp0"
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" set "PY=python"

REM Impresora local de esta PC (los tickets de la caja salen aqui).
start "Impresora local" "%PY%" "%~dp0impresora_local.py"

"%PY%" app_pos.py --pos --red
if errorlevel 1 (
  echo.
  echo La caja se detuvo con un error. Revisa el mensaje de arriba.
)
echo.
echo Presiona cualquier tecla para cerrar...
pause >nul
