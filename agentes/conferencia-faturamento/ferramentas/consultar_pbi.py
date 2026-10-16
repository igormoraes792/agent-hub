"""Consulta rápida ao Power BI (validações e investigação de poucos documentos). Mostra o resultado como tabela.

Uso:
  python ferramentas/consultar_pbi.py --dax "EVALUATE ROW(\"total\", [P1 Vlr Faturamento Líquido])"
  python ferramentas/consultar_pbi.py --arquivo resultados/x/investigar.dax [--max 50] [--csv saida.csv]

Usa a API REST com o login do Azure CLI (sem janela de login).
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # não deixar __pycache__ na pasta do agente
sys.path.insert(0, str(Path(__file__).parent))
import pbi_rest  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dax", help="consulta DAX (texto)")
    g.add_argument("--arquivo", help="arquivo .dax")
    ap.add_argument("--max", type=int, default=50, help="máximo de linhas mostradas (padrão 50)")
    ap.add_argument("--csv", help="também grava todas as linhas neste CSV")
    a = ap.parse_args()

    dax = a.dax if a.dax else Path(a.arquivo).read_text(encoding="utf-8")
    colunas, linhas = pbi_rest.executar(dax)
    if a.csv:
        Path(a.csv).parent.mkdir(parents=True, exist_ok=True)
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(colunas)
            w.writerows(linhas)
    print(f"{len(linhas)} linha(s)" + (f" (mostrando {a.max})" if len(linhas) > a.max else "") +
          (f" · gravado em {a.csv}" if a.csv else ""))
    if colunas:
        print("| " + " | ".join(colunas) + " |")
        print("|" + "---|" * len(colunas))
        for l in linhas[: a.max]:
            print("| " + " | ".join("" if v is None else str(v) for v in l) + " |")


if __name__ == "__main__":
    main()
