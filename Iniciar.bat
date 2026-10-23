@echo off
rem Modo console (para ver mensagens do servidor). No dia a dia use o atalho "Agent Hub":
rem ele liga o servidor escondido e o desliga ao fechar a janela. Aqui: feche esta janela (ou Ctrl+C) para parar.
cd /d "%~dp0"
title Agent Hub
if not exist ".venv\Scripts\python.exe" (
  echo Criando ambiente Python...
  python -m venv .venv || goto :erro
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto :erro
)
if not exist "Agent Hub.lnk" ".venv\Scripts\python.exe" AgentHub.pyw --atalho
".venv\Scripts\python.exe" server.py %*
goto :eof
:erro
echo Falha ao preparar o ambiente. Verifique se o Python 3.11+ esta instalado.
pause
