"""Janela do Agent Hub: Edge/Chrome em modo app (sem barra de endereço, com o ícone do hub na barra de tarefas)."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

TITULO = "Agent Hub"
PASTA = Path(__file__).resolve().parent.parent
# Identidade do hub na barra de tarefas: sem ela a janela é do Edge, e fixar fixa o Edge.
APP_ID = "Herval.AgentHub"
COMANDO = f'"{PASTA / ".venv" / "Scripts" / "pythonw.exe"}" "{PASTA / "AgentHub.pyw"}"'
ICONE = f"{PASTA / 'static' / 'icone.ico'},0"


def _gravar_identidade(store) -> None:
    import pythoncom
    from win32com.propsys import propsys, pscon

    def texto(valor: str):
        return propsys.PROPVARIANTType(valor, pythoncom.VT_LPWSTR)

    store.SetValue(pscon.PKEY_AppUserModel_ID, texto(APP_ID))
    store.Commit()


def marcar_janela(hwnd: int) -> None:
    """Põe a janela no grupo do hub e diz ao Windows o que abrir (e com qual ícone) quando ela for fixada."""
    import pythoncom
    from win32com.propsys import propsys, pscon

    store = propsys.SHGetPropertyStoreForWindow(hwnd, propsys.IID_IPropertyStore)
    for chave, valor in ((pscon.PKEY_AppUserModel_RelaunchCommand, COMANDO),
                         (pscon.PKEY_AppUserModel_RelaunchDisplayNameResource, TITULO),
                         (pscon.PKEY_AppUserModel_RelaunchIconResource, ICONE)):
        store.SetValue(chave, propsys.PROPVARIANTType(valor, pythoncom.VT_LPWSTR))
    _gravar_identidade(store)  # o ID por último: o Windows lê os dados de relançamento quando ele muda


def marcar_atalho(lnk: Path) -> None:
    """Mesma identidade no atalho "Agent Hub.lnk", para o atalho fixado e a janela aberta serem um ícone só."""
    import pythoncom
    from win32com.propsys import propsys
    from win32com.shell import shell

    caminho = str(Path(lnk).resolve())
    link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink)
    arquivo = link.QueryInterface(pythoncom.IID_IPersistFile)
    arquivo.Load(caminho, 2)  # STGM_READWRITE
    _gravar_identidade(link.QueryInterface(propsys.IID_IPropertyStore))
    arquivo.Save(caminho, True)


def _janelas_do_hub() -> set[int]:
    """Janelas visíveis com o título do hub (no Windows)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    achadas: set[int] = set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visitar(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value == TITULO:
                achadas.add(hwnd)
        return True

    user32.EnumWindows(visitar, 0)
    return achadas


def _maximizar_nova(antes: set[int], prazo: float = 15) -> None:
    """Espera a janela nova do hub aparecer, maximiza e marca com a identidade do hub. (Com o Edge já aberto,
    --start-maximized é ignorado.)"""
    import ctypes

    fim = time.time() + prazo
    while time.time() < fim:
        novas = _janelas_do_hub() - antes
        if novas:
            for hwnd in novas:
                ctypes.windll.user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
            for hwnd in _janelas_do_hub():  # também as que já estavam abertas
                try:
                    marcar_janela(hwnd)
                except Exception:  # noqa: BLE001 - sem a marca a janela só fica no grupo do Edge
                    pass
            return
        time.sleep(0.2)


def abrir_janela(url: str, aba: bool = False) -> None:
    """Abre o hub numa janela própria e maximizada do Edge/Chrome. Sem Edge/Chrome (ou com aba=True), usa o
    navegador padrão."""
    if not aba:
        candidatos = [Path(os.environ.get(v, "")) / sub for v in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")
                      for sub in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe")]
        for exe in candidatos:
            if exe.is_file():
                antes = _janelas_do_hub() if sys.platform == "win32" else set()
                subprocess.Popen([str(exe), f"--app={url}", "--start-maximized"])
                if sys.platform == "win32":
                    try:
                        _maximizar_nova(antes)
                    except Exception:  # noqa: BLE001 - se não achar a janela, ela só não abre maximizada
                        pass
                return
    webbrowser.open(url)
