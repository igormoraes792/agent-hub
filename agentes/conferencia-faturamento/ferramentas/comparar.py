"""Compara, documento a documento, a extração do Power BI com a do Superset.

Uso:
  python ferramentas/comparar.py --pbi resultados/x/pbi.csv --superset resultados/x/superset.csv \
      --chave-pbi NU_DOCUMENTO --chave-ss co_id_order --valor-pbi valor --valor-ss valor \
      [--data-pbi dt --data-ss dt_min] [--tolerancia 0.05] --saida resultados/x

Gera na pasta de saída:
  resumo.md          resumo legível (totais, categorias, maiores diferenças, diferença por dia)
  resumo.json        o mesmo resumo em JSON
  divergencias.csv   todos os documentos com diferença (abre direto no Excel: separador ';' e vírgula decimal)
  por_dia.csv        diferença por dia, se as colunas de data forem informadas
Categorias: "Igual", "Valor diferente", "Só no Power BI", "Só no Superset".
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path


def chave_norm(v) -> str:
    s = str(v if v is not None else "").strip()
    s = re.sub(r"\.0+$", "", s)  # 8700221.0 -> 8700221
    return s.lstrip("0") or s


def num(v) -> float:
    if v in (None, ""):
        return 0.0
    try:
        return float(v)
    except ValueError:
        return float(str(v).replace(".", "").replace(",", "."))


def data_norm(v) -> str:
    s = str(v or "").strip()
    if re.fullmatch(r"\d{8}(\.0+)?", s):
        s = s[:8]
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


def br(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def ler(caminho: str, chave: str, valor: str, data: str | None):
    por_doc: dict[str, dict] = {}
    with open(caminho, encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            k = chave_norm(linha.get(chave))
            d = por_doc.setdefault(k, {"valor": 0.0, "datas": set(), "attrs": {}, "chave_original": linha.get(chave)})
            d["valor"] += num(linha.get(valor))
            if data:
                d["datas"].add(data_norm(linha.get(data)))
            for c, v in linha.items():
                if c not in (chave, valor) and v not in (None, "") and c not in d["attrs"]:
                    d["attrs"][c] = v
    return por_doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pbi", required=True)
    ap.add_argument("--superset", required=True)
    ap.add_argument("--chave-pbi", required=True)
    ap.add_argument("--chave-ss", required=True)
    ap.add_argument("--valor-pbi", required=True)
    ap.add_argument("--valor-ss", required=True)
    ap.add_argument("--data-pbi")
    ap.add_argument("--data-ss")
    ap.add_argument("--tolerancia", type=float, default=0.05, help="diferença máxima (R$) para considerar igual")
    ap.add_argument("--saida", required=True)
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()

    pbi = ler(a.pbi, a.chave_pbi, a.valor_pbi, a.data_pbi)
    ss = ler(a.superset, a.chave_ss, a.valor_ss, a.data_ss)
    saida = Path(a.saida)
    saida.mkdir(parents=True, exist_ok=True)

    cats = defaultdict(lambda: {"docs": 0, "pbi": 0.0, "ss": 0.0})
    linhas = []
    for k in sorted(set(pbi) | set(ss)):
        p, s = pbi.get(k), ss.get(k)
        vp, vs = (p or {}).get("valor", 0.0), (s or {}).get("valor", 0.0)
        if p and not s:
            cat = "Só no Power BI"
        elif s and not p:
            cat = "Só no Superset"
        elif abs(vp - vs) <= a.tolerancia:
            cat = "Igual"
        else:
            cat = "Valor diferente"
        c = cats[cat]
        c["docs"] += 1
        c["pbi"] += vp
        c["ss"] += vs
        if cat != "Igual":
            linhas.append({
                "documento": (p or s)["chave_original"], "categoria": cat, "valor_pbi": vp, "valor_superset": vs,
                "diferenca_pbi_menos_superset": vp - vs,
                "data_pbi": ",".join(sorted((p or {}).get("datas") or [])),
                "data_superset": ",".join(sorted((s or {}).get("datas") or [])),
                **{f"pbi_{c}": v for c, v in ((p or {}).get("attrs") or {}).items()},
                **{f"ss_{c}": v for c, v in ((s or {}).get("attrs") or {}).items()},
            })
    linhas.sort(key=lambda r: -abs(r["diferenca_pbi_menos_superset"]))

    total_pbi = sum(d["valor"] for d in pbi.values())
    total_ss = sum(d["valor"] for d in ss.values())
    ordem = ["Igual", "Valor diferente", "Só no Power BI", "Só no Superset"]
    resumo = {
        "total_pbi": round(total_pbi, 2), "total_superset": round(total_ss, 2),
        "diferenca_total": round(total_pbi - total_ss, 2),
        "docs_pbi": len(pbi), "docs_superset": len(ss),
        "categorias": {k: {"docs": cats[k]["docs"], "valor_pbi": round(cats[k]["pbi"], 2),
                           "valor_superset": round(cats[k]["ss"], 2),
                           "diferenca": round(cats[k]["pbi"] - cats[k]["ss"], 2)} for k in ordem},
        "maiores_diferencas": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()
                                if k in ("documento", "categoria", "valor_pbi", "valor_superset",
                                         "diferenca_pbi_menos_superset", "data_pbi", "data_superset")}
                               for r in linhas[: a.top]],
    }

    # Diferença por dia (cada sistema pela sua própria data)
    por_dia = []
    if a.data_pbi and a.data_ss:
        dias = defaultdict(lambda: [0.0, 0.0, 0, 0])
        for d in pbi.values():
            for dt in d["datas"] or {""}:
                dias[dt][0] += d["valor"] / max(len(d["datas"]), 1)
                dias[dt][2] += 1
        for d in ss.values():
            for dt in d["datas"] or {""}:
                dias[dt][1] += d["valor"] / max(len(d["datas"]), 1)
                dias[dt][3] += 1
        por_dia = [{"dia": k, "valor_pbi": round(v[0], 2), "valor_superset": round(v[1], 2),
                    "diferenca": round(v[0] - v[1], 2), "docs_pbi": v[2], "docs_superset": v[3]}
                   for k, v in sorted(dias.items())]
        resumo["dias_com_diferenca"] = [d for d in por_dia if abs(d["diferenca"]) > a.tolerancia]

    # Arquivos
    (saida / "resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
    campos = []
    for r in linhas:
        for c in r:
            if c not in campos:
                campos.append(c)
    with open(saida / "divergencias.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=campos or ["documento"], delimiter=";")
        w.writeheader()
        for r in linhas:
            w.writerow({k: (br(v).replace(".", "") if isinstance(v, float) else v) for k, v in r.items()})
    if por_dia:
        with open(saida / "por_dia.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(por_dia[0].keys()), delimiter=";")
            w.writeheader()
            for r in por_dia:
                w.writerow({k: (br(v).replace(".", "") if isinstance(v, float) else v) for k, v in r.items()})

    md = [f"# Comparação Power BI × Superset", "",
          f"| | Power BI | Superset | Diferença |", "|---|---:|---:|---:|",
          f"| Valor | {br(total_pbi)} | {br(total_ss)} | **{br(total_pbi - total_ss)}** |",
          f"| Documentos | {len(pbi)} | {len(ss)} | {len(pbi) - len(ss)} |", "",
          "| Categoria | Docs | Valor Power BI | Valor Superset | Diferença |", "|---|---:|---:|---:|---:|"]
    for k in ordem:
        c = resumo["categorias"][k]
        md.append(f"| {k} | {c['docs']} | {br(c['valor_pbi'])} | {br(c['valor_superset'])} | {br(c['diferenca'])} |")
    md += ["", f"Soma das diferenças por categoria = {br(sum(c['diferenca'] for c in resumo['categorias'].values()))} "
               f"(deve ser igual à diferença total).", "",
           f"## Maiores diferenças (top {a.top})", "",
           "| Documento | Categoria | Power BI | Superset | Diferença | Data PBI | Data Superset |",
           "|---|---|---:|---:|---:|---|---|"]
    for r in resumo["maiores_diferencas"]:
        md.append(f"| {r['documento']} | {r['categoria']} | {br(r['valor_pbi'])} | {br(r['valor_superset'])} | "
                  f"{br(r['diferenca_pbi_menos_superset'])} | {r['data_pbi']} | {r['data_superset']} |")
    if por_dia:
        md += ["", "## Dias com diferença", "", "| Dia | Power BI | Superset | Diferença | Docs PBI | Docs SS |",
               "|---|---:|---:|---:|---:|---:|"]
        for d in resumo["dias_com_diferenca"]:
            md.append(f"| {d['dia']} | {br(d['valor_pbi'])} | {br(d['valor_superset'])} | {br(d['diferenca'])} | "
                      f"{d['docs_pbi']} | {d['docs_superset']} |")
    md += ["", f"Arquivos: `{saida / 'divergencias.csv'}`" + (f", `{saida / 'por_dia.csv'}`" if por_dia else "")]
    (saida / "resumo.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
