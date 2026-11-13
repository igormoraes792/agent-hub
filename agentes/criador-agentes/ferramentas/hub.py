"""Acesso do Criador de Agentes à API local do Agent Hub (http://127.0.0.1:8787).

Uso (a partir da pasta do agente):  python -I -X utf8 ferramentas/hub.py <comando> [opções]

  meta                         modelos, esforços e modos de permissão de cada IA (claude, codex, antigravity)
  mcp                          servidores MCP configurados e se estão funcionando
  ferramentas-mcp SERVIDOR     nomes das ferramentas de um MCP (para montar tools / disallowed_tools)
                               [--pasta PASTA_DO_AGENTE] lê também o .mcp.json da pasta (ex.: dremio)
  agentes                      lista os agentes (id, nome, IA, modelo, pasta)
  ver ID                       configuração completa de um agente (JSON)
  criar --arquivo agente.json  cadastra um agente novo (sem "cwd", usa a pasta do arquivo)
  atualizar ID --arquivo X     troca só os campos presentes no arquivo (faz backup de data/agents.json antes)
  exportar ID [--arquivo X]    grava a configuração atual em JSON (padrão: <pasta do agente>/agente.json)
  exportar-claude ID           grava o agente como subagente do Claude Code (~/.claude/agents/<nome>.md)
  apagar ID --confirmo         remove o agente do hub (backup antes; a pasta do agente não é apagada)
  testar ID "mensagem" [--espera 300] [--apagar]
                               abre um chat com o agente, envia a mensagem e mostra a resposta
  chats [--agente ID]          lista os chats (id, título, agente, data)

Saída em JSON ou texto no stdout. Nunca imprime credenciais.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

URL = f"http://127.0.0.1:{os.environ.get('AGENT_HUB_PORT', '8787')}"
HUB = Path(__file__).resolve().parents[3]
CAMPOS = ("name", "description", "provider", "model", "effort", "prompt", "welcome", "tools", "disallowed_tools",
          "permission_mode", "sandbox", "approval_policy", "cwd", "subagents", "color")


def api(metodo: str, caminho: str, corpo: dict | None = None, timeout: float = 60):
    dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8") if corpo is not None else None
    req = urllib.request.Request(URL + caminho, data=dados, method=metodo,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        sys.exit(f"ERRO {e.code} em {metodo} {caminho}: {e.read().decode('utf-8', 'replace')[:500]}")
    except urllib.error.URLError as e:
        sys.exit(f"ERRO: o Agent Hub não respondeu em {URL} ({e.reason}). Ele está aberto?")


def mostrar(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def backup_agentes():
    origem = HUB / "data" / "agents.json"
    destino = origem.with_name(f"agents.json.bak-{datetime.datetime.now():%Y-%m-%d-%H%M%S}")
    shutil.copy2(origem, destino)
    print(f"backup: {destino}", file=sys.stderr)


def ler_arquivo(caminho: str) -> dict:
    p = Path(caminho).resolve()
    dados = json.loads(p.read_text(encoding="utf-8"))
    desconhecidos = sorted(set(dados) - set(CAMPOS) - {"id", "created_at", "updated_at"})
    if desconhecidos:
        sys.exit(f"ERRO: campos desconhecidos no arquivo: {desconhecidos}. Válidos: {list(CAMPOS)}")
    dados = {k: v for k, v in dados.items() if k in CAMPOS}
    if "cwd" in dados and dados["cwd"]:
        dados["cwd"] = str(Path(dados["cwd"]))  # aceita barras normais; grava no formato do Windows
    return dados, p


def agente(id_: str) -> dict:
    for a in api("GET", "/api/agents"):
        if a["id"] == id_:
            return a
    sys.exit(f"ERRO: agente {id_} não encontrado. Use o comando 'agentes' para ver os ids.")


# --- MCP: listar ferramentas de um servidor -------------------------------------------------------
def config_mcp(nome: str, pasta: str | None) -> dict:
    fontes = []
    if pasta:
        fontes.append(("pasta", Path(pasta) / ".mcp.json"))
    fontes.append(("claude", Path.home() / ".claude.json"))
    for tipo, arq in fontes:
        try:
            servidores = json.loads(arq.read_text(encoding="utf-8")).get("mcpServers") or {}
        except (OSError, json.JSONDecodeError):
            continue
        if nome in servidores:
            return servidores[nome]
    try:
        cx = tomllib.loads((Path.home() / ".codex" / "config.toml").read_text(encoding="utf-8"))
        if nome in (cx.get("mcp_servers") or {}):
            return cx["mcp_servers"][nome]
    except (OSError, tomllib.TOMLDecodeError):
        pass
    sys.exit(f"ERRO: servidor MCP '{nome}' não encontrado (pasta do agente, ~/.claude.json, ~/.codex/config.toml).")


def ferramentas_mcp(nome: str, pasta: str | None) -> list[dict]:
    cfg = config_mcp(nome, pasta)
    proc = subprocess.Popen([cfg["command"], *(cfg.get("args") or [])], env={**os.environ, **(cfg.get("env") or {})},
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    fila: queue.Queue = queue.Queue()

    def ler():
        for linha in proc.stdout:
            try:
                fila.put(json.loads(linha))
            except json.JSONDecodeError:
                continue
        fila.put(None)

    threading.Thread(target=ler, daemon=True).start()

    def enviar(msg):
        proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def pedido(id_, metodo, params):
        enviar({"jsonrpc": "2.0", "id": id_, "method": metodo, "params": params})
        while True:
            msg = fila.get(timeout=120)
            if msg is None:
                sys.exit(f"ERRO: o servidor MCP '{nome}' encerrou sem responder.")
            if "method" in msg and "id" in msg:
                enviar({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "não suportado"}})
            elif msg.get("id") == id_:
                return msg.get("result") or {}

    try:
        pedido(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "criador-agentes", "version": "1.0"}})
        enviar({"jsonrpc": "2.0", "method": "notifications/initialized"})
        ferramentas, cursor, n = [], None, 2
        while True:
            res = pedido(n, "tools/list", {"cursor": cursor} if cursor else {})
            ferramentas += res.get("tools") or []
            cursor, n = res.get("nextCursor"), n + 1
            if not cursor:
                break
        return ferramentas
    finally:
        proc.terminate()


# --- comandos -------------------------------------------------------------------------------------
def cmd_meta(_):
    m = api("GET", "/api/meta")
    resumo = {}
    for ia in ("claude", "codex", "antigravity"):
        d = m.get(ia) or {}
        resumo[ia] = {
            "disponivel": d.get("available"),
            "modelos": [{"valor": x["value"], "nome": x.get("displayName"), "descricao": x.get("description")}
                        for x in d.get("models") or []],
            "modelo_padrao": d.get("defaultModel"),
            "esforcos": d.get("effortLevels"),
            "modos_permissao": d.get("permissionModes"),
            "sandbox": d.get("sandboxModes"),
        }
    resumo["claude"]["subagentes_nativos"] = [a["name"] for a in (m.get("claude") or {}).get("agents") or []]
    mostrar(resumo)


def cmd_mcp(_):
    mostrar([{k: s.get(k) for k in ("ia", "name", "scope", "status", "detail")} for s in api("GET", "/api/mcp")["servers"]])


def cmd_ferramentas_mcp(a):
    for t in ferramentas_mcp(a.servidor, a.pasta):
        desc = " ".join((t.get("description") or "").split())[:110]
        print(f"mcp__{a.servidor}__{t['name']}\t{desc}")


def cmd_agentes(_):
    for a in api("GET", "/api/agents"):
        print(f"{a['id']}  {a['name']}  |  {a.get('provider')} {a.get('model') or ''} {a.get('effort') or ''}  |  {a.get('cwd') or ''}")


def cmd_ver(a):
    mostrar(agente(a.id))


def cmd_criar(a):
    dados, arq = ler_arquivo(a.arquivo)
    if not dados.get("name"):
        sys.exit("ERRO: o arquivo precisa do campo 'name'.")
    if any(x["name"].lower() == dados["name"].lower() for x in api("GET", "/api/agents")):
        sys.exit(f"ERRO: já existe um agente chamado '{dados['name']}'. Use 'atualizar' ou outro nome.")
    dados.setdefault("cwd", str(arq.parent))
    backup_agentes()
    mostrar(api("POST", "/api/agents", dados))


def cmd_atualizar(a):
    dados, _ = ler_arquivo(a.arquivo)
    atual = agente(a.id)
    backup_agentes()
    mostrar(api("PUT", f"/api/agents/{a.id}", {**atual, **dados}))


def cmd_exportar(a):
    atual = {k: v for k, v in agente(a.id).items() if k in CAMPOS}
    destino = Path(a.arquivo) if a.arquivo else Path(atual.get("cwd") or ".") / "agente.json"
    destino.write_text(json.dumps(atual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"gravado: {destino}")


def cmd_exportar_claude(a):
    mostrar(api("POST", f"/api/agents/{a.id}/export-claude"))


def cmd_apagar(a):
    if not a.confirmo:
        sys.exit("ERRO: apagar exige --confirmo (e a confirmação explícita do usuário no chat).")
    nome = agente(a.id)["name"]
    backup_agentes()
    api("DELETE", f"/api/agents/{a.id}")
    print(f"agente '{nome}' ({a.id}) removido do hub. A pasta dele continua no disco.")


def cmd_testar(a):
    ag = agente(a.id)
    chat = api("POST", "/api/chats", {"agent_id": ag["id"], "title": f"[teste] {ag['name']}"})
    cid = chat["id"]
    print(f"chat de teste: {cid}", file=sys.stderr)
    api("POST", f"/api/chats/{cid}/send", {"text": a.mensagem})
    fim, visto = time.time() + a.espera, 0
    resultado = None
    while time.time() < fim:
        time.sleep(3)
        d = api("GET", f"/api/chats/{cid}/events")
        eventos = d.get("events") or []
        for e in eventos[visto:]:
            k = e.get("kind")
            if k == "assistant" and not e.get("greeting"):
                print(e.get("text") or "")
            elif k == "tool":
                print(f"[ferramenta] {e.get('name') or e.get('tool') or ''}", file=sys.stderr)
            elif k in ("result", "error"):
                resultado = e
        visto = len(eventos)
        if d.get("pending"):
            print(f"[parado] o agente pediu aprovação ({len(d['pending'])} pedido(s)). Aprove no hub, no chat {cid}.",
                  file=sys.stderr)
            break
        if resultado and (d.get("chat") or {}).get("status") == "idle":
            break
    if resultado:
        info = {k: resultado.get(k) for k in ("is_error", "cost_usd", "duration_ms", "text") if k in resultado}
        print(f"[fim] {json.dumps(info, ensure_ascii=False)}", file=sys.stderr)
    elif not (d.get("pending")):
        print(f"[tempo esgotado] sem resposta final em {a.espera}s; o chat {cid} continua aberto no hub.", file=sys.stderr)
    if a.apagar and resultado:
        api("DELETE", f"/api/chats/{cid}")
        print("chat de teste apagado.", file=sys.stderr)


def cmd_chats(a):
    for c in api("GET", "/api/chats"):
        if a.agente and c.get("agent_id") != a.agente:
            continue
        quando = datetime.datetime.fromtimestamp(c.get("updated_at") or 0).strftime("%d/%m %H:%M")
        print(f"{c['id']}  {quando}  {c.get('provider')}  agente={c.get('agent_id') or '-'}  {c.get('title')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="comando", required=True)
    sub.add_parser("meta").set_defaults(f=cmd_meta)
    sub.add_parser("mcp").set_defaults(f=cmd_mcp)
    p = sub.add_parser("ferramentas-mcp"); p.add_argument("servidor"); p.add_argument("--pasta"); p.set_defaults(f=cmd_ferramentas_mcp)
    sub.add_parser("agentes").set_defaults(f=cmd_agentes)
    p = sub.add_parser("ver"); p.add_argument("id"); p.set_defaults(f=cmd_ver)
    p = sub.add_parser("criar"); p.add_argument("--arquivo", required=True); p.set_defaults(f=cmd_criar)
    p = sub.add_parser("atualizar"); p.add_argument("id"); p.add_argument("--arquivo", required=True); p.set_defaults(f=cmd_atualizar)
    p = sub.add_parser("exportar"); p.add_argument("id"); p.add_argument("--arquivo"); p.set_defaults(f=cmd_exportar)
    p = sub.add_parser("exportar-claude"); p.add_argument("id"); p.set_defaults(f=cmd_exportar_claude)
    p = sub.add_parser("apagar"); p.add_argument("id"); p.add_argument("--confirmo", action="store_true"); p.set_defaults(f=cmd_apagar)
    p = sub.add_parser("testar"); p.add_argument("id"); p.add_argument("mensagem")
    p.add_argument("--espera", type=int, default=300); p.add_argument("--apagar", action="store_true"); p.set_defaults(f=cmd_testar)
    p = sub.add_parser("chats"); p.add_argument("--agente"); p.set_defaults(f=cmd_chats)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
