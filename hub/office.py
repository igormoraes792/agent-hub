"""PowerPoint (.pptx) no Agent Hub: ler o conteúdo, exportar para PDF e gerar imagens dos slides.

Usado pelo servidor (anexos e botões dos arquivos no chat) e pela IA, direto no terminal:
    "<hub>\\.venv\\Scripts\\python.exe" "<hub>\\hub\\office.py" ler "C:\\...\\apresentacao.pptx"
    "<hub>\\.venv\\Scripts\\python.exe" "<hub>\\hub\\office.py" exportar "C:\\...\\apresentacao.pptx" --formato pdf|png

Exportação: PowerPoint instalado (fiel ao que o usuário vê); sem ele, LibreOffice (só PDF).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

PASTA_HUB = Path(__file__).resolve().parent.parent
PASTA_PREVIAS = PASTA_HUB / "data" / "previas"
SOFFICE = [Path(os.environ.get(v, "")) / "LibreOffice" / "program" / "soffice.exe"
           for v in ("ProgramFiles", "ProgramFiles(x86)")]
_TRAVA = threading.Lock()  # uma exportação por vez: o PowerPoint é uma instância só


# --- leitura --------------------------------------------------------------------------------------
def _texto(shape) -> str:
    if not shape.has_text_frame:
        return ""
    return "\n".join(p.text.strip() for p in shape.text_frame.paragraphs if p.text.strip())


def _tabela(shape) -> list[str]:
    linhas = []
    for i, row in enumerate(shape.table.rows):
        celulas = [c.text.replace("\n", " ").replace("|", "/").strip() for c in row.cells]
        linhas.append("| " + " | ".join(celulas) + " |")
        if i == 0:
            linhas.append("|" + "---|" * len(celulas))
    return linhas


def _formas(shapes, nivel=0):
    for shape in shapes:
        yield shape, nivel
        if shape.shape_type == 6:  # grupo
            yield from _formas(shape.shapes, nivel + 1)


def ler(caminho: str | Path) -> dict:
    """Conteúdo do pptx: tamanho do slide e, por slide, layout, título, textos, tabelas, gráficos, imagens e notas."""
    from pptx import Presentation
    from pptx.util import Emu

    prs = Presentation(str(caminho))
    slides = []
    for n, slide in enumerate(prs.slides, 1):
        item = {"numero": n, "layout": slide.slide_layout.name, "titulo": "", "textos": [], "tabelas": [],
                "graficos": [], "imagens": 0, "notas": ""}
        if slide.shapes.title is not None:
            item["titulo"] = _texto(slide.shapes.title)
        for shape, nivel in _formas(slide.shapes):
            if shape.has_text_frame and shape != slide.shapes.title:
                texto = _texto(shape)
                if texto:
                    item["textos"].append({"forma": shape.name, "texto": texto})
            if getattr(shape, "has_table", False) and shape.has_table:
                item["tabelas"].append({"forma": shape.name, "linhas": _tabela(shape)})
            if getattr(shape, "has_chart", False) and shape.has_chart:
                ch = shape.chart
                series = [s.name for p in ch.plots for s in p.series]
                item["graficos"].append({"forma": shape.name, "tipo": str(ch.chart_type).split(" ")[0], "series": series})
            if shape.shape_type == 13:  # imagem
                item["imagens"] += 1
        if not item["titulo"] and item["textos"]:  # slide sem campo de título (caixas de texto soltas)
            item["titulo"] = item["textos"][0]["texto"].split("\n")[0][:120]
        if slide.has_notes_slide:
            item["notas"] = slide.notes_slide.notes_text_frame.text.strip()
        slides.append(item)
    largura, altura = Emu(prs.slide_width), Emu(prs.slide_height)
    return {"arquivo": str(caminho), "slides": slides, "total_slides": len(slides),
            "tamanho_slide_cm": [round(largura.cm, 2), round(altura.cm, 2)],
            "layouts_disponiveis": [l.name for l in prs.slide_layouts]}


def para_markdown(info: dict) -> str:
    l = [f"# {Path(info['arquivo']).name}", "",
         f"{info['total_slides']} slides · slide de {info['tamanho_slide_cm'][0]} × {info['tamanho_slide_cm'][1]} cm",
         f"Layouts do modelo: {', '.join(info['layouts_disponiveis'])}", ""]
    for s in info["slides"]:
        l.append(f"## Slide {s['numero']}: {s['titulo'] or '(sem título)'}  [layout: {s['layout']}]")
        for t in s["textos"]:
            l.append(f"- ({t['forma']}) " + t["texto"].replace("\n", " / "))
        for t in s["tabelas"]:
            l.append(f"Tabela ({t['forma']}):")
            l += t["linhas"]
        for g in s["graficos"]:
            l.append(f"Gráfico ({g['forma']}): {g['tipo']}, séries: {', '.join(g['series']) or '-'}")
        if s["imagens"]:
            l.append(f"Imagens: {s['imagens']}")
        if s["notas"]:
            l.append("Notas: " + s["notas"].replace("\n", " / "))
        l.append("")
    return "\n".join(l)


# --- exportação -----------------------------------------------------------------------------------
def _destino_livre(caminho: Path) -> Path:
    """Não sobrescreve: apresentacao.pdf, apresentacao_2.pdf, ..."""
    if not caminho.exists():
        return caminho
    n = 2
    while (alvo := caminho.with_name(f"{caminho.stem}_{n}{caminho.suffix}")).exists():
        n += 1
    return alvo


def _pasta_previa(arquivo: Path) -> Path:
    chave = f"{arquivo.resolve()}|{arquivo.stat().st_mtime_ns}".encode("utf-8")
    return PASTA_PREVIAS / hashlib.sha1(chave).hexdigest()[:16]


def _limpar_previas(dias: int = 7) -> None:
    """As imagens são só para conferência: apaga as com mais de uma semana."""
    import time
    limite = time.time() - dias * 86400
    for pasta in PASTA_PREVIAS.glob("*"):
        try:
            if pasta.is_dir() and pasta.stat().st_mtime < limite:
                shutil.rmtree(pasta, ignore_errors=True)
        except OSError:
            pass


def _powerpoint(arquivo: Path, formato: str, destino: Path) -> list[Path]:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    app = pres = None
    try:
        app = win32com.client.Dispatch("PowerPoint.Application")
        pres = app.Presentations.Open(str(arquivo), -1, 0, 0)  # somente leitura, sem janela
        if formato == "pdf":
            pres.SaveAs(str(destino), 32)  # ppSaveAsPDF
            return [destino]
        destino.mkdir(parents=True, exist_ok=True)
        largura = 1280
        altura = round(largura * pres.PageSetup.SlideHeight / pres.PageSetup.SlideWidth)
        saidas = []
        for i in range(1, pres.Slides.Count + 1):
            png = destino / f"slide{i:02d}.png"
            pres.Slides(i).Export(str(png), "PNG", largura, altura)
            saidas.append(png)
        return saidas
    finally:
        if pres is not None:
            pres.Close()
        if app is not None and app.Presentations.Count == 0:
            app.Quit()  # só fecha o PowerPoint se o usuário não estiver com outra apresentação aberta
        pythoncom.CoUninitialize()


def _libreoffice_pdf(arquivo: Path, destino: Path) -> list[Path]:
    exe = next((p for p in SOFFICE if p.is_file()), None)
    if not exe:
        raise RuntimeError("nem PowerPoint nem LibreOffice disponíveis para exportar")
    with tempfile.TemporaryDirectory() as tmp:
        perfil = Path(tmp, "perfil").as_uri()  # perfil próprio: funciona mesmo com o LibreOffice aberto
        subprocess.run([str(exe), f"-env:UserInstallation={perfil}", "--headless", "--convert-to", "pdf",
                        "--outdir", tmp, str(arquivo)], check=True, capture_output=True, timeout=300)
        gerado = Path(tmp, arquivo.stem + ".pdf")
        if not gerado.is_file():
            raise RuntimeError("o LibreOffice não gerou o PDF")
        shutil.move(str(gerado), destino)
    return [destino]


def exportar(caminho: str | Path, formato: str) -> list[Path]:
    """PDF ao lado do arquivo (sem sobrescrever) ou PNG de cada slide em data/previas/ (reaproveita se já existir)."""
    with _TRAVA:
        return _exportar(Path(caminho).resolve(), formato)


def _exportar(arquivo: Path, formato: str) -> list[Path]:
    if arquivo.suffix.lower() != ".pptx" or not arquivo.is_file():
        raise ValueError(f"arquivo .pptx não encontrado: {arquivo}")
    if formato == "png":
        _limpar_previas()
        pasta = _pasta_previa(arquivo)
        prontos = sorted(pasta.glob("slide*.png"))
        if prontos:
            return prontos
        try:
            return _powerpoint(arquivo, "png", pasta)
        except ImportError:
            raise RuntimeError("as imagens dos slides precisam do PowerPoint instalado") from None
    if formato != "pdf":
        raise ValueError("formato deve ser pdf ou png")
    destino = _destino_livre(arquivo.with_suffix(".pdf"))
    try:
        return _powerpoint(arquivo, "pdf", destino)
    except Exception:  # noqa: BLE001 - sem PowerPoint (ou falhou): tenta o LibreOffice
        return _libreoffice_pdf(arquivo, destino)


def main() -> None:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="PowerPoint no Agent Hub")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ler", help="conteúdo do pptx em Markdown (ou --json)")
    p.add_argument("arquivo")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("exportar", help="PDF ao lado do arquivo ou PNG de cada slide (para conferir o visual)")
    p.add_argument("arquivo")
    p.add_argument("--formato", choices=("pdf", "png"), default="pdf")
    a = ap.parse_args()
    if a.cmd == "ler":
        info = ler(a.arquivo)
        print(json.dumps(info, ensure_ascii=False, indent=2) if a.json else para_markdown(info))
    else:
        for saida in exportar(a.arquivo, a.formato):
            print(saida)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
