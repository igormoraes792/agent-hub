"""Agent Hub — central local de chats e agentes para Claude e Codex.

Uso:  .venv\\Scripts\\python.exe server.py [--port 8787] [--no-browser]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path


def _atualizar_path() -> None:
    """Acrescenta ao PATH o que está no registro do Windows (usuário + máquina). O servidor herda o ambiente de quem o
    abriu, e o reinício pela tela copia o do processo anterior: sem isto, um programa instalado depois (ex.: Node.js
    para um MCP) só seria encontrado fechando o hub e o VS Code."""
    if sys.platform != "win32":
        return
    import winreg
    atuais = os.environ.get("PATH", "").split(os.pathsep)
    vistos = {p.rstrip("\\").lower() for p in atuais if p}
    novos = []
    for raiz, chave in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                        (winreg.HKEY_CURRENT_USER, "Environment")):
        try:
            with winreg.OpenKey(raiz, chave) as k:
                valor = winreg.QueryValueEx(k, "Path")[0]
        except OSError:
            continue
        for p in os.path.expandvars(valor).split(os.pathsep):
            if p and p.rstrip("\\").lower() not in vistos:
                vistos.add(p.rstrip("\\").lower())
                novos.append(p)
    if novos:
        os.environ["PATH"] = os.pathsep.join([*atuais, *novos])
    # variáveis do usuário criadas depois que o hub abriu (ex.: desligar telemetria de um plugin): só acrescenta
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            for i in range(winreg.QueryInfoKey(k)[1]):
                nome, valor, _tipo = winreg.EnumValue(k, i)
                if nome.lower() != "path" and nome not in os.environ and isinstance(valor, str):
                    os.environ[nome] = os.path.expandvars(valor)
    except OSError:
        pass


_atualizar_path()

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from hub import accounts, busca, consumo, icone, importer, mcp_health, office, providers, store
from hub.janela import abrir_janela
from hub.sessions import AGENT_CONTROLLED, bus, codex_server, effective_chat, manager

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("AGENT_HUB_PORT", "8787"))
ALLOWED_ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}

CHAT_FIELDS = ("title", "model", "effort", "permission_mode", "sandbox", "approval_policy", "cwd", "agent_id", "pinned", "archived")
AGENT_FIELDS = ("name", "description", "provider", "model", "effort", "prompt", "welcome", "tools", "disallowed_tools",
                "permission_mode", "sandbox", "approval_policy", "cwd", "subagents", "color")


def _ignore_dropped_connections(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """No Windows, quando o navegador some sem se despedir (aba fechada, PC suspenso), o asyncio imprime
    ConnectionResetError [WinError 10054]. É só uma desconexão normal; os demais erros seguem sendo mostrados."""
    if isinstance(context.get("exception"), (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return
    loop.default_exception_handler(context)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    accounts.carregar_chaves_ambiente()
    asyncio.get_running_loop().set_exception_handler(_ignore_dropped_connections)
    providers.claude_meta.refresh_in_background()
    reaper = asyncio.create_task(manager.reap_idle())
    asyncio.get_running_loop().run_in_executor(None, consumo.resumo, "semana")  # deixa a aba Consumo pronta
    vigia = asyncio.create_task(_desligar_sem_janelas()) if getattr(_app.state, "sozinho", False) else None
    yield
    reaper.cancel()
    if vigia:
        vigia.cancel()
    await manager.close_all()


JANELAS = {"abertas": 0}  # janelas/abas do hub conectadas agora
INICIO = time.time()  # identifica esta execução do servidor (a tela recarrega quando muda)


def _chats_ocupados() -> list[str]:
    ocupados = []
    for cid, s in manager.sessions.items():
        if s.status in ("running", "waiting") or getattr(s, "tasks_running", None):
            ocupados.append((store.chats.get(cid) or {}).get("title") or cid)
    return ocupados


async def _desligar_sem_janelas() -> None:
    """Modo janela única (atalho "Agent Hub"): o servidor se desliga sozinho quando a última janela fecha.
    Se algum chat ainda estiver trabalhando, espera ele terminar antes de desligar."""
    ocioso_desde = time.time() + 45  # tempo para a primeira janela abrir
    while True:
        await asyncio.sleep(3)
        if JANELAS["abertas"] or _chats_ocupados():
            ocioso_desde = time.time()
        elif time.time() - ocioso_desde > 15:  # folga para recarregar a página (F5) sem derrubar o servidor
            print("Nenhuma janela aberta: desligando o servidor.", flush=True)
            _garantir_saida()
            app.state.servidor.should_exit = True
            return


def _garantir_saida(prazo: float = 20) -> None:
    """Se o processo não terminar sozinho no prazo (algo travado no encerramento), registra onde travou e sai.
    Sem isso, um servidor "fantasma" ficava vivo sem atender a porta."""
    import faulthandler
    import threading

    def forcar():
        time.sleep(prazo)
        print(f"O servidor não terminou em {prazo:.0f} s; onde travou:", flush=True)
        faulthandler.dump_traceback(file=sys.stdout, all_threads=True)
        sys.stdout.flush()
        os._exit(0)

    threading.Thread(target=forcar, daemon=True).start()


app = FastAPI(title="Agent Hub", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.middleware("http")
async def only_local_origins(request: Request, call_next):
    """Bloqueia sites externos de usarem a API local (CSRF): pedidos de escrita precisam vir desta página."""
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin not in ALLOWED_ORIGINS:
        return JSONResponse({"detail": "origem não permitida"}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        # Sempre confere com o servidor: depois de uma atualização a tela nova aparece sem precisar de Ctrl+F5.
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-store"})


# --- servidor ------------------------------------------------------------------------------------
@app.get("/api/servidor")
async def servidor_info():
    return {"inicio": INICIO, "pid": os.getpid(), "ocupados": _chats_ocupados()}


@app.post("/api/servidor/reiniciar")
async def servidor_reiniciar(body: dict):
    """Liga um servidor novo (com o código atualizado) e desliga este. A tela recarrega sozinha."""
    import subprocess
    ocupados = _chats_ocupados()
    if ocupados and not body.get("forcar"):
        raise HTTPException(409, "Estes chats estão trabalhando e serão interrompidos: " + "; ".join(ocupados))
    args = list(sys.argv[1:])
    if "--no-browser" not in args:
        args.append("--no-browser")  # a janela que já está aberta reconecta; não abre outra
    subprocess.Popen([sys.executable, "-u", str(ROOT / "server.py"), *args], cwd=ROOT,
                     env={**os.environ, "AGENT_HUB_REINICIO": "1"}, stdin=subprocess.DEVNULL,
                     stdout=sys.stdout, stderr=sys.stderr)
    print("Reiniciando o servidor (pedido pela tela Ambiente).", flush=True)

    def sair():
        _garantir_saida()
        app.state.servidor.should_exit = True

    asyncio.get_running_loop().call_later(0.5, sair)
    return {"ok": True, "inicio": INICIO}


# --- consumo -------------------------------------------------------------------------------------
@app.get("/api/consumo")
async def consumo_resumo(periodo: str = "dia"):
    return await asyncio.to_thread(consumo.resumo, "semana" if periodo == "semana" else "dia")


@app.get("/api/consumo/limites")
async def consumo_limites_salvos():
    return {
        "limites": consumo.limites_claude(),
        "codex": await asyncio.to_thread(consumo.limites_codex),
        "antigravity": consumo.limites_antigravity(),
    }


@app.post("/api/consumo/limites")
async def consumo_limites():
    """Consulta os limites do plano do Claude agora (uma pergunta mínima ao Haiku)."""
    try:
        return {"limites": await asyncio.wait_for(consumo.atualizar_limites_claude(), 90)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Não consegui consultar os limites: {e}") from None


# --- meta ---------------------------------------------------------------------------------------
@app.get("/api/meta")
async def get_meta():
    meta = providers.meta()
    try:
        meta["mcpIssues"] = len(await asyncio.to_thread(mcp_health.issues))
    except Exception:  # noqa: BLE001 - a checagem nunca deve impedir o app de abrir
        meta["mcpIssues"] = 0
    return meta


# --- contas de IA -------------------------------------------------------------------------------
@app.get("/api/accounts")
async def accounts_list():
    meta = providers.meta()
    # em thread: o status do Claude roda `claude auth status`, que leva um ou dois segundos
    return {"accounts": await asyncio.to_thread(accounts.get_all_accounts, meta)}


@app.post("/api/accounts/key")
async def accounts_set_key(body: dict):
    provider = body.get("provider")
    key = body.get("key")
    label = body.get("label")
    if provider not in ("claude", "codex", "antigravity"):
        raise HTTPException(400, "Provedor inválido.")
    accounts.set_account_key(provider, key, label)
    meta = providers.meta()
    if provider == "claude":
        acc = accounts.get_claude_account(meta)
    elif provider == "antigravity":
        acc = accounts.get_antigravity_account(meta)
    else:
        acc = accounts.get_codex_account(meta)
    return {"status": "ok", "account": acc}


@app.post("/api/aparencia/icone")
async def aparencia_icone(body: dict):
    """Ícone do hub (atalhos, barra de tarefas, área de trabalho, janela) na cor de destaque escolhida."""
    return await asyncio.to_thread(icone.aplicar, body.get("cor"))


@app.get("/api/icone/{tipo}")
async def icone_atual(tipo: str, cor: str | None = None):
    if tipo not in ("ico", "png32", "png256"):
        raise HTTPException(404)
    arquivo = (await asyncio.to_thread(icone.arquivos, cor or icone.cor_atual()))[tipo]
    return FileResponse(arquivo, headers={"Cache-Control": "no-cache"})


@app.post("/api/padroes")
async def salvar_padroes(body: dict):
    """Padrões dos chats novos: {"provider": ..., "valores": {model, effort, ...}} ou {"cwd": pasta}.
    Não muda chats já abertos nem agentes, que têm configuração própria."""
    try:
        if "cwd" in body:
            return accounts.definir_padroes(None, {"cwd": body.get("cwd")})
        return accounts.definir_padroes(body.get("provider"), body.get("valores") or {})
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/accounts/login")
async def accounts_login(body: dict):
    provider = body.get("provider")
    try:
        return accounts.iniciar_login(provider)
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/accounts/logout")
async def accounts_logout(body: dict):
    provider = body.get("provider")
    try:
        res = accounts.fazer_logout(provider)
        if provider == "codex" and codex_server.proc and codex_server.proc.returncode is None:
            try:
                codex_server.proc.terminate()
            except Exception:
                pass
            codex_server.proc = None
        return res
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/accounts/switch-profile")
async def accounts_switch(body: dict):
    provider = body.get("provider")
    profile_id = body.get("profile_id")
    try:
        res = accounts.switch_profile(provider, profile_id)
        if provider == "codex" and codex_server.proc and codex_server.proc.returncode is None:
            try:
                codex_server.proc.terminate()
            except Exception:
                pass
            codex_server.proc = None
        return res
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/api/accounts/profiles/{provider}/{profile_id}")
async def accounts_del_profile(provider: str, profile_id: str):
    return accounts.delete_profile(provider, profile_id)


# --- servidores MCP -----------------------------------------------------------------------------
@app.get("/api/mcp")
async def mcp_list():
    return {"servers": await asyncio.to_thread(mcp_health.collect)}


@app.post("/api/mcp/fix")
async def mcp_fix(body: dict):
    try:
        result = await asyncio.to_thread(mcp_health.fix, body.get("ia"), body.get("scope"), body.get("name"),
                                         body.get("project"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)) from e
    if body.get("ia") == "codex" and codex_server.proc and codex_server.proc.returncode is None:
        try:
            await codex_server.request("config/mcpServer/reload", {}, timeout=30)
        except Exception:  # noqa: BLE001 - chats novos já pegam a configuração nova
            pass
    return result


# --- importar conversas -------------------------------------------------------------------------
@app.get("/api/import/{provider}")
async def import_list(provider: str):
    try:
        if provider == "claude":
            return await asyncio.to_thread(importer.list_claude)
        if provider == "codex":
            return await importer.list_codex()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Não consegui listar as conversas: {e}") from e
    raise HTTPException(404)


@app.post("/api/import/{provider}/{source_id}")
async def import_one(provider: str, source_id: str):
    try:
        if provider == "claude":
            chat = await asyncio.to_thread(importer.import_claude, source_id)
        elif provider == "codex":
            chat = await importer.import_codex(source_id)
        else:
            raise HTTPException(404)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Não consegui importar: {e}") from e
    bus.publish({"type": "chat_updated", "chat": _chat_out(chat)})
    return _chat_out(chat)


@app.post("/api/meta/refresh")
async def refresh_meta():
    providers.claude_meta.refresh_in_background()
    return {"ok": True}


@app.get("/api/codex/sandbox")
async def codex_sandbox_status():
    try:
        res = await codex_server.request("windowsSandbox/readiness", {}, timeout=30)
        return {"status": res.get("status")}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


@app.post("/api/codex/sandbox/setup")
async def codex_sandbox_setup(body: dict):
    mode = body.get("mode") if body.get("mode") in ("elevated", "unelevated") else "unelevated"
    try:
        res = await codex_server.request("windowsSandbox/setupStart", {"mode": mode}, timeout=60)
        return {"started": res.get("started", False)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e)) from e


_PICK_FOLDER_SCRIPT = r"""
import sys, tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
root.lift(); root.focus_force()
path = filedialog.askdirectory(parent=root, initialdir=sys.argv[1] or None, title=sys.argv[2], mustexist=True)
# UTF-8 explícito: no Windows o stdout redirecionado usa a página de código antiga (cp1252) e "Ações" chegava
# ao servidor como "A��es", uma pasta que não existe (WinError 267 ao abrir o chat)
sys.stdout.buffer.write((path or "").encode("utf-8"))
"""
_pick_lock = asyncio.Lock()


@app.post("/api/fs/pick-folder")
async def pick_folder(body: dict):
    """Abre a janela 'Selecionar pasta' do Windows (o servidor roda na mesma máquina) e devolve o caminho."""
    if _pick_lock.locked():
        raise HTTPException(409, "Já existe uma janela de seleção de pasta aberta.")
    initial = body.get("initial") or ""
    if not os.path.isdir(initial):
        initial = str(Path.home())
    async with _pick_lock:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-c", _PICK_FOLDER_SCRIPT, initial, body.get("title") or "Escolha a pasta de trabalho",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=900)
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(408, "A janela de seleção ficou aberta tempo demais.")
    if proc.returncode != 0:
        raise HTTPException(500, f"Não consegui abrir a janela de pastas: {err.decode('utf-8', 'replace')[-300:]}")
    path = out.decode("utf-8", "replace").strip()
    if path and not os.path.isdir(path):
        raise HTTPException(500, f"A pasta escolhida não foi reconhecida ({path}). Tente de novo ou digite o caminho.")
    return {"path": os.path.normpath(path) if path else None}


# --- chats --------------------------------------------------------------------------------------
def _chat_out(chat: dict) -> dict:
    session = manager.sessions.get(chat["id"])
    return {**effective_chat(chat), "status": session.status if session else "idle",
            "tasks": session.background_tasks() if session else []}


@app.get("/api/chats")
async def list_chats():
    chats = sorted(store.chats.all(), key=lambda c: c.get("updated_at", 0), reverse=True)
    return [_chat_out(c) for c in chats]


@app.get("/api/busca")
async def buscar_conteudo(q: str = ""):
    """Busca no texto das conversas; só roda quando o usuário liga a busca no conteúdo."""
    q = q.strip()[:200]
    if len(q) < 2:
        return {"termos": [], "chats": {}}
    ids = [c["id"] for c in store.chats.all()]
    return await asyncio.to_thread(busca.buscar, q, ids)


@app.post("/api/chats")
async def create_chat(body: dict):
    agent = store.agents.get(body["agent_id"]) if body.get("agent_id") else None
    provider = (agent or {}).get("provider") or body.get("provider") or "claude"
    if provider not in ("claude", "codex", "antigravity"):
        raise HTTPException(400, "provider inválido")

    def pick(field, default=None):
        value = body.get(field)
        if value in (None, "") and agent:
            value = agent.get(field)
        return value if value not in (None, "") else default

    padroes = accounts.padroes_chat()
    meus = padroes.get(provider, {})  # padrões dos chats novos desta IA (Configurações → Contas)
    default_model = meus.get("model") or (
        "default" if provider == "claude" else ("gemini-3.8-flash-high" if provider == "antigravity" else None))
    default_perm = meus.get("permission_mode") or (
        "default" if provider == "claude" else ("auto" if provider == "antigravity" else "default"))
    cwd = pick("cwd", padroes.get("cwd") or str(Path.home()))
    if not os.path.isdir(cwd):
        onde = "Edite o agente e escolha a pasta de novo." if agent and not body.get("cwd") else "Escolha outra pasta."
        raise HTTPException(400, f"A pasta de trabalho não existe: {cwd}. {onde}")

    chat = store.chats.create({
        "title": body.get("title") or (f"{agent['name']}" if agent else "Novo chat"),
        "provider": provider,
        "agent_id": agent["id"] if agent else None,
        "model": pick("model", default_model),
        "effort": pick("effort", meus.get("effort")),
        "cwd": cwd,
        "permission_mode": pick("permission_mode", default_perm),
        "sandbox": pick("sandbox", meus.get("sandbox") or "workspace-write"),
        "approval_policy": pick("approval_policy", meus.get("approval_policy") or "on-request"),
        "session_id": None,
        "thread_id": None,
        "conversation_id": None,
        "auto_title": not body.get("title"),
        # Mensagem de boas-vindas que o usuário viu antes de escrever (entra no histórico e no contexto da IA).
        "greeting": (body.get("greeting") or "").strip() or None,
    })
    if chat["greeting"]:
        store.append_event(chat["id"], {"kind": "assistant", "text": chat["greeting"], "greeting": True})
    bus.publish({"type": "chat_updated", "chat": _chat_out(chat)})
    return _chat_out(chat)


@app.patch("/api/chats/{chat_id}")
async def update_chat(chat_id: str, body: dict):
    chat = store.chats.get(chat_id)
    if not chat:
        raise HTTPException(404)
    changes = {k: v for k, v in body.items() if k in CHAT_FIELDS}
    if chat.get("agent_id"):  # em chats de agente, modelo e permissões só mudam no próprio agente
        changes = {k: v for k, v in changes.items() if k not in AGENT_CONTROLLED}
    started = chat.get("session_id") or chat.get("thread_id")
    if "cwd" in changes and started and changes["cwd"] != chat.get("cwd"):
        raise HTTPException(400, "A pasta não pode mudar depois que a conversa começou. Crie um novo chat.")
    if "title" in changes:
        changes["auto_title"] = False
    if "archived" in changes:
        changes["archived"] = bool(changes["archived"])
    # arquivar/desarquivar não conta como atividade: o chat volta para o mesmo lugar da lista
    chat = store.chats.update(chat_id, changes, touch=set(changes) != {"archived"})
    session = manager.sessions.get(chat_id)
    if session and set(changes) & {"model", "effort", "permission_mode", "sandbox", "approval_policy"}:
        await session.apply_settings()
    bus.publish({"type": "chat_updated", "chat": _chat_out(chat)})
    return _chat_out(chat)


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str):
    await manager.close(chat_id)
    store.delete_events(chat_id)
    store.delete_anexos(chat_id)
    store.chats.delete(chat_id)
    bus.publish({"type": "chat_deleted", "chat_id": chat_id})
    return {"ok": True}


@app.get("/api/chats/{chat_id}/events")
async def chat_events(chat_id: str):
    chat = store.chats.get(chat_id)
    if not chat:
        raise HTTPException(404)
    session = manager.sessions.get(chat_id)
    return {"chat": _chat_out(chat), "events": store.read_events(chat_id),
            "pending": session.pending_requests() if session else []}


TIPOS_IMAGEM = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
# Documentos: salvos no disco; a IA recebe o caminho (e, no Excel, cada aba convertida em CSV).
EXT_DOCUMENTOS = {"xlsx": "Excel", "xlsm": "Excel", "xls": "Excel (formato antigo)", "csv": "CSV", "pdf": "PDF",
                  "docx": "Word", "pptx": "PowerPoint", "txt": "texto", "json": "JSON", "md": "Markdown"}
MAX_IMAGEM_BYTES = 10 * 1024 * 1024
MAX_DOCUMENTO_BYTES = 30 * 1024 * 1024
MAX_ANEXOS = 10


def _salvar_anexos(chat_id: str, anexos: list) -> tuple[list[dict], list[dict]]:
    """Grava os anexos (base64) em data/anexos/<chat>/. Devolve (imagens, documentos)."""
    import base64
    import uuid
    if len(anexos) > MAX_ANEXOS:
        raise HTTPException(400, f"No máximo {MAX_ANEXOS} arquivos por mensagem.")
    imagens, documentos = [], []
    pasta = store.anexos_dir(chat_id)
    for a in anexos:
        tipo = (a.get("media_type") or "").lower()
        nome_original = (a.get("name") or "arquivo")[:120]
        ext = nome_original.rsplit(".", 1)[-1].lower() if "." in nome_original else ""
        if tipo in TIPOS_IMAGEM:
            ext_salva, limite = TIPOS_IMAGEM[tipo], MAX_IMAGEM_BYTES
        elif ext in EXT_DOCUMENTOS:
            ext_salva, limite = ext, MAX_DOCUMENTO_BYTES
        else:
            raise HTTPException(400, f'"{nome_original}": tipo não suportado. Use imagens (PNG, JPG, GIF, WEBP) ou '
                                     f"{', '.join(sorted(e.upper() for e in EXT_DOCUMENTOS))}.")
        try:
            dados = base64.b64decode(a.get("data") or "", validate=True)
        except ValueError:
            raise HTTPException(400, f'"{nome_original}" está corrompido.') from None
        if not dados or len(dados) > limite:
            raise HTTPException(400, f'"{nome_original}" está vazio ou é maior que {limite // (1024 * 1024)} MB.')
        pasta.mkdir(parents=True, exist_ok=True)
        nome = f"{uuid.uuid4().hex[:12]}.{ext_salva}"
        (pasta / nome).write_bytes(dados)
        item = {"file": nome, "name": nome_original, "media_type": tipo, "path": str(pasta / nome)}
        if tipo in TIPOS_IMAGEM:
            imagens.append(item)
        else:
            item["kind"] = EXT_DOCUMENTOS[ext]
            item["size"] = len(dados)
            if ext in ("xlsx", "xlsm"):
                item["sheets"] = _excel_para_csv(pasta / nome)
            if ext == "pptx":
                item["slides"] = _pptx_para_markdown(pasta / nome)
            documentos.append(item)
    return imagens, documentos


def _pptx_para_markdown(arquivo: Path) -> dict:
    """Texto de cada slide em Markdown (ao lado do arquivo) para a IA ler sem abrir o PowerPoint."""
    try:
        info = office.ler(arquivo)
    except Exception as e:  # noqa: BLE001 - a IA ainda recebe o arquivo original
        return {"erro": f"não consegui ler os slides ({type(e).__name__})"}
    destino = arquivo.with_name(f"{arquivo.stem}_slides.md")
    destino.write_text(office.para_markdown(info), encoding="utf-8")
    return {"total": info["total_slides"], "md": str(destino), "titulos": [s["titulo"] for s in info["slides"]][:30]}


def _excel_para_csv(arquivo: Path) -> list[dict]:
    """Converte cada aba do Excel em CSV (ao lado do arquivo) para a IA ler rápido e sem depender do Excel."""
    import csv
    import datetime
    try:
        import openpyxl
        wb = openpyxl.load_workbook(arquivo, read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001 - a IA ainda recebe o arquivo original
        return [{"erro": f"não consegui ler as abas ({type(e).__name__})"}]
    abas = []
    for i, ws in enumerate(wb.worksheets):
        destino = arquivo.with_name(f"{arquivo.stem}_aba{i + 1}.csv")
        linhas, cabecalho, colunas = 0, [], 0
        with open(destino, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            for row in ws.iter_rows(values_only=True):
                if row is None or all(v is None for v in row):
                    continue
                valores = ["" if v is None else (v.isoformat() if isinstance(v, (datetime.date, datetime.datetime)) else v)
                           for v in row]
                if not cabecalho:
                    cabecalho = [str(v) for v in valores]
                w.writerow(valores)
                linhas += 1
                colunas = max(colunas, len(valores))
        abas.append({"nome": ws.title, "csv": str(destino), "linhas": linhas, "colunas": colunas,
                     "cabecalho": [c for c in cabecalho if c][:30]})
    wb.close()
    return abas


def _bloco_documentos(documentos: list[dict]) -> str:
    """Texto que vai junto da mensagem para a IA (não aparece no balão): onde estão os arquivos e o que têm."""
    if not documentos:
        return ""
    linhas = ["[Arquivos anexados pelo usuário nesta mensagem. Estão salvos neste computador (caminhos locais, não são "
              "links): leia-os com as ferramentas locais (leitura de arquivo ou terminal, ex.: Python/PowerShell), "
              "nunca com busca na web.]"]
    for d in documentos:
        tamanho = f'{d["size"] / 1024:,.0f}'.replace(",", ".")
        linhas.append(f'- "{d["name"]}" ({d["kind"]}, {tamanho} KB): {d["path"]}')
        for aba in d.get("sheets") or []:
            if "erro" in aba:
                linhas.append(f"  - {aba['erro']}; use o arquivo original.")
                continue
            linhas.append(f'  - aba "{aba["nome"]}": {aba["linhas"]} linhas (1ª = cabeçalho) × {aba["colunas"]} colunas '
                          f"→ CSV (separador ;): {aba['csv']}")
            if aba["cabecalho"]:
                linhas.append(f"    colunas: {', '.join(aba['cabecalho'])}")
        slides = d.get("slides")
        if slides and "erro" in slides:
            linhas.append(f"  - {slides['erro']}; use o arquivo original.")
        elif slides:
            linhas.append(f"  - {slides['total']} slides; texto, tabelas e notas de cada slide em Markdown: {slides['md']}")
            if slides["titulos"]:
                linhas.append(f"    títulos: {' | '.join(t or '(sem título)' for t in slides['titulos'])}")
    return "\n".join(linhas)


@app.post("/api/chats/{chat_id}/send")
async def chat_send(chat_id: str, body: dict):
    text = (body.get("text") or "").strip()
    chat = store.chats.get(chat_id)
    if not chat:
        raise HTTPException(404)
    # Em thread: converter um Excel grande para CSV leva alguns segundos e não pode travar o servidor.
    images, documentos = await asyncio.to_thread(_salvar_anexos, chat_id, body.get("attachments") or [])
    if not text and not images and not documentos:
        raise HTTPException(400, "mensagem vazia")
    volta = {"archived": False} if chat.get("archived") else {}  # mensagem nova tira o chat dos arquivados
    if chat.get("auto_title"):
        padrao = f"Análise de {documentos[0]['name']}" if documentos else "Análise de imagem"
        title = re.sub(r"\s+", " ", text)[:60] or padrao[:60]
        chat = store.chats.update(chat_id, {"title": title, "auto_title": False, **volta})
    else:
        chat = store.chats.update(chat_id, volta)  # atualiza updated_at para subir na lista
    bus.publish({"type": "chat_updated", "chat": _chat_out(chat)})
    session = manager.get(chat_id)
    asyncio.create_task(session.send(text, images, documentos, _bloco_documentos(documentos)))
    return {"ok": True}


@app.get("/api/chats/{chat_id}/anexos/{nome}")
async def chat_anexo(chat_id: str, nome: str):
    if not re.fullmatch(r"[a-f0-9]{12}\.(png|jpg|gif|webp|xlsx|xlsm|xls|csv|pdf|docx|pptx|txt|json|md)", nome) \
            or not re.fullmatch(r"\w+", chat_id):
        raise HTTPException(404)
    arquivo = store.anexos_dir(chat_id) / nome
    if not arquivo.is_file():
        raise HTTPException(404)
    if nome.rsplit(".", 1)[-1] in ("png", "jpg", "gif", "webp"):
        return FileResponse(arquivo)
    # Documentos: baixa com o nome original (guardado no histórico do chat)
    original = next((d.get("name") for e in store.read_events(chat_id) for d in (e.get("files") or [])
                     if d.get("file") == nome), nome)
    return FileResponse(arquivo, filename=original)


# --- arquivos citados nas respostas (abrir, baixar, exportar) ---------------------------------------
# A IA grava arquivos no disco e cita o caminho; o chat mostra botões para eles. Só tipos de documento, só dentro da
# pasta do usuário (o servidor roda nesta máquina e só aceita POST da própria página).
EXT_ARQUIVOS = {".pptx", ".pdf", ".png", ".xlsx", ".xlsm", ".docx", ".csv"}


def _arquivo_local(caminho: str) -> Path:
    try:
        arquivo = Path(caminho or "").expanduser().resolve()
    except (OSError, ValueError):
        raise HTTPException(400, "caminho inválido") from None
    if arquivo.suffix.lower() not in EXT_ARQUIVOS or not arquivo.is_relative_to(Path.home()):
        raise HTTPException(403, "tipo de arquivo ou pasta não permitidos")
    if not arquivo.is_file():
        raise HTTPException(404, "arquivo não encontrado")
    return arquivo


@app.get("/api/arquivos/info")
async def arquivo_info(path: str):
    try:
        arquivo = _arquivo_local(path)
    except HTTPException as e:
        return {"existe": False, "motivo": e.detail}
    st = arquivo.stat()
    return {"existe": True, "path": str(arquivo), "nome": arquivo.name, "pasta": str(arquivo.parent),
            "ext": arquivo.suffix.lower()[1:], "tamanho": st.st_size, "modificado": st.st_mtime}


@app.get("/api/arquivos/baixar")
async def arquivo_baixar(path: str, inline: bool = False):
    arquivo = _arquivo_local(path)
    if inline:  # imagens da prévia e PDF abertos no próprio navegador
        return FileResponse(arquivo)
    return FileResponse(arquivo, filename=arquivo.name)


@app.post("/api/arquivos/abrir")
async def arquivo_abrir(body: dict):
    """Abre no programa padrão do Windows (PowerPoint, Excel, leitor de PDF...)."""
    arquivo = _arquivo_local(body.get("path"))
    await asyncio.to_thread(os.startfile, str(arquivo))
    return {"ok": True}


@app.post("/api/arquivos/abrir-pasta")
async def arquivo_abrir_pasta(body: dict):
    arquivo = _arquivo_local(body.get("path"))
    import subprocess
    subprocess.Popen(["explorer", "/select,", str(arquivo)])
    return {"ok": True}


@app.post("/api/arquivos/exportar")
async def arquivo_exportar(body: dict):
    """PowerPoint -> PDF (ao lado do arquivo) ou PNG de cada slide (prévia)."""
    arquivo = _arquivo_local(body.get("path"))
    formato = body.get("formato")
    if arquivo.suffix.lower() != ".pptx" or formato not in ("pdf", "png"):
        raise HTTPException(400, "exportação disponível para .pptx em pdf ou png")
    try:
        saidas = await asyncio.to_thread(office.exportar, arquivo, formato)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Não consegui exportar: {e}") from None
    return {"arquivos": [str(p) for p in saidas]}


@app.post("/api/chats/{chat_id}/interrupt")
async def chat_interrupt(chat_id: str):
    session = manager.sessions.get(chat_id)
    if session:
        await session.interrupt()
    return {"ok": True}


@app.post("/api/chats/{chat_id}/restart")
async def chat_restart(chat_id: str):
    """Encerra o processo do chat; a próxima mensagem reabre a sessão (mantendo o histórico)."""
    await manager.close(chat_id)
    return {"ok": True}


@app.post("/api/chats/{chat_id}/approvals/{request_id}")
async def chat_approval(chat_id: str, request_id: str, body: dict):
    session = manager.sessions.get(chat_id)
    if not session or not session.resolve_approval(request_id, body):
        raise HTTPException(404, "pedido não encontrado (talvez já respondido)")
    return {"ok": True}


# --- agentes ------------------------------------------------------------------------------------
def _clean_agent(body: dict) -> dict:
    data = {k: body.get(k) for k in AGENT_FIELDS if k in body}
    for key in ("tools", "disallowed_tools", "subagents"):
        if isinstance(data.get(key), str):
            data[key] = [t.strip() for t in data[key].split(",") if t.strip()]
    if data.get("provider") not in (None, "claude", "codex", "antigravity"):
        raise HTTPException(400, "provider inválido")
    if data.get("cwd") and not os.path.isdir(data["cwd"]):
        raise HTTPException(400, f"A pasta de trabalho não existe: {data['cwd']}")
    return data


@app.get("/api/agents")
async def list_agents():
    return sorted(store.agents.all(), key=lambda a: a.get("name", "").lower())


@app.post("/api/agents")
async def create_agent(body: dict):
    data = _clean_agent(body)
    if not data.get("name"):
        raise HTTPException(400, "nome é obrigatório")
    data.setdefault("provider", "claude")
    return store.agents.create(data)


@app.put("/api/agents/{agent_id}")
async def update_agent(agent_id: str, body: dict):
    agent = store.agents.update(agent_id, _clean_agent(body))
    if not agent:
        raise HTTPException(404)
    # Os chats deste agente seguem as configurações dele: aplica nas sessões abertas e atualiza a tela.
    for chat in store.chats.all():
        if chat.get("agent_id") == agent_id:
            session = manager.sessions.get(chat["id"])
            if session:
                await session.apply_settings()
            bus.publish({"type": "chat_updated", "chat": _chat_out(chat)})
    return agent


@app.delete("/api/agents/{agent_id}")
async def delete_agent(agent_id: str):
    store.agents.delete(agent_id)
    return {"ok": True}


@app.post("/api/agents/{agent_id}/export-claude")
async def export_agent_to_claude(agent_id: str):
    """Grava o agente em ~/.claude/agents/<nome>.md para virar subagente também no Claude Code / VS Code."""
    agent = store.agents.get(agent_id)
    if not agent:
        raise HTTPException(404)
    slug = re.sub(r"[^a-z0-9-]+", "-", agent["name"].lower()).strip("-") or "agente"
    lines = ["---", f"name: {slug}", f"description: {_yaml_str(agent.get('description') or agent['name'])}"]
    if agent.get("tools"):
        lines.append(f"tools: {', '.join(agent['tools'])}")
    if agent.get("model") not in (None, "", "default"):
        lines.append(f"model: {agent['model']}")
    lines += ["---", "", agent.get("prompt") or ""]
    path = Path.home() / ".claude" / "agents" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    providers.claude_meta.refresh_in_background()
    return {"path": str(path)}


def _yaml_str(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


# --- tempo real ---------------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket(ws: WebSocket):
    origin = ws.headers.get("origin")
    if origin and origin not in ALLOWED_ORIGINS:
        await ws.close(code=1008)
        return
    await ws.accept()
    queue = bus.subscribe()
    JANELAS["abertas"] += 1

    async def enviar():
        while True:
            await ws.send_json(await queue.get())

    envio = None
    try:
        await ws.send_json({"type": "hello", "statuses": manager.statuses(), "pending": manager.all_pending()})
        envio = asyncio.create_task(enviar())
        # Fica ouvindo para saber na hora que a janela fechou (só enviando, a queda passaria despercebida).
        while (await ws.receive())["type"] != "websocket.disconnect":
            pass
    except (WebSocketDisconnect, RuntimeError, ConnectionError, OSError):
        pass  # aba fechada, recarregada ou PC suspenso: desconexão normal
    finally:
        if envio:
            envio.cancel()
        bus.unsubscribe(queue)
        JANELAS["abertas"] -= 1


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--aba", action="store_true", help="abrir numa aba do navegador padrão em vez de janela própria")
    parser.add_argument("--sozinho", action="store_true",
                        help="desligar quando a última janela fechar (usado pelo atalho Agent Hub)")
    args = parser.parse_args()
    if args.port != PORT:
        os.environ["AGENT_HUB_PORT"] = str(args.port)
        ALLOWED_ORIGINS.clear()
        ALLOWED_ORIGINS.update({f"http://127.0.0.1:{args.port}", f"http://localhost:{args.port}"})
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        import threading
        threading.Timer(1.5, lambda: abrir_janela(url, args.aba)).start()
    print(f"Agent Hub rodando em {url}  (Ctrl+C para parar)")
    # timeout_graceful_shutdown: no Windows (Python 3.12+) o desligamento pode ficar esperando para sempre por
    # conexões que já fecharam; com o prazo ele segue e o processo termina.
    servidor = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, loop="asyncio", log_level="warning",
                                             timeout_graceful_shutdown=5))
    app.state.servidor = servidor
    app.state.sozinho = args.sozinho
    if os.environ.pop("AGENT_HUB_REINICIO", None):
        _esperar_porta_livre(args.port)
    servidor.run()


def _esperar_porta_livre(porta: int, prazo: float = 40) -> None:
    """Reinício: o servidor antigo ainda está saindo; espera ele soltar a porta."""
    import socket
    fim = time.time() + prazo
    while time.time() < fim:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", porta))
                return
            except OSError:
                time.sleep(0.3)


if __name__ == "__main__":
    main()
