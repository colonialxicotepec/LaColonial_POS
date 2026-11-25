@echo off
title Importar inventario al POS - La Colonial
color 0B
echo ============================================================
echo   IMPORTAR INVENTARIO (CATALOGO LIMPIO) AL POS
echo ============================================================
echo.
echo Copia el catalogo limpio (Historico\Auxiliares\inventario_moderno.db)
echo al inventario propio del POS. Conserva el stock local salvo que se
echo pida lo contrario con --reiniciar-stock.
echo.
echo Para el corte final se usa mejor cerrar_inventario.py
echo (hace ademas la conciliacion y sella el inventario).
echo.
cd /d "%~dp0"
set "PY=%~dp0..\..\runtime\python.exe"
if not exist "%PY%" set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" importar_inventario.py %*
echo.
echo Presiona cualquier tecla para cerrar...
pause >nul
