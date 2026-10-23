"""Janela do Agent Hub: Edge/Chrome em modo app (sem barra de endereço, com o ícone do hub na barra de tarefas)."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

TITULO = "Agent Hub"


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
    """Espera a janela nova do hub aparecer e maximiza. (Com o Edge já aberto, --start-maximized é ignorado.)"""
    import ctypes

    fim = time.time() + prazo
    while time.time() < fim:
        novas = _janelas_do_hub() - antes
        if novas:
            for hwnd in novas:
                ctypes.windll.user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
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
