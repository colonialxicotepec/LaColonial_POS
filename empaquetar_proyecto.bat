@echo off
title Empaquetar proyecto - POS La Colonial
color 0B
echo ============================================================
echo   EMPAQUETAR PROYECTO PARA OTRA COMPUTADORA
echo ============================================================
echo.
echo Genera un ZIP en la carpeta dist\ con el runtime portable,
echo el codigo, la base del POS y la documentacion.
echo.
cd /d "%~dp0"
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" empaquetar_proyecto.py %*
echo.
echo Presiona cualquier tecla para cerrar...
pause >nul
