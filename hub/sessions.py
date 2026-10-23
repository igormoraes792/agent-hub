"""Sessões de chat: Claude (via Claude Agent SDK) e Codex (via `codex app-server`).

Cada chat vira uma sessão viva enquanto está em uso. Tudo que acontece é convertido para um formato de
evento único (ver `emit`) e enviado para a interface pelo `bus`. Pedidos de permissão ficam pendentes até
a interface responder via `resolve_approval`.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from collections import deque
from typing import Any

from . import consumo, store
from .providers import find_codex

MAX_OUTPUT_CHARS = 20_000
IDLE_DISCONNECT_SECONDS = 20 * 60


def _clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if text and len(text) > limit:
        return text[:limit] + f"\n… [{len(text) - limit} caracteres omitidos]"
    return text


class Bus:
    """Distribui mensagens para todas as abas do navegador conectadas por WebSocket."""

    def __init__(self):
        self.queues: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=5000)
        self.queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.queues.discard(q)

    def publish(self, msg: dict) -> None:
        for q in list(self.queues):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass


bus = Bus()


class Session:
    provider = ""

    def __init__(self, chat: dict):
        self.chat_id = chat["id"]
        self.status = "idle"
        self.pending: dict[str, dict] = {}  # id -> {"request": ..., "future": ...}
        self.last_activity = time.time()

    # --- utilitários -------------------------------------------------------------------------
    @property
    def chat(self) -> dict:
        chat = store.chats.get(self.chat_id)
        return effective_chat(chat) if chat else {}

    def agent(self) -> dict | None:
        agent_id = self.chat.get("agent_id")
        return store.agents.get(agent_id) if agent_id else None

    def emit(self, event: dict, persist: bool = True) -> None:
        event = {"ts": time.time(), **event}
        if persist:
            store.append_event(self.chat_id, event)
        bus.publish({"type": "event", "chat_id": self.chat_id, "event": event})

    def delta(self, text: str) -> None:
        bus.publish({"type": "delta", "chat_id": self.chat_id, "text": text})

    def set_status(self, status: str) -> None:
        self.last_activity = time.time()
        if status != self.status:
            self.status = status
            bus.publish({"type": "status", "chat_id": self.chat_id, "status": status})

    def save_chat(self, **changes) -> None:
        chat = store.chats.update(self.chat_id, changes)
        if chat:
            bus.publish({"type": "chat_updated", "chat": chat})

    async def ask(self, request: dict) -> dict:
        """Mostra um pedido de aprovação/pergunta na interface e espera a resposta."""
        rid = store.new_id()
        request = {**request, "id": rid, "provider": self.provider}
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = {"request": request, "future": fut}
        bus.publish({"type": "approval", "chat_id": self.chat_id, "request": request})
        self.set_status("waiting")
        try:
            return await fut
        finally:
            self.pending.pop(rid, None)
            bus.publish({"type": "approval_done", "chat_id": self.chat_id, "id": rid})
            if self.status == "waiting" and not self.pending:
                self.set_status("running")

    def resolve_approval(self, rid: str, response: dict) -> bool:
        item = self.pending.get(rid)
        if not item or item["future"].done():
            return False
        item["future"].set_result(response)
        return True

    def deny_all_pending(self, message: str = "Interrompido pelo usuário.") -> None:
        for item in list(self.pending.values()):
            if not item["future"].done():
                item["future"].set_result({"decision": "deny", "message": message})

    def pending_requests(self) -> list[dict]:
        return [p["request"] for p in self.pending.values()]

    def background_tasks(self) -> list[str]:
        """Descrições das tarefas/subagentes que continuam rodando em segundo plano."""
        return list(getattr(self, "tasks_running", {}).values())

    def _publish_tasks(self) -> None:
        bus.publish({"type": "tasks", "chat_id": self.chat_id, "tasks": self.background_tasks()})

    # --- interface pública --------------------------------------------------------------------
    async def send(self, text: str, images: list[dict] | None = None, files: list[dict] | None = None,
                   files_note: str = "") -> None: ...
    async def interrupt(self) -> None: ...

    def emit_user(self, text: str, images: list[dict] | None, files: list[dict] | None = None) -> None:
        """Mensagem do usuário no histórico (com imagens e arquivos anexados, se houver)."""
        event: dict[str, Any] = {"kind": "user", "text": text}
        if images:
            event["images"] = [{"file": i["file"], "name": i.get("name") or i["file"]} for i in images]
        if files:
            event["files"] = [{"file": f["file"], "name": f["name"], "kind": f.get("kind", "")} for f in files]
        self.emit(event)

    @staticmethod
    def texto_para_ia(text: str, files: list[dict] | None, files_note: str) -> str:
        """O que a IA recebe: a mensagem + onde estão os arquivos anexados (isso não aparece no balão)."""
        base = text or ("Analise o(s) arquivo(s) anexado(s)." if files else "Analise a imagem.")
        if files_note:
            base = f"{base}\n\n{files_note}"
        if base.startswith("/"):  # comando (/compact, /skill…): não pode receber texto extra
            return base
        return f"{base}\n\n{agora_brasilia()}"
    async def apply_settings(self) -> None: ...
    async def close(self) -> None: ...


# =============================================================================================
# Claude
# =============================================================================================
class ClaudeSession(Session):
    provider = "claude"

    def __init__(self, chat: dict):
        super().__init__(chat)
        self.client = None
        self.task: asyncio.Task | None = None
        self.ready = asyncio.Event()
        self.connect_error: str | None = None
        self.stderr_tail: deque[str] = deque(maxlen=40)
        self.announced_init = False
        self.restart_pending = False
        self.tasks_running: dict[str, str] = {}  # subagentes/tarefas em segundo plano ainda ativos: id -> descrição

    def _options(self):
        from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions

        chat = self.chat
        agent = self.agent()
        system_prompt: dict[str, Any] = {"type": "preset", "preset": "claude_code"}
        extra = extra_instructions(chat, agent)
        if extra:
            system_prompt["append"] = extra

        # Chat de agente: só os subagentes marcados nele. Chat livre: todos os agentes Claude do Agent Hub.
        if agent:
            ids_subagentes = agent.get("subagents") or []
        else:
            ids_subagentes = [a["id"] for a in store.agents.all() if a.get("provider") == "claude"]
        subagents = {}
        for sub_id in ids_subagentes:
            sub = store.agents.get(sub_id)
            if sub and sub.get("provider") == "claude":
                subagents[_slug(sub["name"])] = AgentDefinition(
                    description=sub.get("description") or sub["name"],
                    prompt="\n\n".join(p for p in (IDIOMA, FUSO, _pasta_do_agente(sub), sub.get("prompt") or "") if p),
                    tools=_ferramentas(sub.get("tools")),
                    disallowedTools=sub.get("disallowed_tools") or None,
                    model=None if sub.get("model") in (None, "", "default") else sub["model"],
                )

        model = chat.get("model")
        return ClaudeAgentOptions(
            cwd=chat.get("cwd") or None,
            model=None if model in (None, "", "default") else model,
            effort=chat.get("effort") or None,
            permission_mode=chat.get("permission_mode") or "default",
            system_prompt=system_prompt,
            setting_sources=["user", "project", "local"],
            resume=chat.get("session_id"),
            # Conversa importada: ramifica na primeira retomada para não mexer na original do VS Code.
            fork_session=bool(chat.get("fork_pending") and chat.get("session_id")),
            can_use_tool=self._can_use_tool,
            include_partial_messages=True,
            tools=_ferramentas((agent or {}).get("tools")),
            disallowed_tools=(agent or {}).get("disallowed_tools") or [],
            agents=subagents or None,
            add_dirs=[self._pasta_anexos()],  # arquivos anexados pelo usuário: leitura sem pedir permissão
            stderr=self.stderr_tail.append,
        )

    def _pasta_anexos(self) -> str:
        pasta = store.anexos_dir(self.chat_id)
        pasta.mkdir(parents=True, exist_ok=True)
        return str(pasta)

    async def _ensure_connected(self) -> None:
        cwd = self.chat.get("cwd")
        if cwd and not os.path.isdir(cwd):
            os.makedirs(cwd, exist_ok=True)  # conversa importada cuja pasta original foi apagada
        if self.task is None or self.task.done():
            self.ready.clear()
            self.connect_error = None
            self.announced_init = False
            self.task = asyncio.create_task(self._run())
        await asyncio.wait_for(self.ready.wait(), timeout=120)
        if self.connect_error:
            raise RuntimeError(self.connect_error)

    async def _run(self) -> None:
        """Dona do cliente: conecta, lê mensagens e desconecta na mesma task (exigência do SDK)."""
        from claude_agent_sdk import ClaudeSDKClient

        try:
            async with ClaudeSDKClient(self._options()) as client:
                self.client = client
                self.ready.set()
                async for msg in client.receive_messages():
                    try:
                        self._handle(msg)
                    except Exception as e:  # noqa: BLE001
                        self.emit({"kind": "error", "text": f"Falha ao processar mensagem: {e}"})
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            tail = "\n".join(list(self.stderr_tail)[-10:])
            self.connect_error = f"{type(e).__name__}: {e}" + (f"\n{tail}" if tail else "")
            self.emit({"kind": "error", "text": "Sessão do Claude encerrada com erro:\n" + self.connect_error})
        finally:
            self.client = None
            self.ready.set()
            self.tasks_running.clear()  # com a sessão fechada, as tarefas em segundo plano também param
            self._publish_tasks()
            self.deny_all_pending("Sessão encerrada.")
            self.set_status("idle")

    async def _can_use_tool(self, tool_name: str, tool_input: dict, ctx) -> Any:
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        if tool_name == "AskUserQuestion":
            request = {"kind": "question", "title": "O Claude tem perguntas",
                       "questions": [
                           {"id": q.get("question"), "header": q.get("header", ""), "question": q.get("question", ""),
                            "options": q.get("options") or [], "multiSelect": q.get("multiSelect", False), "isOther": True}
                           for q in tool_input.get("questions", [])
                       ]}
        elif tool_name == "ExitPlanMode":
            request = {"kind": "plan", "title": "Aprovar plano?", "detail": tool_input.get("plan", "")}
        else:
            request = {"kind": "tool", "title": getattr(ctx, "title", None) or f"Permitir {tool_name}?",
                       "tool": tool_name, "input": tool_input,
                       "detail": getattr(ctx, "decision_reason", None) or getattr(ctx, "description", None),
                       "can_always": bool(getattr(ctx, "suggestions", None))}

        res = await self.ask(request)
        if res.get("decision") == "deny":
            return PermissionResultDeny(message=res.get("message") or "O usuário recusou.", interrupt=False)
        updated = tool_input
        if request["kind"] == "question":
            answers = {q: (", ".join(a) if isinstance(a, list) else a) for q, a in (res.get("answers") or {}).items()}
            updated = {**tool_input, "answers": answers}
        always = res.get("decision") == "allow_always" and getattr(ctx, "suggestions", None)
        return PermissionResultAllow(updated_input=updated, updated_permissions=ctx.suggestions if always else None)

    def _handle(self, msg) -> None:
        from claude_agent_sdk import (AssistantMessage, RateLimitEvent, ResultMessage, StreamEvent, SystemMessage,
                                      TaskNotificationMessage, TaskStartedMessage, TextBlock, ThinkingBlock,
                                      ToolResultBlock, ToolUseBlock, UserMessage)

        self.last_activity = time.time()
        if self.status == "idle" and isinstance(msg, (AssistantMessage, StreamEvent)):
            # Turno iniciado pelo próprio Claude (ex.: subagente em segundo plano terminou).
            self.set_status("running")
        if isinstance(msg, StreamEvent):
            ev = msg.event or {}
            if (msg.parent_tool_use_id is None and ev.get("type") == "content_block_delta"
                    and (ev.get("delta") or {}).get("type") == "text_delta"):
                self.delta(ev["delta"].get("text", ""))
            return

        if isinstance(msg, TaskStartedMessage):
            nome = _nome_tarefa(msg.description or msg.task_type)
            self.tasks_running[msg.task_id] = nome[:80]
            self._publish_tasks()
            self.emit({"kind": "system", "text": f"Tarefa iniciada: {nome}"})
            return
        if isinstance(msg, TaskNotificationMessage):
            self.tasks_running.pop(msg.task_id, None)
            self._publish_tasks()
            status = {"completed": "concluída", "failed": "falhou", "stopped": "interrompida"}.get(msg.status, msg.status)
            self.emit({"kind": "system", "text": f"Tarefa {status}: {_nome_tarefa(msg.summary)}"})
            return

        if isinstance(msg, SystemMessage):
            data = msg.data or {}
            if msg.subtype == "init":
                if data.get("session_id") and data["session_id"] != self.chat.get("session_id"):
                    self.save_chat(session_id=data["session_id"], fork_pending=False)
                if not self.announced_init:
                    self.announced_init = True
                    labels = {"connected": "✓", "pending": "(conectando…)"}
                    servers = data.get("mcp_servers") or []
                    mcp = ", ".join(
                        f"{s.get('name')} {labels.get(s.get('status'), '✗ ' + str(s.get('status')))}"
                        for s in servers if s.get("status") != "needs-auth")
                    no_auth = sum(1 for s in servers if s.get("status") == "needs-auth")
                    if no_auth:
                        mcp += f"{' · ' if mcp else ''}{no_auth} sem login"
                    self.emit({"kind": "system", "text": (
                        f"Claude conectado · modelo {data.get('model')} · {len(data.get('tools') or [])} ferramentas"
                        f" · modo {data.get('permissionMode')}" + (f"\nMCP: {mcp}" if mcp else ""))})
            elif msg.subtype == "compact_boundary":
                self.emit({"kind": "system", "text": "Contexto compactado."})
            return

        if isinstance(msg, AssistantMessage):
            parent = msg.parent_tool_use_id
            for block in msg.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    self.emit({"kind": "assistant", "text": block.text, "parent": parent})
                elif isinstance(block, ThinkingBlock) and block.thinking.strip():
                    self.emit({"kind": "thinking", "text": block.thinking, "parent": parent})
                elif isinstance(block, ToolUseBlock):
                    self.emit({"kind": "tool", "id": block.id, "name": block.name, "input": block.input,
                               "parent": parent})
            if msg.error:
                self.emit({"kind": "error", "text": f"Erro da API: {msg.error}"})
            return

        if isinstance(msg, UserMessage):
            if isinstance(msg.content, str):
                text = msg.content
                if "<local-command-stdout>" in text:
                    text = text.replace("<local-command-stdout>", "").replace("</local-command-stdout>", "")
                    self.emit({"kind": "system", "text": text.strip()})
                return
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    self.emit({"kind": "tool_result", "id": block.tool_use_id,
                               "output": _clip(_content_text(block.content)), "is_error": bool(block.is_error)})
            return

        if isinstance(msg, ResultMessage):
            if msg.session_id and msg.session_id != self.chat.get("session_id"):
                self.save_chat(session_id=msg.session_id, fork_pending=False)
            if msg.is_error and msg.subtype != "success":
                errs = "; ".join(msg.errors or []) if getattr(msg, "errors", None) else (msg.result or msg.subtype)
                self.emit({"kind": "error", "text": f"Turno terminou com erro: {errs}"})
            self.emit({"kind": "result", "cost_usd": msg.total_cost_usd, "duration_ms": msg.duration_ms,
                       "turns": msg.num_turns, "usage": _usage_summary(msg.usage), "is_error": msg.is_error})
            # Subagentes em segundo plano continuam trabalhando depois do fim do turno: o chat segue "trabalhando".
            self.set_status("running" if self.tasks_running else "idle")
            if self.restart_pending and not self.tasks_running:  # configuração que exige sessão nova
                asyncio.create_task(self._reabrir())
            return

        if isinstance(msg, RateLimitEvent):
            info = msg.rate_limit_info
            if info:
                consumo.salvar_limite_claude(info.raw)  # aba Consumo
            # Aviso no chat só quando muda (não a cada mensagem).
            aviso = (info.rate_limit_type, info.status) if info and info.status not in ("allowed", None) else None
            if aviso and aviso != getattr(self, "_ultimo_aviso_limite", None):
                self._ultimo_aviso_limite = aviso
                janela = {"five_hour": "da sessão (5 h)", "seven_day": "semanal", "seven_day_opus": "semanal do Opus",
                          "seven_day_sonnet": "semanal do Sonnet"}.get(info.rate_limit_type, info.rate_limit_type or "")
                uso = f"{round(100 * info.utilization)}% " if info.utilization is not None else ""
                texto = (f"Limite {janela} atingido." if info.status == "rejected"
                         else f"Atenção: você já usou {uso}do limite {janela}. Detalhes na aba Consumo.")
                self.emit({"kind": "system", "level": "warn", "text": texto})

    async def send(self, text: str, images: list[dict] | None = None, files: list[dict] | None = None,
                   files_note: str = "") -> None:
        self.emit_user(text, images, files)
        self.set_status("running")
        texto = self.texto_para_ia(text, files, files_note)
        try:
            await self._ensure_connected()
            if not images:
                await self.client.query(texto)
                return
            # Imagens vão como blocos da própria mensagem (base64), antes do texto.
            import base64
            content: list[dict] = [
                {"type": "image", "source": {"type": "base64", "media_type": i["media_type"],
                                             "data": base64.b64encode(Path(i["path"]).read_bytes()).decode("ascii")}}
                for i in images
            ]
            content.append({"type": "text", "text": texto})

            async def mensagem():
                yield {"type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None}

            await self.client.query(mensagem())
        except Exception as e:  # noqa: BLE001
            self.emit({"kind": "error", "text": f"Não consegui enviar para o Claude: {e}"})
            self.set_status("idle")

    async def interrupt(self) -> None:
        self.deny_all_pending()
        if self.client:
            try:
                await self.client.interrupt()
            except Exception as e:  # noqa: BLE001
                self.emit({"kind": "error", "text": f"Falha ao interromper: {e}"})
        self.emit({"kind": "system", "text": "Interrompido."})
        self.set_status("idle")

    async def apply_settings(self) -> None:
        if not self.client:
            return
        chat = self.chat
        model = chat.get("model")
        try:
            await self.client.set_model(None if model in (None, "", "default") else model)
        except Exception as e:  # noqa: BLE001
            self.emit({"kind": "error", "text": f"Não consegui trocar o modelo: {e}"})
        try:
            await self.client.set_permission_mode(chat.get("permission_mode") or "default")
        except Exception:  # noqa: BLE001
            # Alguns modos (ex.: "Sem perguntar nada") só podem ser ativados ao abrir a sessão.
            self.restart_pending = True
            self.emit({"kind": "system", "level": "warn",
                       "text": "O novo modo de permissões vale a partir da próxima mensagem: a sessão será reaberta "
                               "automaticamente ao fim desta resposta, mantendo o histórico."})
            if self.status == "idle" and not self.tasks_running:
                await self._reabrir()

    async def _reabrir(self) -> None:
        """Fecha a sessão; a próxima mensagem a reabre (com resume) já com as configurações novas."""
        self.restart_pending = False
        await asyncio.sleep(0.5)
        await self.close()

    async def close(self) -> None:
        self.deny_all_pending("Sessão encerrada.")
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self.task = None


AGENT_CONTROLLED = ("model", "effort", "permission_mode", "sandbox", "approval_policy")


def effective_chat(chat: dict) -> dict:
    """Configurações que valem para o chat: em chats de agente, modelo/esforço/permissões vêm do agente
    (só são ajustáveis no agente) e acompanham as mudanças feitas nele."""
    agent = store.agents.get(chat["agent_id"]) if chat.get("agent_id") else None
    if not agent:
        return chat
    return {**chat, **{k: agent[k] for k in AGENT_CONTROLLED if agent.get(k) not in (None, "")}}


FUSO = ("Fuso horário: o usuário está em Brasília (UTC-3, America/Sao_Paulo, sem horário de verão). Mostre "
        "SEMPRE os horários no horário de Brasília. Horários técnicos vindos de sistemas costumam estar em UTC: "
        "colunas de auditoria/carga (dh_update_*, dh_insert_*, dt_carga, updated_at, created_at), changed_on/"
        "created_on e logs do Superset, metadados e jobs do Dremio, horários de atualização (refresh) do Power BI, "
        "logs e APIs em geral. Converta subtraindo 3 horas antes de citar (ex.: 14:20 UTC = 11:20 de Brasília) e, "
        "quando ajudar, diga que converteu. Datas e horas de negócio (data de faturamento, emissão, captação do "
        "pedido) já vêm no horário local: não converta. Se um horário ficar no futuro em relação ao horário atual "
        "informado na mensagem, ele está em UTC. Se não der para saber o fuso de um horário, avise em vez de chutar.")


def agora_brasilia() -> str:
    """Nota de horário que vai junto de cada mensagem (não aparece no balão do chat)."""
    from datetime import datetime, timedelta, timezone
    agora = datetime.now(timezone(timedelta(hours=-3)))
    return f"[Agora: {agora:%d/%m/%Y %H:%M}, horário de Brasília (UTC-3).]"


IDIOMA = ("Idioma: escreva SEMPRE em português do Brasil, em tudo o que aparece para o usuário: respostas, raciocínio "
          "interno (thinking), planos e listas de tarefas, a descrição curta de cada comando/ferramenta (campo "
          "description), nomes de tarefas e subagentes e mensagens intermediárias. Mantenha em inglês apenas código, "
          "comandos, nomes de arquivos/colunas e termos técnicos sem tradução.")


def _ferramentas(lista) -> list[str] | None:
    """No Windows o terminal do Claude Code pode aparecer como 'Bash' ou como 'PowerShell', conforme a sessão.
    Quem tem acesso a um recebe os dois, para nunca ficar sem terminal."""
    if not lista:
        return None
    ferramentas = list(lista)
    if any(t in ferramentas for t in ("Bash", "PowerShell")):
        ferramentas += [t for t in ("Bash", "PowerShell") if t not in ferramentas]
    return ferramentas


def _pasta_do_agente(agent: dict) -> str:
    """Como subagente ele roda na pasta do chat que o chamou; por isso recebe o caminho completo da própria pasta."""
    cwd = agent.get("cwd")
    if not cwd:
        return ""
    return (f'Sua pasta de trabalho é "{cwd}": seus manuais, ferramentas e modelos ficam nela. Leia arquivos pelo '
            f'caminho completo (ex.: "{cwd}\\CONHECIMENTO.md") e rode scripts a partir dela (cd "{cwd}" antes do comando), '
            f"porque a conversa que chamou você pode estar em outra pasta.")


def extra_instructions(chat: dict, agent: dict | None) -> str:
    """Idioma + instruções do agente + a saudação que o usuário viu antes de escrever."""
    parts = [IDIOMA, FUSO]
    if agent and agent.get("prompt"):
        parts.append(agent["prompt"])
    if chat.get("greeting"):
        parts.append("Antes da primeira mensagem, o usuário viu esta saudação, apresentada como sua:\n\n"
                     + chat["greeting"])
    return "\n\n".join(parts)


_PARECE_COMANDO = re.compile(r"""^\s*(\w+=|cd\s|python|pwsh|powershell|bash|cmd|\$|\.\/|[A-Za-z]:[\\/])|&&|\$\(|\|\s*\w""")


def _nome_tarefa(texto) -> str:
    """Nome amigável de uma tarefa em segundo plano: se o 'nome' for o próprio comando, não mostra o comando."""
    texto = str(texto or "").strip()
    if _PARECE_COMANDO.search(texto.split("\n", 1)[0]):
        return "comando em segundo plano"
    return _uma_linha(texto)


def _uma_linha(texto, limite: int = 140) -> str:
    """Resumo de uma linha para avisos internos (descrições de tarefa às vezes trazem o comando inteiro)."""
    texto = str(texto or "").strip()
    primeira = texto.split("\n", 1)[0].strip()
    if len(primeira) > limite:
        return primeira[:limite] + " …"
    return primeira + (" …" if "\n" in texto else "")


def _content_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for c in content:
        if isinstance(c, dict):
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif c.get("type") == "image":
                parts.append("[imagem]")
            else:
                parts.append(json.dumps(c, ensure_ascii=False))
        else:
            parts.append(str(c))
    return "\n".join(parts)


def _usage_summary(usage: dict | None) -> dict:
    usage = usage or {}
    return {
        "input": (usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0),
        "output": usage.get("output_tokens") or 0,
    }


def _slug(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "agente"


# =============================================================================================
# Codex — um único processo `codex app-server` atende todos os chats (uma thread por chat)
# =============================================================================================
class CodexServer:
    def __init__(self):
        self.proc: asyncio.subprocess.Process | None = None
        self.next_id = 0
        self.waiting: dict[int, asyncio.Future] = {}
        self.threads: dict[str, "CodexSession"] = {}
        self.filhos: dict[str, "CodexSession"] = {}  # thread de subagente -> chat que o chamou
        self.lock = asyncio.Lock()
        self.stderr_tail: deque[str] = deque(maxlen=40)

    async def ensure(self) -> None:
        async with self.lock:
            if self.proc and self.proc.returncode is None:
                return
            path = find_codex()
            if not path:
                raise RuntimeError("codex.exe não encontrado (instale a extensão do Codex ou defina CODEX_PATH).")
            self.proc = await asyncio.create_subprocess_exec(
                path, "app-server", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=2 ** 26)
            asyncio.create_task(self._read_stdout(self.proc))
            asyncio.create_task(self._read_stderr(self.proc))
            await self._request_unlocked("initialize", {
                "clientInfo": {"name": "agent-hub", "title": "Agent Hub", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True}})
            await self._write({"method": "initialized"})

    async def _write(self, msg: dict) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def _request_unlocked(self, method: str, params: dict, timeout: float = 120) -> dict:
        self.next_id += 1
        rid = self.next_id
        fut = asyncio.get_running_loop().create_future()
        self.waiting[rid] = fut
        await self._write({"id": rid, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.waiting.pop(rid, None)

    async def request(self, method: str, params: dict, timeout: float = 120) -> dict:
        await self.ensure()
        return await self._request_unlocked(method, params, timeout)

    async def respond(self, rid, result: dict | None = None, error: dict | None = None) -> None:
        msg: dict[str, Any] = {"id": rid}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result or {}
        await self._write(msg)

    async def _read_stderr(self, proc) -> None:
        async for line in proc.stderr:
            self.stderr_tail.append(line.decode("utf-8", "replace").rstrip())

    async def _read_stdout(self, proc) -> None:
        try:
            async for raw in proc.stdout:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                try:
                    await self._dispatch(msg)
                except Exception as e:  # noqa: BLE001
                    self.stderr_tail.append(f"dispatch error: {e}")
        finally:
            err = RuntimeError("codex app-server encerrou: " + " | ".join(list(self.stderr_tail)[-5:]))
            for fut in self.waiting.values():
                if not fut.done():
                    fut.set_exception(err)
            for session in list(self.threads.values()):
                session.on_server_exit()
            self.threads.clear()

    async def _dispatch(self, msg: dict) -> None:
        method = msg.get("method")
        if method is None:  # resposta a um pedido nosso
            fut = self.waiting.get(msg.get("id"))
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(RuntimeError((msg["error"] or {}).get("message", str(msg["error"]))))
                else:
                    fut.set_result(msg.get("result") or {})
            return
        params = msg.get("params") or {}
        tid = params.get("threadId")
        session = self.threads.get(tid)
        filho = None
        if session is None and tid in self.filhos:  # subagente: tudo vai para o chat que o chamou
            session, filho = self.filhos[tid], tid
        if "id" in msg:  # pedido do servidor (aprovação, pergunta…)
            if session:
                asyncio.create_task(session.on_server_request(msg["id"], method, params))
            else:
                await self.respond(msg["id"], error={"code": -32601, "message": "sem sessão para esta thread"})
            return
        if method == "windowsSandbox/setupCompleted":
            bus.publish({"type": "codex_sandbox", "result": params})
        elif session and filho:
            session.on_child_notification(filho, method, params)
        elif session:
            session.on_notification(method, params)


codex_server = CodexServer()


class CodexSession(Session):
    provider = "codex"

    def __init__(self, chat: dict):
        super().__init__(chat)
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.items: dict[str, dict] = {}
        self.last_usage: dict | None = None
        self.turn_started_at = 0.0
        self.reopen_pending = False

    def _thread_params(self) -> dict:
        chat = self.chat
        agent = self.agent()
        params: dict[str, Any] = {
            "cwd": chat.get("cwd") or None,
            "sandbox": chat.get("sandbox") or "workspace-write",
            "approvalPolicy": chat.get("approval_policy") or "on-request",
        }
        if chat.get("model"):
            params["model"] = chat["model"]
        extra = extra_instructions(chat, agent)
        if extra:
            params["developerInstructions"] = extra
        config = _config_codex(agent)
        if config:
            params["config"] = config
        return params

    async def _ensure_thread(self) -> None:
        if self.thread_id and codex_server.threads.get(self.thread_id) is self:
            return
        saved = self.chat.get("thread_id")
        if saved and self.chat.get("fork_pending"):
            # Conversa importada: ramifica para não mexer na original do VS Code.
            res = await codex_server.request("thread/fork", {**self._thread_params(), "threadId": saved,
                                                             "excludeTurns": True})
            self.save_chat(fork_pending=False)
        elif saved:
            res = await codex_server.request("thread/resume", {**self._thread_params(), "threadId": saved,
                                                               "excludeTurns": True})
        else:
            res = await codex_server.request("thread/start", self._thread_params())
        self.thread_id = res["thread"]["id"]
        codex_server.threads[self.thread_id] = self
        if self.thread_id != saved:
            self.save_chat(thread_id=self.thread_id)
        self.emit({"kind": "system", "text": f"Codex conectado · modelo {res.get('model')} · sandbox "
                                             f"{_sandbox_label(res.get('sandbox'))} · aprovação {res.get('approvalPolicy')}"})

    async def send(self, text: str, images: list[dict] | None = None, files: list[dict] | None = None,
                   files_note: str = "") -> None:
        self.emit_user(text, images, files)
        try:
            await self._ensure_thread()
            texto = self.texto_para_ia(text, files, files_note)
            papeis = sorted({k.split(".")[1] for k in _config_codex(self.agent()) if k.startswith("agents.")})
            if papeis and self.agent() and not texto.startswith("/"):
                # O Codex só delega quando o usuário pede; no chat de agente com subagentes, o pedido vai junto.
                texto += ("\n\n[Você tem autorização do usuário para delegar a subagentes com a ferramenta spawn_agent "
                          f"(agent_type: {', '.join(papeis)}), como indicam as suas instruções.]")
            items = [{"type": "text", "text": texto}]
            items += [{"type": "localImage", "path": i["path"]} for i in images or []]
            if self.turn_id and self.status != "idle":
                await codex_server.request("turn/steer", {"threadId": self.thread_id, "expectedTurnId": self.turn_id,
                                                          "input": items})
                return
            self.set_status("running")
            params: dict[str, Any] = {"threadId": self.thread_id, "input": items}
            chat = self.chat
            if chat.get("model"):
                params["model"] = chat["model"]
            if chat.get("effort"):
                params["effort"] = chat["effort"]
            self.turn_started_at = time.time()
            res = await codex_server.request("turn/start", params)
            self.turn_id = res["turn"]["id"]
        except Exception as e:  # noqa: BLE001
            self.emit({"kind": "error", "text": f"Não consegui enviar para o Codex: {e}"})
            self.set_status("idle")

    async def interrupt(self) -> None:
        self.deny_all_pending()
        if self.thread_id and self.turn_id:
            try:
                await codex_server.request("turn/interrupt", {"threadId": self.thread_id, "turnId": self.turn_id},
                                           timeout=20)
            except Exception as e:  # noqa: BLE001
                self.emit({"kind": "error", "text": f"Falha ao interromper: {e}"})
        else:
            self.set_status("idle")

    async def apply_settings(self) -> None:
        # Modelo e esforço vão em cada turn/start; sandbox e aprovação exigem reabrir a thread,
        # o que só pode acontecer fora de um turno (senão os eventos do turno em andamento se perdem).
        if self.status != "idle":
            self.reopen_pending = True
            return
        self._release_thread()

    def _release_thread(self) -> None:
        self.reopen_pending = False
        if self.thread_id:
            codex_server.threads.pop(self.thread_id, None)
            self.thread_id = None

    async def close(self) -> None:
        self.deny_all_pending("Sessão encerrada.")
        for filho in [f for f, s in codex_server.filhos.items() if s is self]:
            codex_server.filhos.pop(filho, None)
        if self.thread_id:
            codex_server.threads.pop(self.thread_id, None)
            try:
                await codex_server.request("thread/unsubscribe", {"threadId": self.thread_id}, timeout=10)
            except Exception:  # noqa: BLE001
                pass
            self.thread_id = None

    def on_server_exit(self) -> None:
        self.thread_id = None
        self.turn_id = None
        self.deny_all_pending("O processo do Codex encerrou.")
        if self.status != "idle":
            self.emit({"kind": "error", "text": "O processo do Codex encerrou inesperadamente."})
            self.set_status("idle")

    # --- eventos do servidor -------------------------------------------------------------------
    def on_notification(self, method: str, p: dict) -> None:
        self.last_activity = time.time()
        if method == "item/agentMessage/delta":
            self.delta(p.get("delta", ""))
        elif method == "item/started":
            self._item_started(p.get("item") or {})
        elif method == "item/completed":
            self._item_completed(p.get("item") or {})
        elif method == "turn/plan/updated":
            self.emit({"kind": "plan", "id": p.get("turnId"), "explanation": p.get("explanation"),
                       "steps": p.get("plan") or []})
        elif method == "thread/tokenUsage/updated":
            usage = p.get("tokenUsage") or {}
            self.last_usage = usage.get("last") or usage.get("total")
        elif method == "turn/started":
            self.turn_id = (p.get("turn") or {}).get("id") or self.turn_id
            self.set_status("running")
        elif method == "turn/completed":
            turn = p.get("turn") or {}
            status = turn.get("status")
            if status == "failed":
                self.emit({"kind": "error", "text": ((turn.get("error") or {}).get("message")) or "Turno falhou."})
            elif status == "interrupted":
                self.emit({"kind": "system", "text": "Interrompido."})
            u = self.last_usage or {}
            self.emit({"kind": "result", "cost_usd": None,
                       "duration_ms": turn.get("durationMs") or int((time.time() - self.turn_started_at) * 1000),
                       "usage": {"input": u.get("inputTokens", 0), "output": u.get("outputTokens", 0)},
                       "is_error": status == "failed"})
            self.turn_id = None
            self.deny_all_pending("Turno encerrado.")
            self.set_status("idle")
            if self.reopen_pending:
                self._release_thread()
        elif method == "error":
            err = (p.get("error") or {}).get("message", "erro")
            if p.get("willRetry"):
                self.emit({"kind": "system", "level": "warn", "text": f"{err} (tentando de novo…)"})
            else:
                self.emit({"kind": "error", "text": err})
        elif method == "mcpServer/startupStatus/updated" and p.get("status") == "failed":
            self.emit({"kind": "system", "level": "warn",
                       "text": f"MCP {p.get('name')} falhou: {p.get('error') or p.get('failureReason')}"})
        elif method == "thread/compacted":
            self.emit({"kind": "system", "text": "Contexto compactado."})
        elif method in ("warning", "configWarning", "deprecationNotice"):
            msg = p.get("message") or p.get("summary") or json.dumps(p, ensure_ascii=False)[:300]
            self.emit({"kind": "system", "level": "warn", "text": msg})

    def _item_started(self, item: dict) -> None:
        if self._subagente(item):
            return
        tool = codex_tool_from_item(item)
        if tool:
            self.items[item["id"]] = item
            self.emit({"kind": "tool", "id": item["id"], **tool})

    def _item_completed(self, item: dict) -> None:
        if self._subagente(item, concluido=True):
            return
        started = self.items.pop(item.get("id"), None) is not None
        for event in codex_item_events(item, include_tool_start=not started):
            self.emit(event)

    # --- subagentes (papéis do Codex chamados com spawn_agent) -----------------------------------
    def _subagente(self, item: dict, concluido: bool = False) -> bool:
        """Itens que controlam subagentes. Registra a thread do subagente (para os eventos e aprovações dele
        virem para este chat) e mostra uma etapa "Delegando: <papel>". True = item já tratado."""
        t = item.get("type")
        if t == "collabAgentToolCall":
            for filho in item.get("receiverThreadIds") or []:
                codex_server.filhos[filho] = self
            return item.get("tool") in ("wait", "wait_agent")  # "esperando o subagente": não vira etapa
        if t != "subAgentActivity":
            return False
        filho = item.get("agentThreadId")
        if filho:
            codex_server.filhos[filho] = self
        if not concluido:  # o mesmo item chega em started e completed: trata uma vez só
            return True
        papel = (item.get("agentPath") or "").rstrip("/").rsplit("/", 1)[-1] or "subagente"
        tid = f"sub-{filho}"
        tipo = item.get("kind")
        if tipo == "started":
            self.emit({"kind": "tool", "id": tid, "name": "Agent", "input": {"subagent_type": papel,
                                                                             "description": papel}})
        elif tipo == "completed":
            self.emit({"kind": "tool_result", "id": tid, "is_error": False, "output": "Concluído."})
        elif tipo in ("failed", "errored", "interrupted", "killed", "shutdown", "cancelled"):
            self.emit({"kind": "tool_result", "id": tid, "is_error": True, "output": f"Subagente terminou: {tipo}"})
        # "interacted" (o chat principal mandou mensagem/esperou o subagente) e outros: só andamento, sem etapa
        return True

    def on_child_notification(self, filho: str, method: str, p: dict) -> None:
        """Eventos da thread de um subagente: viram detalhes dentro da etapa (não mexem no turno deste chat)."""
        self.last_activity = time.time()
        if method == "item/completed":
            item = p.get("item") or {}
            if self._subagente(item, concluido=True):
                return
            started = self.items.pop(item.get("id"), None) is not None
            for ev in codex_item_events(item, include_tool_start=not started):
                self.emit({**ev, "parent": filho})
        elif method == "item/started":
            item = p.get("item") or {}
            if self._subagente(item):
                return
            tool = codex_tool_from_item(item)
            if tool:
                self.items[item["id"]] = item
                self.emit({"kind": "tool", "id": item["id"], "parent": filho, **tool})
        elif method == "error" and not p.get("willRetry"):
            self.emit({"kind": "system", "level": "warn",
                       "text": f"Subagente: {(p.get('error') or {}).get('message', 'erro')}"})
        elif method == "mcpServer/startupStatus/updated" and p.get("status") == "failed":
            self.emit({"kind": "system", "level": "warn",
                       "text": f"Subagente: MCP {p.get('name')} falhou: {p.get('error') or p.get('failureReason')}"})

    async def on_server_request(self, rid, method: str, p: dict) -> None:
        try:
            result = await self._answer_request(method, p)
            await codex_server.respond(rid, result)
        except Exception as e:  # noqa: BLE001
            await codex_server.respond(rid, error={"code": -32000, "message": str(e)})

    async def _answer_request(self, method: str, p: dict) -> dict:
        decision_map = {"allow": "accept", "allow_always": "acceptForSession", "deny": "decline"}
        if method == "item/commandExecution/requestApproval":
            res = await self.ask({"kind": "command", "title": "Executar comando?", "tool": "Shell",
                                  "input": {"command": p.get("command"), "cwd": p.get("cwd")},
                                  "detail": p.get("reason"), "can_always": True})
            return {"decision": decision_map[res.get("decision", "deny")]}
        if method == "item/fileChange/requestApproval":
            item = self.items.get(p.get("itemId")) or {}
            diff = "\n\n".join(f"--- {c.get('path')}\n{c.get('diff', '')}" for c in item.get("changes") or [])
            res = await self.ask({"kind": "file", "title": "Aplicar alterações em arquivos?", "tool": "Editar arquivos",
                                  "detail": p.get("reason"), "diff": _clip(diff, 15000), "can_always": True})
            return {"decision": decision_map[res.get("decision", "deny")]}
        if method == "item/tool/requestUserInput":
            res = await self.ask({"kind": "question", "title": "O Codex tem perguntas",
                                  "questions": [{"id": q.get("id"), "header": q.get("header", ""),
                                                 "question": q.get("question", ""), "options": q.get("options") or [],
                                                 "multiSelect": False, "isOther": q.get("isOther", True)}
                                                for q in p.get("questions") or []]})
            answers = res.get("answers") or {}
            return {"answers": {qid: {"answers": a if isinstance(a, list) else [a]} for qid, a in answers.items()}}
        if method == "item/permissions/requestApproval":
            res = await self.ask({"kind": "tool", "title": "Conceder permissões extras?", "tool": "Permissões",
                                  "input": p.get("permissions"), "detail": p.get("reason"), "can_always": True})
            if res.get("decision") == "deny":
                return {"permissions": {}}
            return {"permissions": p.get("permissions") or {},
                    "scope": "session" if res.get("decision") == "allow_always" else "turn"}
        if method == "mcpServer/elicitation/request":
            detail = p.get("message", "")
            if p.get("mode") == "url":
                detail += f"\n\n{p.get('url')}"
            res = await self.ask({"kind": "tool", "title": f"MCP {p.get('serverName')} pede confirmação",
                                  "tool": f"mcp:{p.get('serverName')}", "detail": detail,
                                  "input": p.get("requestedSchema") if p.get("mode") != "url" else None})
            return {"action": "accept", "content": {}} if res.get("decision") != "deny" else {"action": "decline"}
        raise RuntimeError(f"pedido não suportado: {method}")


def _mcps_do_codex() -> set[str]:
    """Servidores MCP configurados no ~/.codex/config.toml (só esses podem ser desligados por chat)."""
    import tomllib
    try:
        with open(Path.home() / ".codex" / "config.toml", "rb") as f:
            return set((tomllib.load(f).get("mcp_servers") or {}).keys())
    except (OSError, ValueError):
        return set()


def _mcps_bloqueados(agent: dict | None) -> list[str]:
    """"mcp__servidor" (ou "mcp__servidor__*") nas ferramentas bloqueadas do agente = servidor desligado no Codex."""
    existentes = _mcps_do_codex()
    nomes = []
    for t in (agent or {}).get("disallowed_tools") or []:
        if t.startswith("mcp__"):
            nome = t.split("__")[1]
            if nome in existentes and nome not in nomes:
                nomes.append(nome)
    return nomes


def _toml_str(texto: str) -> str:
    return json.dumps(texto, ensure_ascii=False)  # as escapes do JSON valem como string básica do TOML


def _papel_codex(sub: dict) -> Path:
    """Arquivo de configuração do papel (subagente) do Codex: modelo, esforço e instruções.
    (MCPs não entram aqui: o Codex lê [mcp_servers.x] do papel como um servidor novo e rejeita o papel inteiro.
    O subagente herda os MCPs desligados na conversa que o chamou.)"""
    linhas = []
    if sub.get("provider") == "codex" and sub.get("model"):
        linhas.append(f"model = {_toml_str(sub['model'])}")
    if sub.get("provider") == "codex" and sub.get("effort"):
        linhas.append(f"model_reasoning_effort = {_toml_str(sub['effort'])}")
    instrucoes = "\n\n".join(p for p in (IDIOMA, FUSO, _pasta_do_agente(sub), sub.get("prompt") or "") if p)
    linhas.append(f"developer_instructions = {_toml_str(instrucoes)}")
    pasta = store.DATA_DIR / "codex-papeis"
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / f"{sub['id']}.toml"
    conteudo = "\n".join(linhas) + "\n"
    if not arquivo.exists() or arquivo.read_text(encoding="utf-8") != conteudo:
        arquivo.write_text(conteudo, encoding="utf-8")
    return arquivo


def _config_codex(agent: dict | None) -> dict:
    """Configuração extra da conversa no Codex (chaves pontuadas, que não apagam o resto do config.toml):
    - subagentes: no chat de agente, os marcados nele; no chat livre, todos os agentes Codex do Agent Hub.
      Cada um vira um papel do Codex, chamado pela ferramenta spawn_agent com agent_type = nome do papel;
    - MCPs bloqueados no agente ficam desligados (ex.: o do Power BI, que pede login da Microsoft)."""
    config: dict[str, Any] = {}
    if agent:
        ids = agent.get("subagents") or []
    else:
        ids = [a["id"] for a in store.agents.all() if a.get("provider") == "codex"]
    for sub_id in ids:
        sub = store.agents.get(sub_id)
        if not sub or sub_id == (agent or {}).get("id"):
            continue
        papel = _slug_papel(sub["name"])
        config[f"agents.{papel}.description"] = sub.get("description") or sub["name"]
        config[f"agents.{papel}.config_file"] = str(_papel_codex(sub)).replace("\\", "/")
    # MCPs desligados: os do agente e os dos subagentes dele (o subagente herda a configuração desta conversa).
    for quem in [agent] + [store.agents.get(i) for i in ids]:
        for nome in _mcps_bloqueados(quem):
            config[f"mcp_servers.{nome}.enabled"] = False
    return config


def _slug_papel(nome: str) -> str:
    """Nome do papel: o do agente sem o "(Codex)" do fim, para casar com o nome usado nas instruções."""
    import re
    return _slug(re.sub(r"\s*\([^)]*\)\s*$", "", nome))


def _sandbox_label(sandbox) -> str:
    if isinstance(sandbox, dict):
        return sandbox.get("type", "?")
    return str(sandbox)


def codex_tool_from_item(item: dict) -> dict | None:
    """Nome e entrada de uma ação do Codex (comando, edição, MCP…), ou None se o item não for uma ação."""
    t = item.get("type")
    if t == "commandExecution":
        return {"name": "Shell", "input": {"command": item.get("command"), "cwd": item.get("cwd")}}
    if t == "fileChange":
        return {"name": "Editar arquivos", "input": {"arquivos": [
            f"{(c.get('kind') or {}).get('type', '?')} {c.get('path')}" for c in item.get("changes") or []]}}
    if t == "mcpToolCall":
        return {"name": f"mcp:{item.get('server')}/{item.get('tool')}", "input": item.get("arguments")}
    if t == "dynamicToolCall":
        return {"name": item.get("tool") or "ferramenta", "input": item.get("arguments")}
    if t == "webSearch":
        return {"name": "WebSearch", "input": {"query": item.get("query")}}
    if t == "collabAgentToolCall":
        return {"name": f"Subagente ({item.get('tool')})", "input": {"prompt": item.get("prompt"),
                                                                    "model": item.get("model")}}
    if t == "imageGeneration":
        return {"name": "Gerar imagem", "input": {"prompt": item.get("revisedPrompt")}}
    return None


def codex_item_events(item: dict, include_tool_start: bool = True, include_user: bool = False) -> list[dict]:
    """Converte um item concluído do Codex em eventos do Agent Hub (usado ao vivo e na importação)."""
    t = item.get("type")
    iid = item.get("id")
    if t == "userMessage":
        if not include_user:
            return []
        text = "\n".join(c.get("text", "") if c.get("type") == "text" else "[imagem]"
                         for c in item.get("content") or [])
        return [{"kind": "user", "text": text}] if text.strip() else []
    if t == "agentMessage":
        return [{"kind": "assistant", "text": item["text"]}] if (item.get("text") or "").strip() else []
    if t == "reasoning":
        text = "\n".join(item.get("summary") or []) or "\n".join(item.get("content") or [])
        return [{"kind": "thinking", "text": text}] if text.strip() else []
    if t == "plan":
        return [{"kind": "assistant", "text": "**Plano proposto**\n\n" + (item.get("text") or "")}]
    if t == "contextCompaction":
        return [{"kind": "system", "text": "Contexto compactado."}]
    tool = codex_tool_from_item(item)
    if not tool:
        return []
    events = [{"kind": "tool", "id": iid, **tool}] if include_tool_start else []
    status = item.get("status")
    is_error = status in ("failed", "declined") or (t == "commandExecution" and item.get("exitCode") not in (0, None))
    if t == "commandExecution":
        out = item.get("aggregatedOutput") or ""
        if item.get("exitCode") not in (0, None):
            out += f"\n[código de saída {item.get('exitCode')}]"
        if status == "declined":
            out = "Recusado." + ("\n" + out if out.strip() else "")
    elif t == "fileChange":
        out = "\n\n".join(f"--- {c.get('path')}\n{c.get('diff', '')}" for c in item.get("changes") or [])
    elif t == "mcpToolCall":
        if item.get("error"):
            out = json.dumps(item["error"], ensure_ascii=False)
        else:
            out = _content_text((item.get("result") or {}).get("content"))
    elif t == "dynamicToolCall":
        out = _content_text(item.get("contentItems"))
    elif t == "webSearch":
        out = json.dumps(item.get("results") or item.get("action"), ensure_ascii=False)[:4000]
    else:
        out = status or ""
    events.append({"kind": "tool_result", "id": iid, "output": _clip(out), "is_error": is_error})
    return events


# =============================================================================================
# Gerenciador
# =============================================================================================
class Manager:
    def __init__(self):
        self.sessions: dict[str, Session] = {}

    def get(self, chat_id: str) -> Session:
        session = self.sessions.get(chat_id)
        if session is None:
            chat = store.chats.get(chat_id)
            if not chat:
                raise KeyError(chat_id)
            session = ClaudeSession(chat) if chat["provider"] == "claude" else CodexSession(chat)
            self.sessions[chat_id] = session
        return session

    def statuses(self) -> dict[str, str]:
        return {cid: s.status for cid, s in self.sessions.items()}

    def all_pending(self) -> list[dict]:
        return [{"chat_id": cid, "request": r} for cid, s in self.sessions.items() for r in s.pending_requests()]

    async def close(self, chat_id: str) -> None:
        session = self.sessions.pop(chat_id, None)
        if session:
            await session.close()

    async def close_all(self) -> None:
        for cid in list(self.sessions):
            await self.close(cid)
        if codex_server.proc and codex_server.proc.returncode is None:
            codex_server.proc.terminate()

    async def reap_idle(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.time()
            for cid, s in list(self.sessions.items()):
                if (s.status == "idle" and not getattr(s, "tasks_running", None)
                        and now - s.last_activity > IDLE_DISCONNECT_SECONDS):
                    await self.close(cid)


manager = Manager()
