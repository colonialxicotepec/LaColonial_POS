@echo off
title Historico 1box en RED local - La Colonial
color 0A
echo ============================================================
echo   HISTORICO 1BOX ACCESIBLE DESDE LA RED LOCAL
echo ============================================================
echo.
echo Solo consulta: nadie puede modificar el inventario desde aqui.
echo Util para que los cajeros consulten productos, clientes y
echo proveedores del sistema viejo durante la migracion.
echo.
echo El historico se carga UNA SOLA VEZ en esta computadora y
echo queda disponible en la red local para todos los equipos.
echo.
echo   Desde otro equipo:  http://IP-DE-ESTA-PC:8095/
echo   (la IP y el puerto exactos aparecen en la ventana de abajo)
echo.
echo Si otro equipo no entra, ejecuta como administrador:
echo   abrir_puerto_historico.bat
echo.
echo Deja esta ventana abierta mientras se consulte.
echo.
cd /d "%~dp0"
set "PY=..\runtime\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" consultar_historico.py --red %*
if errorlevel 1 (
  echo.
  echo La consulta se detuvo con un error. Revisa el mensaje de arriba.
)
echo.
pause >nul
