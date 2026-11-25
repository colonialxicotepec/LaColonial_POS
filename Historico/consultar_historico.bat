@echo off
setlocal
cd /d "%~dp0"
title La Colonial - Historico 1box (solo consulta)

set "PY=..\runtime\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" consultar_historico.py %*
if errorlevel 1 pause
