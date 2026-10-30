"""Mede o tempo de carregamento de um painel do Superset, gráfico a gráfico.

Etapas (cada uma pode ser desligada):
  estrutura   painel, abas, filtros nativos, gráficos e datasets (cache, SQL do dataset virtual)
  medicao     cada gráfico rodado sozinho pela API do Superset: sem cache (force) e de novo com cache.
              Aplica o valor padrão dos filtros nativos, como na abertura real do painel.
  concorrencia todos os gráficos sem cache ao mesmo tempo (6 por vez, como o navegador)
  filtros     consulta que preenche a lista de cada filtro nativo
  dremio      o SQL gerado de cada gráfico rodado direto no Dremio: tempo e reflections usadas
  log         tempos reais vistos pelos usuários (eventos load_chart/render_chart do log do Superset)

Só lê: não altera gráficos, datasets nem o painel. Imprime um JSON no stdout (nada é gravado em arquivo).

Uso: python -X utf8 ferramentas/medir_painel.py --painel 212 [--dias-log 7] [--sem dremio,concorrencia]
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mcp_cliente import ClienteMCP  # noqa: E402

HUB = Path(__file__).resolve().parents[3]
BRASILIA = timezone(timedelta(hours=-3))
ETAPAS = ("estrutura", "medicao", "concorrencia", "filtros", "dremio", "log")
SIMULTANEOS = 6  # requisições por domínio que o navegador faz ao mesmo tempo (HTTP/1.1)


def log(msg: str):
    print(f"[{datetime.now(BRASILIA):%H:%M:%S}] {msg}", file=sys.stderr, flush=True)


def resultado(texto: str):
    externo = json.loads(texto)
    interno = json.loads(externo["result"]) if isinstance(externo.get("result"), str) else externo
    return interno.get("result", interno) if isinstance(interno, dict) else interno


def chamar(ss: ClienteMCP, ferramenta: str, args: dict, timeout: float = 600):
    return resultado(ss.chamar(ferramenta, args, timeout))


# --- estrutura ------------------------------------------------------------------------------------
def abas_dos_graficos(posicao: dict) -> dict[int, list[str]]:
    """chart_id -> caminho de abas até ele (vazio = fora de abas, carrega na abertura)."""
    abas = {}
    for no in posicao.values():
        if not isinstance(no, dict) or no.get("type") != "CHART":
            continue
        cid = (no.get("meta") or {}).get("chartId")
        caminho = []
        for pai in no.get("parents") or []:
            p = posicao.get(pai) or {}
            if p.get("type") == "TAB":
                caminho.append((p.get("meta") or {}).get("text") or pai)
        abas[cid] = caminho
    return abas


def primeira_aba(posicao: dict) -> set[str]:
    """Abas visíveis na abertura: a primeira de cada grupo de abas."""
    visiveis = set()
    for no in posicao.values():
        if isinstance(no, dict) and no.get("type") == "TABS" and no.get("children"):
            p = posicao.get(no["children"][0]) or {}
            visiveis.add((p.get("meta") or {}).get("text") or no["children"][0])
    return visiveis


def alertas_dataset(ds: dict, cache_banco) -> list[str]:
    a = []
    sql = ds.get("sql") or ""
    s = re.sub(r"--[^\n]*", "", sql).upper()
    if ds.get("cache_timeout") == -1:
        a.append("cache desligado (cache_timeout = -1): toda abertura consulta o Dremio")
    elif ds.get("cache_timeout") is None:
        a.append(f"cache herdado do banco ({cache_banco} s)" if cache_banco else "cache padrão do Superset")
    if sql:
        if re.search(r"\bJOIN\b", s):
            a.append("JOIN no SQL do dataset virtual: roda a cada consulta, dentro da subconsulta do Superset")
        if re.search(r"SELECT\s+\*", s):
            a.append("SELECT * no dataset virtual")
        if re.search(r"\bON\b[^;]*\b(CAST|LTRIM|RTRIM|TRIM|UPPER|LOWER|SUBSTR|LPAD)\s*\(", s):
            a.append("função na chave do JOIN (CAST/TRIM...): impede uso eficiente de índice/reflection")
        if "COUNT(DISTINCT" in s.replace(" ", ""):
            a.append("COUNT DISTINCT no dataset")
        if "{{" in sql or "{%" in sql:
            a.append("Jinja no SQL: o SQL muda a cada filtro (cache por combinação; pode impedir reflection)")
        if re.search(r"\bORDER\s+BY\b", s):
            a.append("ORDER BY dentro do dataset virtual (trabalho perdido: o Superset reordena)")
        if re.search(r"\bUNION\b", s):
            a.append("UNION no dataset virtual")
    return a


def alertas_grafico(g: dict, params: dict, qc: dict | None) -> list[str]:
    a = []
    viz = g.get("viz_type") or ""
    q = ((qc or {}).get("queries") or [{}])[0]
    limite = q.get("row_limit") or params.get("row_limit")
    if not qc:
        a.append("sem query_context salvo: abrir o gráfico no Superset e salvar para poder medir")
    if limite and int(limite) > 10000:
        a.append(f"row_limit alto ({limite}): muitos dados trafegam e são desenhados no navegador")
    if viz == "table" and not params.get("server_pagination") and (params.get("all_columns") or []) and (limite or 0) > 1000:
        a.append("tabela em modo raw sem paginação no servidor")
    if viz == "handlebars" and (limite or 0) > 500:
        a.append(f"Handlebars com até {limite} linhas: o desenho no navegador pesa")
    if g.get("cache_timeout") == -1:
        a.append("cache do gráfico desligado (-1)")
    colunas = json.dumps(q, ensure_ascii=False)
    if re.search(r"_faturado\b|dh_update_silver", colunas):
        a.append("usa colunas *_faturado/dh_update_silver: na VENDAS_CONSOLIDADA_DASH podem tirar a consulta da reflection (confirmar em dremio_direto)")
    if len(q.get("metrics") or []) > 8:
        a.append(f"{len(q['metrics'])} métricas numa só consulta")
    return a


# --- filtros nativos aplicados ao gráfico ---------------------------------------------------------
def no_escopo(filtro: dict, cid: int) -> bool:
    if "chartsInScope" in filtro:
        return cid in (filtro.get("chartsInScope") or [])
    return cid not in ((filtro.get("scope") or {}).get("excluded") or [])


def aplicar_padroes(qc: dict, cid: int, filtros: list[dict], colunas_ds: set[str]) -> list[str]:
    """Aplica o valor padrão dos filtros nativos (como a abertura do painel). Devolve o que aplicou."""
    aplicados = []
    for f in filtros:
        if not no_escopo(f, cid):
            continue
        extra = ((f.get("defaultDataMask") or {}).get("extraFormData") or {})
        if not extra:
            continue
        for q in qc.get("queries") or []:
            for flt in extra.get("filters") or []:
                if flt.get("col") in colunas_ds:
                    q.setdefault("filters", []).append(flt)
                    aplicados.append(f"{f.get('name')}: {flt.get('col')} {flt.get('op')} {flt.get('val')}")
            for ad in extra.get("adhoc_filters") or []:
                expr = ad.get("sqlExpression") or ""
                if ad.get("expressionType") == "SQL" and any(re.search(rf"\b{re.escape(c)}\b", expr) for c in colunas_ds):
                    ex = q.setdefault("extras", {})
                    chave = "having" if ad.get("clause") == "HAVING" else "where"
                    ex[chave] = f"({ex[chave]}) AND ({expr})" if ex.get(chave) else f"({expr})"
                    aplicados.append(f"{f.get('name')}: {expr}")
            if extra.get("time_range"):
                temporais = [x for x in q.get("filters") or [] if x.get("op") == "TEMPORAL_RANGE"]
                for x in temporais:
                    x["val"] = extra["time_range"]
                if not temporais:
                    q["time_range"] = extra["time_range"]
                aplicados.append(f"{f.get('name')}: período {extra['time_range']}")
    return sorted(set(aplicados))


def rodar_grafico(ss: ClienteMCP, qc: dict, forcar: bool) -> dict:
    corpo = dict(qc, force=forcar, result_format="json", result_type="full")
    t = time.perf_counter()
    try:
        texto = ss.chamar("superset_chart_data", {"query_context": json.dumps(corpo, ensure_ascii=False)}, 900)
    except Exception as e:  # noqa: BLE001 — erro do gráfico vira dado da medição
        return {"segundos": round(time.perf_counter() - t, 2), "erro": str(e)[:500]}
    seg = time.perf_counter() - t
    res = resultado(texto)
    consultas = res if isinstance(res, list) else []
    erro = next((c.get("error") for c in consultas if c.get("error")), None)
    return {
        "segundos": round(seg, 2),
        "kb": round(len(texto.encode("utf-8")) / 1024, 1),
        "linhas": sum(c.get("rowcount") or 0 for c in consultas),
        "consultas": len(consultas),
        "do_cache": all(bool(c.get("is_cached")) for c in consultas) if consultas else None,
        "cache_timeout_efetivo": consultas[0].get("cache_timeout") if consultas else None,
        "erro": str(erro)[:500] if erro else None,
        "sql": [c.get("query") for c in consultas if c.get("query")],
    }


# --- Dremio direto --------------------------------------------------------------------------------
class Dremio:
    def __init__(self):
        cfg = (HUB / "data" / "dremioai.yaml").read_text(encoding="utf-8")
        self.uri = re.search(r"^\s*uri:\s*(\S+)", cfg, re.M).group(1).rstrip("/")
        self.pat = (HUB / "data" / "dremio.pat").read_text(encoding="utf-8").strip()
        self.nomes: dict[str, str] = {}

    def _req(self, metodo: str, caminho: str, corpo=None):
        r = urllib.request.Request(self.uri + caminho, method=metodo,
                                   data=json.dumps(corpo).encode() if corpo is not None else None,
                                   headers={"Authorization": f"Bearer {self.pat}", "Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=120) as resp:
            return json.load(resp)

    def nome(self, dataset_id: str) -> str:
        if dataset_id not in self.nomes:
            try:
                self.nomes[dataset_id] = ".".join(self._req("GET", f"/api/v3/catalog/{dataset_id}").get("path") or [dataset_id])
            except Exception:  # noqa: BLE001
                self.nomes[dataset_id] = dataset_id
        return self.nomes[dataset_id]

    def rodar(self, sql: str, prazo: float = 600) -> dict:
        t = time.perf_counter()
        try:
            jid = self._req("POST", "/api/v3/sql", {"sql": sql})["id"]
            while True:
                st = self._req("GET", f"/api/v3/job/{jid}")
                if st.get("jobState") in ("COMPLETED", "FAILED", "CANCELED"):
                    break
                if time.perf_counter() - t > prazo:
                    self._req("POST", f"/api/v3/job/{jid}/cancel", {})
                    return {"segundos": round(time.perf_counter() - t, 2), "erro": f"cancelado após {prazo}s"}
                time.sleep(0.3)
        except Exception as e:  # noqa: BLE001
            return {"segundos": round(time.perf_counter() - t, 2), "erro": str(e)[:300]}
        rel = (st.get("acceleration") or {}).get("reflectionRelationships") or []
        escolhidas = sorted({self.nome(r["datasetId"]) for r in rel if r.get("relationship") == "CHOSEN"})
        casadas = sorted({self.nome(r["datasetId"]) for r in rel if r.get("relationship") == "MATCHED"} - set(escolhidas))
        inicio, fim = st.get("startedAt"), st.get("endedAt")
        dur = None
        if inicio and fim:
            p = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))  # noqa: E731
            dur = round((p(fim) - p(inicio)).total_seconds(), 2)
        return {"segundos": round(time.perf_counter() - t, 2), "segundos_no_dremio": dur, "estado": st.get("jobState"),
                "linhas": st.get("rowCount"), "reflections_usadas": escolhidas, "reflections_casadas_nao_usadas": casadas,
                "erro": (st.get("errorMessage") or "")[:300] or None, "job": jid}


# --- log do Superset ------------------------------------------------------------------------------
def percentil(valores: list[float], p: float):
    if not valores:
        return None
    v = sorted(valores)
    return v[min(len(v) - 1, round(p / 100 * (len(v) - 1)))]


def ler_log(ss: ClienteMCP, painel: int, dias: int, graficos_do_painel: set[int], max_eventos: int = 5000) -> dict:
    desde = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%S")
    q = f"(filters:!((col:dashboard_id,opr:eq,value:{painel}),(col:action,opr:eq,value:log),(col:dttm,opr:gt,value:'{desde}')),order_column:dttm,order_direction:desc)"
    eventos, pagina, total = [], 0, None
    while len(eventos) < max_eventos:
        r = json.loads(ss.chamar("superset_log_list", {"q": q, "page": pagina, "page_size": 100}, 300))
        r = json.loads(r["result"]) if isinstance(r.get("result"), str) else r
        total = r.get("count", total)
        lote = r.get("result") or []
        for e in lote:
            try:
                j = json.loads(e.get("json") or "{}")
            except json.JSONDecodeError:
                continue
            j["_dttm"] = e.get("dttm")
            j["_user"] = e.get("user_id")
            eventos.append(j)
        if len(lote) < 100:
            break
        pagina += 1
    # Só eventos deste painel (o navegador manda no mesmo lote eventos de outros painéis abertos na mesma aba).
    nomes_eventos: dict[str, int] = {}
    meus = []
    for j in eventos:
        if (j.get("dashboard_id") or j.get("source_id")) != painel:
            continue
        nome = j.get("event_name") or "?"
        nomes_eventos[nome] = nomes_eventos.get(nome, 0) + 1
        if graficos_do_painel and j.get("slice_id") and j["slice_id"] not in graficos_do_painel:
            continue
        meus.append(j)

    # Uma "carga" = gráficos disparados pela mesma ação (abrir, filtrar, atualizar): mesmo impression_id + trigger_event.
    # start_offset conta desde a abertura da página; medimos cada carga a partir do primeiro pedido dela.
    cargas: dict[tuple, dict] = {}
    for j in meus:
        if j.get("event_name") not in ("load_chart", "render_chart") or not j.get("impression_id"):
            continue
        c = cargas.setdefault((j["impression_id"], j.get("trigger_event")),
                              {"loads": [], "renders": [], "dttm": j["_dttm"], "usuario": j["_user"], "imp": j["impression_id"]})
        c["loads" if j["event_name"] == "load_chart" else "renders"].append(j)
        c["dttm"] = min(c["dttm"], j["_dttm"])
    primeira_da_aba: dict[str, tuple] = {}
    for chave, c in cargas.items():
        if not c["loads"]:
            continue
        c["t0"] = min(x.get("start_offset") or 0 for x in c["loads"])
        if c["imp"] not in primeira_da_aba or c["t0"] < cargas[primeira_da_aba[c["imp"]]]["t0"]:
            primeira_da_aba[c["imp"]] = chave

    por_grafico: dict[int, dict] = {}
    resumo_cargas = {"abertura": [], "outras": []}
    por_hora: dict[int, list[float]] = {}
    for chave, c in cargas.items():
        if not c["loads"]:
            continue
        t0, fim = c["t0"], 0
        for x in c["loads"]:
            ini = (x.get("start_offset") or 0) - t0
            dur = x.get("duration") or 0
            fim = max(fim, ini + dur)
            g = por_grafico.setdefault(x["slice_id"], {"carga_ms": [], "espera_ms": [], "tela_ms": [], "desenho_ms": [],
                                                       "cache": 0, "erros": 0, "estouros": 0, "linhas": []})
            g["carga_ms"].append(dur)
            g["espera_ms"].append(ini)
            g["tela_ms"].append(ini + dur)
            g["cache"] += 1 if x.get("is_cached") else 0
            g["erros"] += 1 if x.get("has_err") else 0
            g["estouros"] += 1 if dur >= 59000 else 0
            if x.get("row_count") is not None:
                g["linhas"].append(x["row_count"])
        for x in c["renders"]:
            if x.get("slice_id") in por_grafico:
                por_grafico[x["slice_id"]]["desenho_ms"].append(x.get("duration") or 0)
            fim = max(fim, (x.get("start_offset") or 0) + (x.get("duration") or 0) - t0)
        tipo = "abertura" if primeira_da_aba.get(c["imp"]) == chave else "outras"
        resumo_cargas[tipo].append(fim / 1000)
        if tipo == "abertura" and c["dttm"]:
            h = datetime.fromisoformat(c["dttm"][:19]).replace(tzinfo=timezone.utc).astimezone(BRASILIA).hour
            por_hora.setdefault(h, []).append(fim / 1000)

    def s(ms, p=None):
        if not ms:
            return None
        return round((percentil(ms, p) if p else statistics.median(ms)) / 1000, 2)

    graficos = {sid: {
        "cargas": len(g["carga_ms"]),
        "consulta_mediana_s": s(g["carga_ms"]), "consulta_p90_s": s(g["carga_ms"], 90),
        "espera_mediana_s": s(g["espera_ms"]),
        "ate_aparecer_mediana_s": s(g["tela_ms"]), "ate_aparecer_p90_s": s(g["tela_ms"], 90),
        "desenho_mediana_s": s(g["desenho_ms"]),
        "pct_do_cache": round(100 * g["cache"] / len(g["carga_ms"])),
        "erros": g["erros"], "estouros_60s": g["estouros"],
        "linhas_max": max(g["linhas"]) if g["linhas"] else None,
    } for sid, g in por_grafico.items()}
    ab, outras = resumo_cargas["abertura"], resumo_cargas["outras"]
    return {
        "periodo": f"últimos {dias} dias", "eventos_lidos": len(eventos), "eventos_no_periodo": total,
        "eventos_deste_painel": nomes_eventos,
        "aberturas": len(ab), "outras_cargas_filtro_ou_atualizacao": len(outras),
        "usuarios": len({c["usuario"] for c in cargas.values() if c["loads"]}),
        "abertura_completa_mediana_s": round(statistics.median(ab), 1) if ab else None,
        "abertura_completa_p90_s": round(percentil(ab, 90), 1) if ab else None,
        "outras_cargas_mediana_s": round(statistics.median(outras), 1) if outras else None,
        "outras_cargas_p90_s": round(percentil(outras, 90), 1) if outras else None,
        "abertura_por_hora_brasilia": {h: {"aberturas": len(v), "mediana_s": round(statistics.median(v), 1)}
                                       for h, v in sorted(por_hora.items())},
        "graficos": graficos,
    }


# --- principal ------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--painel", type=int, required=True)
    ap.add_argument("--dias-log", type=int, default=7)
    ap.add_argument("--sem", default="", help=f"etapas a pular, separadas por vírgula: {','.join(ETAPAS)}")
    ap.add_argument("--graficos", default="", help="só estes chart_ids (vírgula); padrão = todos do painel")
    a = ap.parse_args()
    pular = {s.strip() for s in a.sem.split(",") if s.strip()}
    inicio = datetime.now(BRASILIA)
    saida: dict = {"painel": a.painel, "medido_em_brasilia": f"{inicio:%d/%m/%Y %H:%M}"}

    with ClienteMCP("superset-herval") as ss:
        log("lendo painel")
        d = chamar(ss, "superset_dashboard_get", {"dashboard_id": a.painel}, 120)
        meta = json.loads(d.get("json_metadata") or "{}")
        posicao = json.loads(d.get("position_json") or "{}")
        filtros = [f for f in meta.get("native_filter_configuration") or [] if f.get("type") == "NATIVE_FILTER"]
        abas = abas_dos_graficos(posicao)
        visiveis = primeira_aba(posicao)
        lista = chamar(ss, "superset_dashboard_charts", {"dashboard_id": a.painel}, 120)
        ids = [c["id"] for c in lista]
        if a.graficos:
            ids = [i for i in ids if str(i) in a.graficos.split(",")]

        bancos: dict[int, dict] = {}
        datasets: dict[int, dict] = {}
        graficos: dict[int, dict] = {}
        for cid in ids:
            g = chamar(ss, "superset_chart_get", {"chart_id": cid}, 120)
            params = json.loads(g.get("params") or "{}")
            qc = json.loads(g["query_context"]) if g.get("query_context") else None
            dsid = g.get("datasource_id")
            if dsid not in datasets:
                ds = chamar(ss, "superset_dataset_get", {"dataset_id": dsid}, 120)
                dbid = (ds.get("database") or {}).get("id")
                if dbid not in bancos:
                    try:
                        bancos[dbid] = chamar(ss, "superset_database_connection_info", {"database_id": dbid}, 120)
                    except Exception:  # noqa: BLE001
                        bancos[dbid] = {}
                cache_banco = bancos[dbid].get("cache_timeout")
                datasets[dsid] = {
                    "nome": ds.get("table_name"), "virtual": bool(ds.get("sql")), "cache_timeout": ds.get("cache_timeout"),
                    "cache_do_banco": cache_banco, "colunas": [c.get("column_name") for c in ds.get("columns") or []],
                    "sql": ds.get("sql"), "alertas": alertas_dataset(ds, cache_banco),
                }
            caminho = abas.get(cid, [])
            na_abertura = not caminho or all(t in visiveis for t in caminho)
            graficos[cid] = {
                "nome": g.get("slice_name"), "tipo": g.get("viz_type"), "dataset": dsid,
                "abas": caminho, "carrega_na_abertura": na_abertura, "cache_timeout": g.get("cache_timeout"),
                "row_limit": (((qc or {}).get("queries") or [{}])[0]).get("row_limit"),
                "alertas": alertas_grafico(g, params, qc), "_qc": qc,
            }
        saida["estrutura"] = {
            "titulo": d.get("dashboard_title"), "graficos": len(ids),
            "na_abertura": sum(1 for g in graficos.values() if g["carrega_na_abertura"]),
            "atualizacao_automatica_s": meta.get("refresh_frequency") or 0,
            "filtros_nativos": [{"nome": f.get("name"), "tipo": f.get("filterType"),
                                 "alvo": [(t.get("datasetId"), (t.get("column") or {}).get("name")) for t in f.get("targets") or []],
                                 "tem_padrao": bool((f.get("defaultDataMask") or {}).get("extraFormData")),
                                 "primeiro_item_automatico": bool((f.get("controlValues") or {}).get("defaultToFirstItem")),
                                 "depende_de": f.get("cascadeParentIds") or []} for f in filtros],
            "datasets": {k: {kk: vv for kk, vv in v.items() if kk != "colunas"} for k, v in datasets.items()},
        }

        if "medicao" not in pular:
            for cid, g in graficos.items():
                if not g["_qc"]:
                    continue
                qc = json.loads(json.dumps(g["_qc"]))
                g["filtros_aplicados"] = aplicar_padroes(qc, cid, filtros, set(datasets[g["dataset"]]["colunas"]))
                g["_qc_medido"] = qc
                log(f"gráfico {cid} sem cache")
                g["sem_cache"] = rodar_grafico(ss, qc, True)
                log(f"gráfico {cid} com cache ({g['sem_cache']['segundos']}s sem)")
                g["com_cache"] = rodar_grafico(ss, qc, False)
                g["com_cache"].pop("sql", None)

        if "filtros" not in pular:
            saida["consultas_dos_filtros"] = []
            for f in filtros:
                for t in f.get("targets") or []:
                    col = (t.get("column") or {}).get("name")
                    if not (t.get("datasetId") and col) or f.get("filterType") != "filter_select":
                        continue
                    log(f"filtro {f.get('name')}")
                    qc = {"datasource": {"id": t["datasetId"], "type": "table"},
                          "queries": [{"columns": [col], "metrics": [], "row_limit": 1000, "orderby": [[col, True]]}]}
                    r = rodar_grafico(ss, qc, True)
                    nome_ds = datasets.get(t["datasetId"], {}).get("nome")
                    if nome_ds is None:
                        try:
                            nome_ds = chamar(ss, "superset_dataset_get", {"dataset_id": t["datasetId"]}, 120).get("table_name")
                        except Exception:  # noqa: BLE001
                            nome_ds = None
                    saida["consultas_dos_filtros"].append({
                        "filtro": f.get("name"), "dataset": t["datasetId"], "dataset_nome": nome_ds, "coluna": col,
                        "segundos": r["segundos"], "linhas": r.get("linhas"), "erro": r.get("erro"),
                        "sql": (r.get("sql") or [None])[0]})

        if "log" not in pular:
            log("lendo log do Superset")
            try:
                saida["log_usuarios"] = ler_log(ss, a.painel, a.dias_log, {c["id"] for c in lista})
            except Exception as e:  # noqa: BLE001
                saida["log_usuarios"] = {"erro": str(e)[:300]}

    medidos = [(cid, g) for cid, g in graficos.items() if g.get("_qc_medido")]
    if "concorrencia" not in pular and "medicao" not in pular and medidos:
        log(f"concorrência: {len(medidos)} gráficos, {SIMULTANEOS} por vez")
        abertos: list[ClienteMCP] = []
        trava = threading.Lock()
        local = threading.local()

        def cliente() -> ClienteMCP:  # uma conexão MCP por thread, como as conexões paralelas do navegador
            if not hasattr(local, "c"):
                local.c = ClienteMCP("superset-herval")
                with trava:
                    abertos.append(local.c)
            return local.c

        abertura = [(cid, g) for cid, g in medidos if g["carrega_na_abertura"]] or medidos
        t0 = time.perf_counter()

        def um(item):
            cid, g = item
            ini = time.perf_counter() - t0
            r = rodar_grafico(cliente(), g["_qc_medido"], True)
            return cid, round(ini, 2), r

        try:
            with ThreadPoolExecutor(SIMULTANEOS) as ex:
                res = list(ex.map(um, abertura))
        finally:
            for c in abertos:
                c.fechar()
        saida["concorrencia"] = {
            "graficos": len(abertura), "simultaneos": SIMULTANEOS,
            "total_s": round(time.perf_counter() - t0, 2),
            "soma_individual_s": round(sum(g["sem_cache"]["segundos"] for _, g in abertura), 2),
            "por_grafico": {cid: {"espera_s": ini, "consulta_s": r["segundos"], "aparece_em_s": round(ini + r["segundos"], 2),
                                  "erro": r.get("erro")} for cid, ini, r in res},
        }

    if "dremio" not in pular and "medicao" not in pular:
        try:
            dr = Dremio()
        except Exception as e:  # noqa: BLE001
            dr = None
            saida["dremio_erro"] = f"sem acesso ao Dremio: {e}"
        if dr:
            for cid, g in medidos:
                sqls = (g.get("sem_cache") or {}).get("sql") or []
                if sqls:
                    log(f"Dremio direto: gráfico {cid}")
                    g["dremio_direto"] = [dr.rodar(s) for s in sqls]

    for g in graficos.values():
        g.pop("_qc", None)
        g.pop("_qc_medido", None)
        if g.get("sem_cache"):
            g["sql_gerado"] = g["sem_cache"].pop("sql", None)
    saida["graficos"] = graficos
    saida["duracao_da_medicao_s"] = round((datetime.now(BRASILIA) - inicio).total_seconds())
    print(json.dumps(saida, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
