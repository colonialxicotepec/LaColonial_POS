@echo off
title Abrir puerto del historico en el firewall - La Colonial
color 0C
setlocal
set "PUERTO=%~1"
if "%PUERTO%"=="" set "PUERTO=8095"

echo ============================================================
echo   ABRIR EL PUERTO %PUERTO% EN EL FIREWALL DE WINDOWS
echo ============================================================
echo.
echo Permite que otros equipos de la MISMA red local consulten
echo el inventario historico (solo lectura).
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

netsh advfirewall firewall delete rule name="Historico La Colonial (%PUERTO%)" >nul 2>&1
netsh advfirewall firewall add rule name="Historico La Colonial (%PUERTO%)" dir=in action=allow protocol=TCP localport=%PUERTO% profile=private

echo.
echo Listo. Ahora:
echo   1) Arranca la consulta con:  historico_en_red.bat
echo   2) Desde el otro equipo entra a:
echo      http://IP-DE-ESTA-PC:%PUERTO%/
echo.
echo Si cambias el puerto en historico_config.json, vuelve a correr
echo este archivo indicando el puerto, por ejemplo:
echo   abrir_puerto_historico.bat 8100
echo.
pause >nul
