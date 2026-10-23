"""Abre o Agent Hub (é o que o atalho "Agent Hub" executa).

Liga o servidor escondido, sem janela de console, se ele ainda não estiver no ar, e abre a janela do hub.
O servidor se desliga sozinho quando a última janela fecha (depois de terminar o que estiver em andamento).
Erros do servidor ficam em data/servidor.log.
"""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PASTA = Path(__file__).resolve().parent
sys.path.insert(0, str(PASTA))
from hub.janela import abrir_janela  # noqa: E402

PORTA = int(os.environ.get("AGENT_HUB_PORT", "8787"))
URL = f"http://127.0.0.1:{PORTA}"


def no_ar() -> bool:
    try:
        with urllib.request.urlopen(f"{URL}/static/icone-32.png", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def ligar_servidor() -> None:
    python = PASTA / ".venv" / "Scripts" / "python.exe"
    (PASTA / "data").mkdir(exist_ok=True)
    arq_log = PASTA / "data" / "servidor.log"
    if arq_log.exists() and arq_log.stat().st_size > 1_000_000:  # guarda só o histórico recente
        arq_log.replace(arq_log.with_suffix(".log.old"))
    log = open(arq_log, "a", encoding="utf-8")
    log.write(f"\n=== {time.strftime('%d/%m/%Y %H:%M:%S')} servidor iniciado pelo atalho ===\n")
    log.flush()
    # Console próprio porém escondido: os programas que o servidor abre (claude, codex, MCPs) usam esse
    # console e também não aparecem na tela.
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    subprocess.Popen([str(python), "-u", "server.py", "--no-browser", "--sozinho"], cwd=PASTA,
                     stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                     startupinfo=si, creationflags=subprocess.CREATE_NEW_CONSOLE)


def criar_atalho() -> None:
    """Cria o atalho "Agent Hub" (com o ícone do hub) nesta pasta. O Iniciar.bat chama quando ele não existe."""
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:AH_LNK);"
        "$s.TargetPath = $env:AH_PYW; $s.Arguments = '\"' + $env:AH_SCRIPT + '\"';"
        "$s.WorkingDirectory = $env:AH_PASTA; $s.IconLocation = $env:AH_ICO + ',0';"
        "$s.Description = 'Abre o Agent Hub'; $s.Save()"
    )
    env = {**os.environ, "AH_LNK": str(PASTA / "Agent Hub.lnk"), "AH_PYW": str(PASTA / ".venv" / "Scripts" / "pythonw.exe"),
           "AH_SCRIPT": str(PASTA / "AgentHub.pyw"), "AH_PASTA": str(PASTA), "AH_ICO": str(PASTA / "static" / "icone.ico")}
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, check=True)


def main() -> None:
    if "--atalho" in sys.argv:
        criar_atalho()
        return
    if not no_ar():
        ligar_servidor()
        for _ in range(120):
            time.sleep(0.25)
            if no_ar():
                break
        else:
            os.startfile(PASTA / "data" / "servidor.log")  # não subiu: mostra o motivo
            return
    abrir_janela(URL)


if __name__ == "__main__":
    main()
