@echo off
title Verificar instalacion - POS La Colonial
color 0B
echo ============================================================
echo   VERIFICAR INSTALACION (POS La Colonial)
echo ============================================================
echo.
cd /d "%~dp0"
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" verificar_instalacion.py
echo.
echo Presiona cualquier tecla para cerrar...
pause >nul
