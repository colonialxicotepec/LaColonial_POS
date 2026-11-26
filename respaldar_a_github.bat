@echo off
REM ============================================================
REM  Respaldo del POS La Colonial a GitHub (repositorio privado)
REM  Genera un snapshot de los datos reales y lo sube.
REM ============================================================
setlocal
cd /d "%~dp0"

if exist "runtime\python.exe" (
    set "PY=runtime\python.exe"
) else (
    set "PY=python"
)

echo.
echo  Respaldando el POS a GitHub...
echo.
"%PY%" respaldo_github.py %*
set "CODE=%ERRORLEVEL%"

echo.
if "%CODE%"=="0" (
    echo  Respaldo terminado correctamente.
) else (
    echo  El respaldo termino con errores. Revisa el mensaje de arriba.
)
echo.
pause
endlocal
