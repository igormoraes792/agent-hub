"""Agent Hub — central local de chats e agentes para Claude e Codex.

Uso:  .venv\\Scripts\\python.exe server.py [--port 8787] [--no-browser]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from hub import importer, mcp_health, providers, store
from hub.sessions import AGENT_CONTROLLED, bus, codex_server, effective_chat, manager

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("AGENT_HUB_PORT", "8787"))
ALLOWED_ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}

CHAT_FIELDS = ("title", "model", "effort", "permission_mode", "sandbox", "approval_policy", "cwd", "agent_id", "pinned")
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
    asyncio.get_running_loop().set_exception_handler(_ignore_dropped_connections)
    providers.claude_meta.refresh_in_background()
    reaper = asyncio.create_task(manager.reap_idle())
    yield
    reaper.cancel()
    await manager.close_all()


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


# --- meta ---------------------------------------------------------------------------------------
@app.get("/api/meta")
async def get_meta():
    meta = providers.meta()
    try:
        meta["mcpIssues"] = len(await asyncio.to_thread(mcp_health.issues))
    except Exception:  # noqa: BLE001 - a checagem nunca deve impedir o app de abrir
        meta["mcpIssues"] = 0
    return meta


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
sys.stdout.write(path or "")
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


@app.post("/api/chats")
async def create_chat(body: dict):
    agent = store.agents.get(body["agent_id"]) if body.get("agent_id") else None
    provider = (agent or {}).get("provider") or body.get("provider") or "claude"
    if provider not in ("claude", "codex"):
        raise HTTPException(400, "provider inválido")

    def pick(field, default=None):
        value = body.get(field)
        if value in (None, "") and agent:
            value = agent.get(field)
        return value if value not in (None, "") else default

    chat = store.chats.create({
        "title": body.get("title") or (f"{agent['name']}" if agent else "Novo chat"),
        "provider": provider,
        "agent_id": agent["id"] if agent else None,
        "model": pick("model", "default" if provider == "claude" else None),
        "effort": pick("effort"),
        "cwd": pick("cwd", str(Path.home())),
        "permission_mode": pick("permission_mode", "default"),
        "sandbox": pick("sandbox", "workspace-write"),
        "approval_policy": pick("approval_policy", "on-request"),
        "session_id": None,
        "thread_id": None,
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
    chat = store.chats.update(chat_id, changes)
    session = manager.sessions.get(chat_id)
    if session and set(changes) & {"model", "effort", "permission_mode", "sandbox", "approval_policy"}:
        await session.apply_settings()
    bus.publish({"type": "chat_updated", "chat": _chat_out(chat)})
    return _chat_out(chat)


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str):
    await manager.close(chat_id)
    store.delete_events(chat_id)
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


@app.post("/api/chats/{chat_id}/send")
async def chat_send(chat_id: str, body: dict):
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "mensagem vazia")
    chat = store.chats.get(chat_id)
    if not chat:
        raise HTTPException(404)
    if chat.get("auto_title"):
        title = re.sub(r"\s+", " ", text)[:60]
        chat = store.chats.update(chat_id, {"title": title, "auto_title": False})
    else:
        chat = store.chats.update(chat_id, {})  # atualiza updated_at para subir na lista
    bus.publish({"type": "chat_updated", "chat": _chat_out(chat)})
    session = manager.get(chat_id)
    asyncio.create_task(session.send(text))
    return {"ok": True}


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
    if data.get("provider") not in (None, "claude", "codex"):
        raise HTTPException(400, "provider inválido")
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
    try:
        await ws.send_json({"type": "hello", "statuses": manager.statuses(), "pending": manager.all_pending()})
        while True:
            await ws.send_json(await queue.get())
    except (WebSocketDisconnect, RuntimeError, ConnectionError, OSError):
        pass  # aba fechada, recarregada ou PC suspenso: desconexão normal
    finally:
        bus.unsubscribe(queue)


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.port != PORT:
        os.environ["AGENT_HUB_PORT"] = str(args.port)
        ALLOWED_ORIGINS.clear()
        ALLOWED_ORIGINS.update({f"http://127.0.0.1:{args.port}", f"http://localhost:{args.port}"})
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        import threading
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Agent Hub rodando em {url}  (Ctrl+C para parar)")
    uvicorn.run(app, host="127.0.0.1", port=args.port, loop="asyncio", log_level="warning")


if __name__ == "__main__":
    main()
