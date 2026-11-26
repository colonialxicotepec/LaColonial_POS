@echo off
title Inicio automatico del Servidor - La Colonial
color 0A
echo ============================================================
echo   INICIO AUTOMATICO DEL SERVIDOR - LA COLONIAL
echo ============================================================
echo.
echo Este script registra caja_en_red.bat en el Inicio de Windows
echo para que al encender esta PC:
echo   1. Arranque el servidor POS en red local.
echo   2. Muestre en pantalla el nombre (%COMPUTERNAME%.local) y la IP.
echo   3. Abra automaticamente el navegador en el POS.
echo.
cd /d "%~dp0"

powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $startup = [Environment]::GetFolderPath('Startup'); $s = $ws.CreateShortcut((Join-Path $startup 'POS_Servidor_LaColonial.lnk')); $s.TargetPath = '%~dp0caja_en_red.bat'; $s.WorkingDirectory = '%~dp0'; $s.Description = 'Servidor POS La Colonial en Red Local'; $s.Save()"
if errorlevel 1 (
  echo Error al crear el acceso directo en Inicio de Windows.
  pause >nul
  exit /b 1
)

echo.
echo ============================================================
echo   LISTO! Al encender la PC se abrira automaticamente el POS.
echo ============================================================
echo.
pause
