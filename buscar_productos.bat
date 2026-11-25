@echo off
title Buscador de Productos - La Colonial
color 0B
echo ============================================================
echo   BUSCADOR DE PRODUCTOS (La Colonial)
echo ============================================================
echo.
echo Se abrira el navegador automaticamente.
echo Deja esta ventana abierta mientras uses el buscador.
echo Para cerrar: cierra esta ventana.
echo.
cd /d "%~dp0"
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" app_pos.py
echo.
echo El buscador se detuvo. Presiona cualquier tecla para cerrar...
pause >nul
