@echo off
title Caja - La Colonial
color 0A
echo ============================================================
echo   CAJA (PUNTO DE VENTA) - LA COLONIAL
echo ============================================================
echo.
echo Se abrira el navegador en la caja.
echo Deja esta ventana abierta mientras cobras.
echo Para cerrar: cierra esta ventana.
echo.
cd /d "%~dp0"
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" set "PY=python"

REM Impresora local de esta PC (los tickets salen en la impresora de aqui).
start "Impresora local" "%PY%" "%~dp0impresora_local.py"

"%PY%" app_pos.py --pos
if errorlevel 1 (
  echo.
  echo La caja se detuvo con un error. Revisa el mensaje de arriba.
  echo Si dice que falta Python, ejecuta instalar_python_portable.bat
)
echo.
echo Presiona cualquier tecla para cerrar...
pause >nul
