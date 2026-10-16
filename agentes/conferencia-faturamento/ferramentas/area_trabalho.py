"""Área de trabalho temporária das conferências (fora das pastas do usuário).

As extrações e comparações precisam de arquivos intermediários (CSV por documento). Eles ficam em
%TEMP%\\agent-hub\\conferencias\\<data>_<rótulo> e são apagados automaticamente depois de 2 dias.
Relatórios e planilhas para o usuário só são gerados quando ele pedir.

Uso:
  python ferramentas/area_trabalho.py criar taqi_varejo_2026-09   -> imprime o caminho da pasta criada
  python ferramentas/area_trabalho.py limpar                      -> apaga pastas com mais de 2 dias
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(tempfile.gettempdir()) / "agent-hub" / "conferencias"
VALIDADE_DIAS = 2


def limpar() -> int:
    removidas = 0
    if not RAIZ.exists():
        return 0
    limite = time.time() - VALIDADE_DIAS * 86400
    for pasta in [p for p in RAIZ.iterdir() if p.is_dir()]:
        mais_recente = max((f.stat().st_mtime for f in pasta.rglob("*")), default=pasta.stat().st_mtime)
        if mais_recente < limite:
            shutil.rmtree(pasta, ignore_errors=True)
            removidas += 1
    return removidas


def criar(rotulo: str) -> Path:
    limpar()
    rotulo = re.sub(r"[^\w\-]+", "_", rotulo.strip().lower()).strip("_") or "conferencia"
    pasta = RAIZ / f"{time.strftime('%Y-%m-%d_%H%M%S')}_{rotulo}"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "criar":
        print(criar(sys.argv[2] if len(sys.argv) > 2 else "conferencia"))
    elif len(sys.argv) >= 2 and sys.argv[1] == "limpar":
        print(f"{limpar()} pasta(s) antiga(s) removida(s) de {RAIZ}")
    else:
        print(__doc__)
        sys.exit(1)
