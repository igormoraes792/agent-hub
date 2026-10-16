"""Roda uma consulta SQL no Superset (SQL Lab / Dremio) e grava TODAS as linhas em CSV.

O SQL Lab devolve no máximo 1000 linhas por consulta; este script pagina pela coluna-chave
(ex.: co_id_order) até trazer tudo. A consulta precisa ter UMA linha por chave (use GROUP BY na chave)
e não pode ter ORDER BY nem LIMIT (o script acrescenta).

Uso:
  python ferramentas/extrair_superset.py --sql consultas/minha.sql --chave co_id_order --saida resultados/x/superset.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # não deixar __pycache__ na pasta do agente
sys.path.insert(0, str(Path(__file__).parent))
from mcp_cliente import ClienteMCP  # noqa: E402

PAGINA = 1000


def executar(ss: ClienteMCP, banco: int, sql: str) -> list[dict]:
    texto = ss.chamar("superset_sqllab_execute", {"database_id": banco, "sql": sql}, 900)
    externo = json.loads(texto)
    interno = json.loads(externo["result"]) if isinstance(externo.get("result"), str) else externo
    if interno.get("status") not in (None, "success"):
        raise RuntimeError(f"Consulta falhou: {json.dumps(interno, ensure_ascii=False)[:1500]}")
    return interno.get("data") or []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sql", required=True, help="arquivo .sql (uma linha por chave, sem ORDER BY/LIMIT)")
    ap.add_argument("--chave", required=True, help="coluna-chave do resultado (ex.: co_id_order)")
    ap.add_argument("--saida", required=True, help="arquivo .csv de saída")
    ap.add_argument("--banco", type=int, default=3, help="database_id no Superset (3 = Dremio)")
    a = ap.parse_args()

    base = Path(a.sql).read_text(encoding="utf-8").strip().rstrip(";")
    k = f'pag."{a.chave}"'
    linhas: list[dict] = []
    with ClienteMCP("superset-herval") as ss:
        ultimo, paginas = "", 0
        while True:
            lit = ultimo.replace("'", "''")
            pagina = executar(ss, a.banco, f"SELECT * FROM (\n{base}\n) pag WHERE {k} > '{lit}' ORDER BY {k} LIMIT {PAGINA}")
            linhas.extend(pagina)
            paginas += 1
            print(f"  página {paginas}: {len(pagina)} linhas (total {len(linhas)})", file=sys.stderr)
            if len(pagina) < PAGINA:
                break
            ultimo = str(pagina[-1][a.chave])
            if paginas > 500:
                raise SystemExit("Mais de 500 páginas: restrinja a consulta.")
        sem_chave = executar(ss, a.banco, f"SELECT * FROM (\n{base}\n) pag WHERE {k} IS NULL OR {k} = '' LIMIT {PAGINA}")
        linhas.extend(sem_chave)

    colunas = list(linhas[0].keys()) if linhas else []
    Path(a.saida).parent.mkdir(parents=True, exist_ok=True)
    with open(a.saida, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=colunas)
        w.writeheader()
        w.writerows(linhas)

    chaves = [str(l.get(a.chave)) for l in linhas]
    somas = {c: round(sum(l.get(c) or 0 for l in linhas), 2) for c in colunas
             if linhas and all(isinstance(l.get(c), (int, float)) or l.get(c) is None for l in linhas)}
    resumo = {"arquivo": a.saida, "linhas": len(linhas), "colunas": colunas, "somas": somas,
              "linhas_sem_chave": len(sem_chave), "chaves_repetidas": len(chaves) - len(set(chaves))}
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    if resumo["chaves_repetidas"]:
        print("ATENÇÃO: há chaves repetidas; a consulta deveria ter GROUP BY na chave.", file=sys.stderr)


if __name__ == "__main__":
    main()
