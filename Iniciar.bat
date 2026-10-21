@echo off
rem Inicia o Agent Hub e abre o navegador. Feche esta janela (ou Ctrl+C) para parar.
cd /d "%~dp0"
title Agent Hub
if not exist ".venv\Scripts\python.exe" (
  echo Criando ambiente Python...
  python -m venv .venv || goto :erro
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto :erro
)
".venv\Scripts\python.exe" server.py %*
goto :eof
:erro
echo Falha ao preparar o ambiente. Verifique se o Python 3.11+ esta instalado.
pause
