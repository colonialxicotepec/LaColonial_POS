@echo off
title Sincronizacion con 1box (LEGACY) - La Colonial
color 0C
echo ============================================================
echo   SINCRONIZACION CON 1box (SISTEMA LEGADO)
echo ============================================================
echo.
echo ATENCION: el inventario ya fue congelado y migrado al POS.
echo Esta sincronizacion esta BLOQUEADA a proposito; si se ejecuta
echo, dejaria el inventario del POS con los numeros de 1box.
echo.
echo Para reactivarla a proposito:
echo     python etl_pipeline.py --descongelar
echo.
cd /d "%~dp0"
set "PY=%~dp0..\..\runtime\python.exe"
if not exist "%PY%" set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" etl_pipeline.py %*
echo.
echo Presiona cualquier tecla para cerrar...
pause >nul
