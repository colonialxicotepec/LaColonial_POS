@echo off
title Abrir puerto de la caja en el firewall - POS La Colonial
color 0C
echo ============================================================
echo   ABRIR EL PUERTO 8090 EN EL FIREWALL DE WINDOWS
echo ============================================================
echo.
echo Permite que otro equipo de la MISMA red local entre a la caja.
echo Requiere permisos de administrador (click derecho - Ejecutar
echo como administrador).
echo.
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Este archivo necesita permisos de administrador.
  echo Cierra esta ventana, click derecho en el archivo y elige
  echo "Ejecutar como administrador".
  echo.
  pause >nul
  exit /b 1
)

netsh advfirewall firewall delete rule name="POS La Colonial (8090)" >nul 2>&1
netsh advfirewall firewall add rule name="POS La Colonial (8090)" dir=in action=allow protocol=TCP localport=8090 profile=private

echo.
echo Listo. Ahora:
echo   1) Arranca la caja con:  python app_pos.py --pos --red
echo      (o pon "allow_lan": true en pos_config.json)
echo   2) Desde el otro equipo entra a:
echo      http://IP-DE-ESTA-PC:8090/pos
echo.
pause >nul
