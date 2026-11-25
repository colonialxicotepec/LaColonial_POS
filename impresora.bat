@echo off
title Impresora local - La Colonial
color 0B
echo ============================================================
echo   IMPRESORA LOCAL DE ESTA COMPUTADORA
echo ============================================================
echo.
echo Este proceso conecta tu impresora USB con el punto de venta.
echo Deja esta ventana abierta mientras trabajas en esta PC.
echo.
cd /d "%~dp0"
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" set "PY=python"

if not exist "%~dp0impresora_pc.json" (
  echo Primera configuracion de esta PC de cajero:
  echo Escribe la IP de la Caja central ^(ej. 192.168.1.50^)
  echo o deja en blanco si solo quieres el agente de impresion.
  set /p "CAJA_IP=IP de la Caja central: "
)

if defined CAJA_IP (
  "%PY%" -c "import json; json.dump({'server_url': 'http://' + '%CAJA_IP%'.strip() + ':8090', 'printer_name': ''}, open('impresora_pc.json', 'w', encoding='utf-8'), indent=2)"
)

"%PY%" -c "import json, webbrowser, os; d=json.load(open('impresora_pc.json', encoding='utf-8')) if os.path.exists('impresora_pc.json') else {}; d.get('server_url') and webbrowser.open('http://127.0.0.1:8099/pos')"

"%PY%" impresora_local.py
if errorlevel 1 (
  echo.
  echo El agente de impresion se detuvo con un error. Revisa el mensaje de arriba.
)
echo.
echo Presiona cualquier tecla para cerrar...
pause >nul
