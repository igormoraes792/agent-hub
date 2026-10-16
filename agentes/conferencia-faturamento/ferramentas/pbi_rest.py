"""Consulta o modelo publicado do Power BI pela API REST (executeQueries), sem janela de login.

O acesso vem do Azure CLI (`az login` feito uma vez; ele renova sozinho). Limites da API por consulta:
100 mil linhas, 1 milhão de valores e 15 MB; 120 consultas por minuto.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

WORKSPACE = "0. Workspace PPU c/recursos especiais"
MODELO = "60000053 - Faturamento Negocios Global"
API = "https://api.powerbi.com/v1.0/myorg"
RECURSO = "https://analysis.windows.net/powerbi/api"
CACHE_IDS = Path(tempfile.gettempdir()) / "agent-hub" / "pbi_ids.json"  # fora da pasta do agente

_token: dict = {}


def _az() -> str:
    achado = shutil.which("az")
    if achado:
        return achado
    portatil = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "AzureCLI" / "bin" / "az.cmd"
    if portatil.exists():
        return str(portatil)
    raise SystemExit("Azure CLI (az) não encontrado. Instale e rode 'az login' uma vez.")


def token() -> str:
    if _token.get("valor") and _token.get("expira", 0) - time.time() > 300:
        return _token["valor"]
    r = subprocess.run([_az(), "account", "get-access-token", "--resource", RECURSO, "-o", "json"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise SystemExit("Não consegui o acesso ao Power BI pelo Azure CLI. Rode 'az login' de novo.\n" + r.stderr[-800:])
    dados = json.loads(r.stdout)
    _token.update(valor=dados["accessToken"], expira=float(dados.get("expires_on") or time.time() + 3000))
    return _token["valor"]


def _req(metodo: str, url: str, corpo: dict | None = None, tentativas: int = 4) -> dict:
    for i in range(tentativas):
        req = urllib.request.Request(url, method=metodo,
                                     data=json.dumps(corpo).encode("utf-8") if corpo is not None else None,
                                     headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                return json.loads(r.read().decode("utf-8-sig"))
        except urllib.error.HTTPError as e:
            texto = e.read().decode("utf-8", "replace")
            if e.code == 429 and i < tentativas - 1:  # limite de consultas por minuto
                time.sleep(int(e.headers.get("Retry-After", "20")))
                continue
            raise RuntimeError(f"Power BI respondeu {e.code}: {_erro_legivel(texto)}") from None
    raise RuntimeError("Power BI: muitas tentativas.")


def _erro_legivel(texto: str) -> str:
    try:
        e = json.loads(texto).get("error", {})
        det = (e.get("pbi.error") or {}).get("details") or []
        msgs = [d.get("detail", {}).get("value") for d in det if d.get("detail")]
        return " | ".join(m for m in msgs if m) or e.get("message") or texto[:1500]
    except (json.JSONDecodeError, AttributeError):
        return texto[:1500]


def ids(workspace: str = WORKSPACE, modelo: str = MODELO) -> tuple[str, str]:
    """IDs do workspace e do modelo (guardados em cache depois da primeira busca)."""
    try:
        cache = json.loads(CACHE_IDS.read_text(encoding="utf-8"))
        if cache.get("workspace") == workspace and cache.get("modelo") == modelo:
            return cache["workspace_id"], cache["modelo_id"]
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    grupos = _req("GET", f"{API}/groups?$top=5000")["value"]
    g = next((x for x in grupos if x["name"] == workspace), None)
    if not g:
        raise SystemExit(f"Workspace '{workspace}' não encontrado para esta conta.")
    modelos = _req("GET", f"{API}/groups/{g['id']}/datasets")["value"]
    m = next((x for x in modelos if x["name"] == modelo), None)
    if not m:
        raise SystemExit(f"Modelo '{modelo}' não encontrado no workspace.")
    CACHE_IDS.parent.mkdir(parents=True, exist_ok=True)
    CACHE_IDS.write_text(json.dumps({"workspace": workspace, "modelo": modelo, "workspace_id": g["id"],
                                     "modelo_id": m["id"]}, ensure_ascii=False), encoding="utf-8")
    return g["id"], m["id"]


def nome_coluna(chave: str) -> str:
    """'[valor]' -> 'valor'; 'TABELA[COLUNA]' -> 'COLUNA'."""
    if chave.endswith("]") and "[" in chave:
        return chave[chave.rindex("[") + 1:-1]
    return chave


def executar(dax: str, workspace: str = WORKSPACE, modelo: str = MODELO) -> tuple[list[str], list[list]]:
    """Roda uma consulta DAX e devolve (colunas, linhas)."""
    g, m = ids(workspace, modelo)
    res = _req("POST", f"{API}/groups/{g}/datasets/{m}/executeQueries",
               {"queries": [{"query": dax}], "serializerSettings": {"includeNulls": True}})
    r0 = (res.get("results") or [{}])[0]
    if r0.get("error"):
        raise RuntimeError(f"Erro no DAX: {json.dumps(r0['error'], ensure_ascii=False)[:1500]}")
    tabelas = r0.get("tables") or [{}]
    linhas = tabelas[0].get("rows") or []
    if not linhas:
        return [], []
    chaves = list(linhas[0].keys())
    return [nome_coluna(c) for c in chaves], [[l.get(c) for c in chaves] for l in linhas]
