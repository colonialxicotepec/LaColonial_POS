@echo off
title Instalar Python portable - POS La Colonial
color 0B
echo ============================================================
echo   INSTALAR PYTHON PORTABLE (sin instalar nada en Windows)
echo ============================================================
echo.
echo Deja un Python 3.12 dentro de la carpeta del proyecto (runtime\).
echo No modifica el sistema, no pide permisos de administrador y no
echo toca el PATH de Windows.
echo.
cd /d "%~dp0"

if exist "runtime\python.exe" (
  echo El runtime ya existe. No se hace nada.
  goto :fin
)

set "ZIP="
for %%f in ("vendor\python-3.12.*-embed-amd64.zip") do set "ZIP=%%f"
if not defined ZIP (
  echo ERROR: no se encontro el paquete portable en vendor\
  echo Descargalo de https://www.python.org/ftp/python/3.12.10/
  echo y dejalo en la carpeta vendor\.
  goto :error
)

echo Extrayendo %ZIP% ...
powershell -NoProfile -Command "Expand-Archive -Path '%ZIP%' -DestinationPath 'runtime' -Force"
if errorlevel 1 goto :error

echo Configurando rutas del runtime ...
(
  echo python312.zip
  echo .
  echo ..
  echo import site
  echo.
  echo ^# La carpeta ".." es la raiz del proyecto: permite importar pos_core, etc.
) > "runtime\python312._pth"

echo.
echo Probando el runtime ...
runtime\python.exe -c "import sqlite3, http.server, ctypes; print('Python portable OK')"
if errorlevel 1 goto :error

echo.
echo Listo. La caja usara runtime\python.exe automaticamente.
goto :fin

:error
echo.
echo No se pudo completar la instalacion del runtime.
pause >nul
exit /b 1

:fin
pause >nul
