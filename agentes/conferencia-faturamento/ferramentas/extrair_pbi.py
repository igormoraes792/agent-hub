"""Roda uma consulta DAX no modelo publicado do Power BI e grava o resultado em CSV.

Uso:
  python ferramentas/extrair_pbi.py --dax consultas/minha.dax --saida resultados/x/pbi.csv

Usa a API REST do Power BI com o login do Azure CLI: não abre janela de login e não precisa do
arquivo aberto no Power BI Desktop. Imprime um resumo em JSON: linhas, colunas e soma das colunas numéricas.
Limite da API: 100 mil linhas e 1 milhão de valores por consulta (divida por período se passar).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # não deixar __pycache__ na pasta do agente
sys.path.insert(0, str(Path(__file__).parent))
import pbi_rest  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dax", required=True, help="arquivo .dax com a consulta (EVALUATE ...)")
    ap.add_argument("--saida", required=True, help="arquivo .csv de saída")
    ap.add_argument("--workspace", default=pbi_rest.WORKSPACE)
    ap.add_argument("--modelo", default=pbi_rest.MODELO)
    a = ap.parse_args()

    colunas, linhas = pbi_rest.executar(Path(a.dax).read_text(encoding="utf-8"), a.workspace, a.modelo)
    Path(a.saida).parent.mkdir(parents=True, exist_ok=True)
    with open(a.saida, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(colunas)
        w.writerows(linhas)

    somas = {}
    for i, c in enumerate(colunas):
        valores = [l[i] for l in linhas]
        if valores and all(isinstance(v, (int, float)) and not isinstance(v, bool) or v is None for v in valores):
            somas[c] = round(sum(v or 0 for v in valores), 2)
    perto_do_limite = len(linhas) >= 99_000 or len(linhas) * max(len(colunas), 1) >= 990_000
    print(json.dumps({"arquivo": a.saida, "linhas": len(linhas), "colunas": colunas, "somas": somas,
                      "perto_do_limite_da_api": perto_do_limite}, ensure_ascii=False, indent=2))
    if perto_do_limite:
        print("ATENÇÃO: resultado no limite da API (100 mil linhas / 1 milhão de valores). Divida a consulta por período.",
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
