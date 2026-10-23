"""Consumo de capacidade: lê os históricos locais do Claude Code e do Codex e monta o resumo da aba "Consumo".

- Claude: ~/.claude/projects/<pasta>/<sessão>.jsonl (+ <sessão>/subagents/agent-*.jsonl). Cada resposta da API
  traz os tokens (entrada, cache lido, cache gravado, saída). O peso de cada requisição é o custo equivalente
  em preço de API, calibrado pelo próprio Claude Code (registros "cost-state"), porque é isso que consome o limite.
- Codex: ~/.codex/sessions/AAAA/MM/DD/rollout-*.jsonl (eventos token_count, com os limites do plano).
- Limites do Claude: o SDK informa o uso das janelas de 5 h e 7 dias a cada conversa (salvo em data/limites.json).

Tudo é aproximado e cobre só este computador (VS Code + Agent Hub); não inclui outros dispositivos nem claude.ai.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from . import store

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CODEX_SESSIONS = Path.home() / ".codex" / "sessions"
LIMITES = store.DATA_DIR / "limites.json"

# Peso relativo de cada tipo de token (mesma proporção dos preços da API).
PESO = {"in": 1.0, "out": 5.0, "cr": 0.1, "cw5": 1.25, "cw1h": 2.0}
# Preço de entrada (US$ por milhão) por família, usado só quando não há "cost-state" para calibrar o modelo.
PRECO_FAMILIA = {"opus": 5.0, "fable": 5.0, "mythos": 5.0, "sonnet": 3.0, "haiku": 1.0}
CONTEXTO_GRANDE = 150_000

_cache_claude: dict[str, tuple[tuple, dict]] = {}
_cache_codex: dict[str, tuple[tuple, dict]] = {}


# ============================================================================================ Claude
def _ts(valor) -> float:
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _tam(conteudo) -> int:
    """Tokens aproximados de um conteúdo (≈ 4 caracteres por token)."""
    if isinstance(conteudo, str):
        return len(conteudo) // 4
    return len(json.dumps(conteudo, ensure_ascii=False)) // 4


def _origem_ferramenta(nome: str, entrada: dict) -> str | None:
    if nome.startswith("mcp__"):
        partes = nome.split("__")
        return f"mcp:{partes[1]}" if len(partes) > 1 else None
    if nome == "Skill":
        return f"skill:{(entrada or {}).get('skill') or '?'}"
    return None


def _ler_claude(arquivo: Path) -> dict:
    """Resumo de um arquivo de sessão (ou de subagente). Guardado em cache até o arquivo mudar."""
    sub = arquivo.parent.name == "subagents"
    info = {"arquivo": str(arquivo), "sub": sub, "sessao": arquivo.parent.parent.name if sub else arquivo.stem,
            "projeto": (arquivo.parent.parent.parent if sub else arquivo.parent).name,
            "tipo_agente": None, "titulo": None, "entrypoint": None, "cwd": None, "custo": {}, "req": [],
            "ferramentas": Counter(), "tokens_resultado": Counter()}
    if sub:
        meta = arquivo.with_suffix(".meta.json")
        try:
            info["tipo_agente"] = json.loads(meta.read_text(encoding="utf-8")).get("agentType")
        except (OSError, ValueError):
            pass
    origem_por_id: dict[str, str] = {}
    contexto: Counter = Counter()  # tokens de resultados de MCP/skill que estão no contexto agora
    reqs: dict[str, dict] = {}
    with open(arquivo, encoding="utf-8", errors="replace") as f:
        for linha in f:
            # Só interessa uma parte dos registros: pula os grandes que não importam sem decodificar.
            if not linha.startswith("{"):
                continue
            try:
                d = json.loads(linha)
            except ValueError:
                continue
            tipo = d.get("type")
            if tipo == "assistant":
                m = d.get("message") or {}
                for c in m.get("content") or []:
                    if c.get("type") == "tool_use":
                        nome = c.get("name") or "?"
                        info["ferramentas"][nome] += 1
                        o = _origem_ferramenta(nome, c.get("input"))
                        if o:
                            origem_por_id[c.get("id")] = o
                u = m.get("usage")
                mid = m.get("id") or d.get("requestId")
                if not u or not mid or m.get("model") in (None, "<synthetic>"):
                    continue
                cc = u.get("cache_creation") or {}
                cw1h = cc.get("ephemeral_1h_input_tokens") or 0
                cw = u.get("cache_creation_input_tokens") or 0
                r = reqs.get(mid)
                if r is None:
                    r = reqs[mid] = {"ts": _ts(d.get("timestamp")), "modelo": m.get("model"),
                                     "atrib": dict(contexto) if contexto else None}
                r.update({"in": u.get("input_tokens") or 0, "cr": u.get("cache_read_input_tokens") or 0,
                          "cw1h": cw1h, "cw5": max(0, cw - cw1h), "out": u.get("output_tokens") or 0})
                info["entrypoint"] = info["entrypoint"] or d.get("entrypoint")
                info["cwd"] = info["cwd"] or d.get("cwd")
            elif tipo == "user":
                m = d.get("message") or {}
                fonte = origem_por_id.get(d.get("sourceToolUseID"))
                if fonte:  # conteúdo da skill carregada
                    n = _tam(m.get("content"))
                    contexto[fonte] += n
                    info["tokens_resultado"][fonte] += n
                if isinstance(m.get("content"), list):
                    for c in m["content"]:
                        if isinstance(c, dict) and c.get("type") == "tool_result":
                            fonte = origem_por_id.get(c.get("tool_use_id"))
                            if fonte:
                                n = _tam(c.get("content"))
                                contexto[fonte] += n
                                info["tokens_resultado"][fonte] += n
                if d.get("isCompactSummary"):
                    contexto.clear()
                info["entrypoint"] = info["entrypoint"] or d.get("entrypoint")
                info["cwd"] = info["cwd"] or d.get("cwd")
            elif tipo == "system" and d.get("subtype") == "compact_boundary":
                contexto.clear()
            elif tipo in ("ai-title", "custom-title"):
                info["titulo"] = d.get("customTitle") or d.get("aiTitle") or d.get("title") or info["titulo"]
            elif tipo == "cost-state":
                info["custo"] = {k: v.get("costUSD") or 0 for k, v in (d.get("modelUsage") or {}).items()}
    info["req"] = list(reqs.values())
    return info


def _arquivos_claude(desde: float) -> list[Path]:
    if not CLAUDE_PROJECTS.is_dir():
        return []
    saida = []
    for padrao in ("*/*.jsonl", "*/*/subagents/*.jsonl"):
        for f in CLAUDE_PROJECTS.glob(padrao):
            try:
                if f.stat().st_mtime >= desde:
                    saida.append(f)
            except OSError:
                pass
    return saida


def _sessoes_claude(desde: float) -> list[dict]:
    infos = []
    for f in _arquivos_claude(desde):
        try:
            st = f.stat()
        except OSError:
            continue
        chave = (st.st_mtime, st.st_size)
        hit = _cache_claude.get(str(f))
        if hit and hit[0] == chave:
            infos.append(hit[1])
            continue
        info = _ler_claude(f)
        _cache_claude[str(f)] = (chave, info)
        infos.append(info)
    return infos


def _unidades(r: dict) -> float:
    return sum(r[k] * PESO[k] for k in PESO)


def _precos(infos: list[dict]) -> dict[str, float]:
    """US$ por unidade, por modelo: custo do Claude Code ÷ unidades das mesmas sessões (inclui subagentes)."""
    unid = defaultdict(lambda: defaultdict(float))
    for i in infos:
        for r in i["req"]:
            unid[i["sessao"]][r["modelo"]] += _unidades(r)
    custo, base = defaultdict(float), defaultdict(float)
    for i in infos:
        for modelo, usd in i["custo"].items():
            u = unid[i["sessao"]].get(modelo)
            if u and usd:
                custo[modelo] += usd
                base[modelo] += u
    precos = {m: custo[m] / base[m] for m in custo if base[m]}
    return precos


def _preco(modelo: str, precos: dict) -> float:
    if modelo in precos:
        return precos[modelo]
    for fam, p in PRECO_FAMILIA.items():
        if fam in (modelo or ""):
            return p / 1e6
    return 3.0 / 1e6


def _nome_modelo(m: str) -> str:
    m = (m or "?").replace("claude-", "")
    g = re.match(r"([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$", m)
    if g:
        return f"{g.group(1).capitalize()} {g.group(2)}{'.' + g.group(3) if g.group(3) else ''}"
    return m


def _pasta_legivel(info: dict) -> str:
    if info.get("cwd"):
        return info["cwd"]
    return info["projeto"]


def resumo_claude(periodo: str, chats_hub: dict) -> dict:
    agora = time.time()
    janela = 86400 if periodo == "dia" else 7 * 86400
    desde = agora - janela
    infos = _sessoes_claude(desde - 86400)  # arquivo pode ter sido tocado antes das requisições do período
    precos = _precos(infos)

    total = 0.0
    tokens = Counter()
    por_modelo: dict = defaultdict(lambda: {"custo": 0.0, "req": 0, "tokens": Counter()})
    por_origem = Counter()
    por_pasta = Counter()
    por_sub = Counter()
    por_fonte = Counter()
    contexto_grande = 0.0
    custo_sessao = Counter()
    custo_sub_sessao = Counter()
    sessoes: dict[str, dict] = {}
    pico_ctx = Counter()
    ferramentas = Counter()
    fonte_resultado = Counter()
    chamadas_fonte = Counter()
    n_req = 0
    passo = 3600 if periodo == "dia" else 86400
    inicio_linha = (int(desde) // passo + 1) * passo if periodo == "dia" else _inicio_dia(desde)
    linha = defaultdict(lambda: defaultdict(float))
    subagentes_sessao = defaultdict(set)

    for i in infos:
        considerou = False
        for r in i["req"]:
            if r["ts"] < desde:
                continue
            considerou = True
            n_req += 1
            custo = _unidades(r) * _preco(r["modelo"], precos)
            ctx = r["in"] + r["cr"] + r["cw5"] + r["cw1h"]
            total += custo
            for k in ("in", "cr", "cw5", "cw1h", "out"):
                tokens[k] += r[k]
            pm = por_modelo[r["modelo"]]
            pm["custo"] += custo
            pm["req"] += 1
            pm["tokens"].update({k: r[k] for k in ("in", "cr", "cw5", "cw1h", "out")})
            por_origem["Agent Hub" if i["entrypoint"] == "sdk-py" else "VS Code / terminal"] += custo
            por_pasta[_pasta_legivel(i)] += custo
            if ctx > CONTEXTO_GRANDE:
                contexto_grande += custo
            custo_sessao[i["sessao"]] += custo
            if i["sub"]:
                por_sub[i["tipo_agente"] or "outro"] += custo
                custo_sub_sessao[i["sessao"]] += custo
                subagentes_sessao[i["sessao"]].add(i["arquivo"])
            pico_ctx[i["sessao"]] = max(pico_ctx[i["sessao"]], ctx)
            if r.get("atrib") and ctx:
                soma = sum(r["atrib"].values())
                fator = min(1.0, ctx / soma) if soma else 0
                for fonte, n in r["atrib"].items():
                    por_fonte[fonte] += custo * min(1.0, n * fator / ctx)
            b = (int(r["ts"]) // passo) * passo if periodo == "dia" else _inicio_dia(r["ts"])
            linha[b][_nome_modelo(r["modelo"])] += custo
            s = sessoes.setdefault(i["sessao"], {"id": i["sessao"], "titulo": None, "origem": None, "pasta": None,
                                                 "req": 0, "ultimo": 0, "modelos": Counter(), "sub": 0})
            s["req"] += 1
            s["ultimo"] = max(s["ultimo"], r["ts"])
            s["modelos"][_nome_modelo(r["modelo"])] += custo
        if considerou:
            for nome, n in i["ferramentas"].items():
                ferramentas[nome] += n
            for fonte, n in i["tokens_resultado"].items():
                fonte_resultado[fonte] += n
        if considerou and not i["sub"]:
            s = sessoes[i["sessao"]]
            s["titulo"] = i["titulo"] or s["titulo"]
            s["origem"] = "Agent Hub" if i["entrypoint"] == "sdk-py" else "VS Code"
            s["pasta"] = _pasta_legivel(i)
    for i in infos:  # quantas vezes cada MCP/skill foi chamado (no período, aproximado pelo arquivo)
        if i["sessao"] in sessoes:
            for nome, n in i["ferramentas"].items():
                o = _origem_ferramenta(nome, {})
                if o and o.startswith("mcp:"):
                    chamadas_fonte[o] += n

    pct = (lambda v: round(100 * v / total, 1)) if total else (lambda v: 0)
    sessoes_pesadas = [sid for sid, c in custo_sub_sessao.items()
                       if custo_sessao[sid] and (c / custo_sessao[sid] >= 0.3 or len(subagentes_sessao[sid]) >= 3)]
    custo_pesadas = sum(custo_sessao[sid] for sid in sessoes_pesadas)

    insights = []
    if total:
        insights.append({"pct": pct(contexto_grande), "titulo": "do seu uso foi com mais de 150 mil tokens de contexto",
                         "dica": "Conversas longas custam mais a cada mensagem, mesmo com cache. Use /compact no meio "
                                 "de uma tarefa longa e abra um chat novo ao trocar de assunto."})
        if custo_pesadas:
            insights.append({"pct": pct(custo_pesadas), "titulo": "do seu uso veio de sessões com muitos subagentes",
                             "dica": "Cada subagente faz as próprias requisições. Chame subagentes só quando ajudar e "
                                     "considere um modelo mais barato (Sonnet/Haiku) para os mais simples."})
        for tipo, c in por_sub.most_common():
            if pct(c) >= 5:
                insights.append({"pct": pct(c), "titulo": f'do seu uso veio de subagentes "{tipo}"',
                                 "dica": "Se isso roda com frequência, configure esse subagente com um modelo mais "
                                         "barato ou deixe o prompt dele mais enxuto."})
        for fonte, c in por_fonte.most_common():
            if fonte.startswith("mcp:") and pct(c) >= 5:
                insights.append({"pct": pct(c), "titulo": f'do seu uso veio do servidor MCP "{fonte[4:]}"',
                                 "dica": "O resultado das ferramentas MCP fica no contexto até o fim da conversa. "
                                         "Use /compact para limpar ou peça consultas mais enxutas (menos linhas/colunas)."})
            elif fonte.startswith("skill:") and pct(c) >= 5:
                insights.append({"pct": pct(c), "titulo": f'do seu uso veio da skill "/{fonte[6:]}"',
                                 "dica": "O conteúdo da skill entra no contexto ao ser carregada e permanece na conversa."})
        caro = max(por_modelo.items(), key=lambda kv: kv[1]["custo"])
        if pct(caro[1]["custo"]) >= 50 and "opus" in caro[0]:
            insights.append({"pct": pct(caro[1]["custo"]), "titulo": f"do seu uso foi no {_nome_modelo(caro[0])}",
                             "dica": "Opus é o modelo que mais consome. Para tarefas simples (extrações, resumos), "
                                     "Sonnet ou Haiku gastam bem menos do limite."})
        insights.sort(key=lambda x: -x["pct"])

    entrada_total = tokens["in"] + tokens["cr"] + tokens["cw5"] + tokens["cw1h"]
    titulos_hub = {c.get("session_id"): c for c in chats_hub.values() if c.get("session_id")}
    top = []
    for sid, c in custo_sessao.most_common(15):
        s = sessoes.get(sid) or {}
        hub = titulos_hub.get(sid)
        top.append({"titulo": (hub or {}).get("title") or s.get("titulo") or "(sem título)",
                    "chat_id": (hub or {}).get("id"), "origem": s.get("origem") or "?", "pasta": s.get("pasta"),
                    "pct": pct(c), "custo": round(c, 2), "req": s.get("req", 0), "pico_ctx": pico_ctx[sid],
                    "ultimo": s.get("ultimo"), "modelos": [m for m, _ in s.get("modelos", Counter()).most_common()],
                    "subagentes": len(subagentes_sessao.get(sid, ())), "pct_sub": round(100 * custo_sub_sessao[sid] / c) if c else 0})

    buckets = []
    b = inicio_linha if periodo == "dia" else _inicio_dia(desde)
    while b <= agora:
        buckets.append({"t": b, "modelos": {m: round(v, 4) for m, v in linha.get(b, {}).items()}})
        b = b + passo if periodo == "dia" else _inicio_dia(b + 86400 + 3600)
    return {
        "total_usd": round(total, 2), "requisicoes": n_req, "sessoes": len([s for s in sessoes if s]),
        "tokens": dict(tokens), "cache_pct": round(100 * tokens["cr"] / entrada_total, 1) if entrada_total else 0,
        "insights": insights,
        "skills": [{"nome": f[6:], "pct": pct(c)} for f, c in por_fonte.most_common() if f.startswith("skill:")],
        "subagentes": [{"nome": t, "pct": pct(c), "custo": round(c, 2)} for t, c in por_sub.most_common()],
        "mcps": [{"nome": f[4:], "pct": pct(c), "chamadas": chamadas_fonte.get(f, 0),
                  "tokens_resultado": fonte_resultado.get(f, 0)} for f, c in por_fonte.most_common() if f.startswith("mcp:")],
        "modelos": [{"nome": _nome_modelo(m), "pct": pct(v["custo"]), "req": v["req"], "tokens": dict(v["tokens"])}
                    for m, v in sorted(por_modelo.items(), key=lambda kv: -kv[1]["custo"])],
        "origens": [{"nome": o, "pct": pct(c)} for o, c in por_origem.most_common()],
        "pastas": [{"nome": p, "pct": pct(c)} for p, c in por_pasta.most_common(10)],
        "top_sessoes": top,
        "ferramentas": [{"nome": _nome_ferramenta(n), "chamadas": q} for n, q in ferramentas.most_common(15)],
        "linha": buckets, "passo": "hora" if periodo == "dia" else "dia",
    }


def _inicio_dia(ts: float) -> float:
    d = datetime.fromtimestamp(ts)
    return datetime(d.year, d.month, d.day).timestamp()


def _nome_ferramenta(nome: str) -> str:
    if nome.startswith("mcp__"):
        p = nome.split("__")
        return f"{p[1]} · {p[2]}" if len(p) > 2 else nome
    return nome


# ============================================================================================ Codex
def _ler_codex(arquivo: Path) -> dict:
    info = {"thread": None, "cwd": None, "titulo": None, "eventos": [], "limites": None, "limites_ts": 0, "originador": None}
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", arquivo.name)
    info["thread"] = m.group(1) if m else arquivo.stem
    modelo = None
    with open(arquivo, encoding="utf-8", errors="replace") as f:
        for linha in f:
            if '"token_count"' not in linha and '"turn_context"' not in linha and '"session_meta"' not in linha \
                    and ('"role":"user"' not in linha or info["titulo"]):
                continue
            try:
                d = json.loads(linha)
            except ValueError:
                continue
            p = d.get("payload") or {}
            if d.get("type") == "session_meta":
                info["cwd"] = p.get("cwd")
                info["originador"] = p.get("originator")
            elif d.get("type") == "turn_context":
                modelo = p.get("model") or modelo
            elif p.get("type") == "message" and p.get("role") == "user" and not info["titulo"]:
                # 1ª mensagem do usuário (pula os blocos de contexto que o Codex injeta, como <environment_context>)
                for c in p.get("content") or []:
                    texto = (c.get("text") or "").strip() if isinstance(c, dict) else ""
                    if texto and not texto.startswith("<"):
                        texto = texto.split("[Arquivos anexados")[0].strip() or texto  # nota de anexos do hub
                        info["titulo"] = re.sub(r"\s+", " ", texto)[:80]
                        if texto.startswith("The following is the Codex agent history"):
                            info["titulo"] = "Tarefa interna do Codex (revisão/resumo automático)"
                        break
            elif p.get("type") == "token_count":
                ts = _ts(d.get("timestamp"))
                u = (p.get("info") or {}).get("last_token_usage")
                if u:
                    info["eventos"].append({"ts": ts, "modelo": modelo or "?", "in": u.get("input_tokens") or 0,
                                            "cached": u.get("cached_input_tokens") or 0,
                                            "out": u.get("output_tokens") or 0,
                                            "reasoning": u.get("reasoning_output_tokens") or 0})
                if p.get("rate_limits"):
                    info["limites"], info["limites_ts"] = p["rate_limits"], ts
    return info


def resumo_codex(periodo: str, chats_hub: dict) -> dict:
    agora = time.time()
    desde = agora - (86400 if periodo == "dia" else 7 * 86400)
    infos = []
    if CODEX_SESSIONS.is_dir():
        for f in CODEX_SESSIONS.glob("*/*/*/rollout-*.jsonl"):
            try:
                st = f.stat()
            except OSError:
                continue
            if st.st_mtime < desde - 86400:
                continue
            chave = (st.st_mtime, st.st_size)
            hit = _cache_codex.get(str(f))
            if not hit or hit[0] != chave:
                hit = (chave, _ler_codex(f))
                _cache_codex[str(f)] = hit
            infos.append(hit[1])
    limites, limites_ts = None, 0
    for i in infos:
        if i["limites"] and i["limites_ts"] > limites_ts:
            limites, limites_ts = i["limites"], i["limites_ts"]
    tokens = Counter()
    por_modelo = Counter()
    por_sessao = Counter()
    hub = {c.get("thread_id"): c for c in chats_hub.values() if c.get("thread_id")}
    nomes = _nomes_codex()
    meta = {}
    for i in infos:
        for e in i["eventos"]:
            if e["ts"] < desde:
                continue
            peso = (e["in"] - e["cached"]) + e["cached"] * 0.1 + e["out"] * 5
            tokens.update({k: e[k] for k in ("in", "cached", "out", "reasoning")})
            por_modelo[e["modelo"]] += peso
            por_sessao[i["thread"]] += peso
            meta[i["thread"]] = i
    total = sum(por_sessao.values())
    pct = (lambda v: round(100 * v / total, 1)) if total else (lambda v: 0)
    return {
        "limites": _limites_codex(limites, limites_ts),
        "tokens": dict(tokens), "sessoes": len(por_sessao),
        "modelos": [{"nome": m, "pct": pct(v)} for m, v in por_modelo.most_common()],
        "top_sessoes": [{"titulo": (hub.get(t) or {}).get("title") or nomes.get(t) or meta[t]["titulo"] or "(sem título)",
                         "chat_id": (hub.get(t) or {}).get("id"),
                         "origem": "Agent Hub" if t in hub or meta[t]["originador"] == "agent-hub" else "VS Code",
                         "pasta": meta[t]["cwd"], "pct": pct(v)} for t, v in por_sessao.most_common(10)],
    }


def limites_codex() -> dict | None:
    """Últimos limites do plano do Codex (das conversas mais recentes). Leve: lê só os arquivos mais novos."""
    if not CODEX_SESSIONS.is_dir():
        return None
    desde = time.time() - 8 * 86400
    arquivos = []
    for f in CODEX_SESSIONS.glob("*/*/*/rollout-*.jsonl"):
        try:
            mt = f.stat().st_mtime
        except OSError:
            continue
        if mt >= desde:
            arquivos.append((mt, f))
    melhor, melhor_ts = None, 0
    for _, f in sorted(arquivos, reverse=True)[:5]:
        st = f.stat()
        chave = (st.st_mtime, st.st_size)
        hit = _cache_codex.get(str(f))
        if not hit or hit[0] != chave:
            hit = (chave, _ler_codex(f))
            _cache_codex[str(f)] = hit
        if hit[1]["limites"] and hit[1]["limites_ts"] > melhor_ts:
            melhor, melhor_ts = hit[1]["limites"], hit[1]["limites_ts"]
    return _limites_codex(melhor, melhor_ts)


def _nomes_codex() -> dict[str, str]:
    """Títulos que o Codex dá às conversas (~/.codex/session_index.jsonl)."""
    nomes = {}
    try:
        with open(CODEX_SESSIONS.parent / "session_index.jsonl", encoding="utf-8") as f:
            for linha in f:
                try:
                    d = json.loads(linha)
                    nomes[d["id"]] = d.get("thread_name") or nomes.get(d["id"])
                except (ValueError, KeyError):
                    pass
    except OSError:
        pass
    return nomes


def _limites_codex(rl: dict | None, ts: float) -> dict | None:
    if not rl:
        return None
    janelas = []
    for chave in ("primary", "secondary"):
        j = rl.get(chave)
        if j:
            mins = j.get("window_minutes") or 0
            nome = "Semana" if mins >= 7 * 1440 else ("Sessão (5 h)" if mins == 300 else f"Janela de {mins // 60} h")
            janelas.append({"nome": nome, "pct": j.get("used_percent"), "reset": j.get("resets_at")})
    return {"janelas": janelas, "plano": rl.get("plan_type"), "visto_em": ts}


# ============================================================================================ limites Claude
def salvar_limite_claude(raw: dict) -> None:
    """Chamado a cada RateLimitEvent do SDK: guarda as janelas de uso do plano (5 h e 7 dias)."""
    if not raw:
        return
    dados = {"raw": raw, "visto_em": time.time()}
    try:
        LIMITES.parent.mkdir(parents=True, exist_ok=True)
        tmp = LIMITES.with_suffix(".tmp")
        tmp.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, LIMITES)
    except OSError:
        pass


def limites_claude() -> dict | None:
    try:
        dados = json.loads(LIMITES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    raw = dados.get("raw") or {}
    nomes = {"five_hour": "Sessão atual (5 h)", "seven_day": "Semana (todos os modelos)",
             "seven_day_opus": "Semana (Opus)", "seven_day_sonnet": "Semana (Sonnet)"}
    janelas = [{"nome": nomes.get(k, k), "pct": round(100 * (v.get("utilization") or 0)), "reset": v.get("resetsAt")}
               for k, v in (raw.get("unifiedWindows") or {}).items()]
    if not janelas and raw.get("rateLimitType"):
        janelas = [{"nome": nomes.get(raw["rateLimitType"], raw["rateLimitType"]),
                    "pct": round(100 * (raw.get("utilization") or 0)), "reset": raw.get("resetsAt")}]
    return {"janelas": janelas, "status": raw.get("status"), "extra": raw.get("overageStatus"),
            "extra_motivo": raw.get("overageDisabledReason"), "visto_em": dados.get("visto_em")}


async def atualizar_limites_claude() -> dict | None:
    """Pergunta rápida (Haiku, sem ferramentas, sem salvar sessão) só para receber os limites atuais."""
    from claude_agent_sdk import ClaudeAgentOptions, RateLimitEvent, query
    pasta = store.DATA_DIR / "tmp"
    pasta.mkdir(parents=True, exist_ok=True)
    opcoes = ClaudeAgentOptions(model="haiku", max_turns=1, tools=[], setting_sources=[], cwd=str(pasta),
                                extra_args={"no-session-persistence": None})
    async for msg in query(prompt="Responda apenas: ok", options=opcoes):
        if isinstance(msg, RateLimitEvent) and msg.rate_limit_info:
            salvar_limite_claude(msg.rate_limit_info.raw)
    return limites_claude()


def resumo(periodo: str) -> dict:
    chats = {c["id"]: c for c in store.chats.all()}
    t0 = time.time()
    claude = resumo_claude(periodo, chats)
    codex = resumo_codex(periodo, chats)
    return {"periodo": periodo, "gerado_em": time.time(), "duracao_s": round(time.time() - t0, 1),
            "claude": claude, "codex": codex, "limites_claude": limites_claude()}
