"""Ícone do Agent Hub na cor de destaque escolhida em Configurações → Geral (laranja, azul, verde, roxo, amarelo).

Os ícones de cada cor são gerados a partir do original (static/icone-256.png), trocando só o fundo laranja, e
ficam em data/icones/<cor>/ (pasta local, fora do Git). Ao trocar a cor, os atalhos do hub (pasta do hub, menu
Iniciar, barra de tarefas e área de trabalho) e as janelas abertas passam a usar o ícone novo.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PASTA_HUB = Path(__file__).resolve().parent.parent
ESTATICO = PASTA_HUB / "static"
PASTA_ICONES = PASTA_HUB / "data" / "icones"
CONFIG = PASTA_HUB / "data" / "hub_settings.json"
VERSAO = 1  # mude para regerar os ícones se o desenho ou as cores mudarem

# Fundo do ícone: meio-termo entre --accent e --accent-secondary do tema (o laranja original segue essa regra)
CORES = {
    "orange": None,  # arquivo original
    "blue": (0x1D, 0xA0, 0xDF),
    "green": (0x1C, 0xB4, 0x54),
    "purple": (0xA9, 0x5B, 0xF3),
    "yellow": (0xE2, 0xAB, 0x0C),
}
TAMANHOS_ICO = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def cor_valida(cor: str | None) -> str:
    return cor if cor in CORES else "orange"


def cor_atual() -> str:
    try:
        return cor_valida(json.loads(CONFIG.read_text(encoding="utf-8")).get("cor_icone"))
    except (OSError, ValueError):
        return "orange"


def _salvar_cor(cor: str) -> None:
    try:
        dados = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        dados = {}
    dados["cor_icone"] = cor
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


def arquivos(cor: str) -> dict[str, Path]:
    """Caminhos do ícone da cor (.ico, png 32 e png 256), gerando se ainda não existirem."""
    cor = cor_valida(cor)
    if CORES[cor] is None:
        return {"ico": ESTATICO / "icone.ico", "png32": ESTATICO / "icone-32.png", "png256": ESTATICO / "icone-256.png"}
    pasta = PASTA_ICONES / f"{cor}-v{VERSAO}"
    saida = {"ico": pasta / "icone.ico", "png32": pasta / "icone-32.png", "png256": pasta / "icone-256.png"}
    if not all(p.exists() for p in saida.values()):
        _gerar(CORES[cor], saida)
    return saida


def _gerar(fundo: tuple[int, int, int], saida: dict[str, Path]) -> None:
    """Troca o fundo laranja pela cor nova mantendo o desenho branco e as bordas suavizadas."""
    from PIL import Image

    original = Image.open(ESTATICO / "icone-256.png").convert("RGBA")
    base = original.getpixel((40, original.height // 2))[:3]  # um ponto do fundo laranja
    img = Image.new("RGBA", original.size)
    pixels = []
    for r, g, b, a in original.getdata():
        # quanto o pixel é "branco" (1) ou "fundo" (0): o verde separa bem o laranja do branco
        t = 0.0 if g <= base[1] else min(1.0, (g - base[1]) / (255 - base[1]))
        pixels.append(tuple(round(c * (1 - t) + 255 * t) for c in fundo) + (a,))
    img.putdata(pixels)
    saida["ico"].parent.mkdir(parents=True, exist_ok=True)
    img.save(saida["png256"])
    img.resize((32, 32), Image.LANCZOS).save(saida["png32"])
    img.save(saida["ico"], sizes=TAMANHOS_ICO)


# --- Windows: atalhos e janelas ------------------------------------------------------------------
def _atalhos_do_hub() -> list[Path]:
    """Atalhos .lnk que abrem este hub: pasta do hub, menu Iniciar, barra de tarefas e áreas de trabalho."""
    import win32com.client

    appdata = Path(os.environ.get("APPDATA", ""))
    pastas = [PASTA_HUB, appdata / r"Microsoft\Windows\Start Menu\Programs",
              appdata / r"Microsoft\Internet Explorer\Quick Launch\User Pinned"]
    try:
        from win32com.shell import shell, shellcon
        pastas.append(Path(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, None, 0)))
    except Exception:  # noqa: BLE001
        pass
    pastas += [Path.home() / "Desktop", Path(os.environ.get("PUBLIC", "")) / "Desktop"]
    ws = win32com.client.Dispatch("WScript.Shell")
    script = str(PASTA_HUB / "AgentHub.pyw").lower()
    achados: list[Path] = []
    for pasta in dict.fromkeys(pastas):
        if not pasta.is_dir():
            continue
        recursivo = "User Pinned" in str(pasta)  # a barra de tarefas guarda os fixados em subpastas
        for lnk in (pasta.rglob("*.lnk") if recursivo else pasta.glob("*.lnk")):
            try:
                if script in ws.CreateShortcut(str(lnk)).Arguments.lower():
                    achados.append(lnk)
            except Exception:  # noqa: BLE001 - atalho quebrado ou sem permissão: ignora
                continue
    return achados


def _trocar_icone_atalho(lnk: Path, ico: Path) -> bool:
    import win32com.client

    try:
        atalho = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(lnk))
        if atalho.IconLocation.lower() == f"{ico},0".lower():
            return False
        atalho.IconLocation = f"{ico},0"
        atalho.Save()
        return True
    except Exception:  # noqa: BLE001 - ex.: atalho da área de trabalho pública sem permissão
        return False


def _avisar_explorer(atalhos: list[Path]) -> None:
    """Faz o Explorer redesenhar os ícones (área de trabalho, Iniciar e barra de tarefas) sem reiniciar."""
    import ctypes

    shell32 = ctypes.windll.shell32
    SHCNE_UPDATEITEM, SHCNE_ASSOCCHANGED, SHCNF_PATHW, SHCNF_IDLIST = 0x2000, 0x08000000, 0x0005, 0x0000
    for lnk in atalhos:
        shell32.SHChangeNotify(SHCNE_UPDATEITEM, SHCNF_PATHW, ctypes.c_wchar_p(str(lnk)), None)
    shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)


def aplicar(cor: str) -> dict:
    """Usa o ícone da cor em todos os atalhos do hub e nas janelas abertas. Seguro chamar várias vezes."""
    cor = cor_valida(cor)
    ico = arquivos(cor)["ico"]
    mudou_cor = cor != cor_atual()
    if mudou_cor:
        _salvar_cor(cor)
    if sys.platform != "win32":
        return {"cor": cor, "atalhos": 0}
    import pythoncom

    pythoncom.CoInitialize()
    try:
        atalhos = _atalhos_do_hub()
        trocados = [lnk for lnk in atalhos if _trocar_icone_atalho(lnk, ico)]
        from .janela import remarcar_janelas
        remarcar_janelas()  # ícone usado quando a janela aberta é fixada
        if trocados:
            _avisar_explorer(trocados)
    finally:
        pythoncom.CoUninitialize()
    return {"cor": cor, "atalhos": len(atalhos), "trocados": len(trocados)}
