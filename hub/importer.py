"""Importa conversas existentes do Claude Code e do Codex (VS Code, CLI) para o Agent Hub.

A conversa importada traz o histórico para a tela e, na primeira mensagem enviada daqui, vira uma
ramificação (fork): a original continua intacta onde estava.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import store
from .sessions import _clip, _content_text, codex_item_events, codex_server

_NOISE_TAGS = ("system-reminder", "ide_selection", "ide_opened_file", "ide_diagnostics", "local-command-caveat")
_NOISE_RE = re.compile(r"<(%s)>.*?</\1>" % "|".join(_NOISE_TAGS), re.S)


def _imported_index() -> dict[str, str]:
    """'claude:<id>' / 'codex:<id>' da conversa original -> chat_id do Agent Hub que a importou."""
    return {c["source_id"]: c["id"] for c in store.chats.all() if c.get("source_id")}


def _hub_owned() -> set[str]:
    """Sessões que o próprio Agent Hub criou ou continuou (cópias): não aparecem na lista de importação."""
    owned = set()
    for c in store.chats.all():
        for key in (c.get("session_id") and f"claude:{c['session_id']}", c.get("thread_id") and f"codex:{c['thread_id']}"):
            if key and key != c.get("source_id"):
                owned.add(key)
    return owned


# ----------------------------------------------------------------------------------------- Claude
def list_claude(limit: int = 300) -> list[dict]:
    from claude_agent_sdk import list_sessions

    imported, owned = _imported_index(), _hub_owned()
    out = []
    for s in list_sessions(limit=limit):
        if f"claude:{s.session_id}" in owned:
            continue
        title = s.custom_title or s.summary or (s.first_prompt or "").strip() or s.session_id
        out.append({
            "provider": "claude", "id": s.session_id, "title": title[:120], "cwd": s.cwd,
            "updated": int(s.last_modified) / 1000 if s.last_modified else None,
            "size": s.file_size, "imported_chat_id": imported.get(f"claude:{s.session_id}"),
        })
    out.sort(key=lambda x: x["updated"] or 0, reverse=True)
    return out


def _claude_user_text(text: str) -> tuple[str, str] | None:
    """Limpa o texto de uma mensagem do usuário. Retorna (kind, texto) ou None se for só ruído."""
    if "<local-command-stdout>" in text:
        inner = re.sub(r"</?local-command-stdout>", "", text).strip()
        return ("system", inner) if inner else None
    cmd = re.search(r"<command-name>(.*?)</command-name>", text, re.S)
    if cmd:
        args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
        return "user", f"{cmd.group(1).strip()} {args.group(1).strip() if args else ''}".strip()
    text = _NOISE_RE.sub("", text).strip()
    return ("user", text) if text else None


def _claude_events(session_id: str) -> list[dict]:
    from claude_agent_sdk import get_session_messages

    events: list[dict] = []
    for m in get_session_messages(session_id):
        msg = m.message or {}
        content = msg.get("content")
        parent = m.parent_tool_use_id
        if m.type == "user":
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
            for b in blocks:
                if b.get("type") == "text":
                    cleaned = _claude_user_text(b.get("text", ""))
                    if cleaned and not parent:
                        events.append({"kind": cleaned[0], "text": cleaned[1]})
                elif b.get("type") == "tool_result":
                    events.append({"kind": "tool_result", "id": b.get("tool_use_id"),
                                   "output": _clip(_content_text(b.get("content"))), "is_error": bool(b.get("is_error"))})
                elif b.get("type") == "image" and not parent:
                    events.append({"kind": "user", "text": "[imagem]"})
        elif m.type == "assistant":
            for b in content or []:
                if b.get("type") == "text" and b.get("text", "").strip():
                    events.append({"kind": "assistant", "text": b["text"], "parent": parent})
                elif b.get("type") == "thinking" and b.get("thinking", "").strip():
                    events.append({"kind": "thinking", "text": b["thinking"], "parent": parent})
                elif b.get("type") == "tool_use":
                    events.append({"kind": "tool", "id": b.get("id"), "name": b.get("name"),
                                   "input": b.get("input"), "parent": parent})
    return events


def import_claude(session_id: str) -> dict:
    from claude_agent_sdk import get_session_info

    existing = _imported_index().get(f"claude:{session_id}")
    if existing:
        return store.chats.get(existing)
    info = get_session_info(session_id)
    if not info:
        raise ValueError("sessão do Claude não encontrada")
    events = _claude_events(session_id)
    notes = [f"Importado do Claude Code · {sum(1 for e in events if e['kind'] == 'user')} mensagens suas. "
             "Ao continuar aqui, a conversa vira uma cópia: a original segue intacta no VS Code."]
    cwd = info.cwd or str(Path.home())
    if not os.path.isdir(cwd):
        notes.append(f"A pasta original ({cwd}) não existe mais; ela será recriada vazia ao continuar.")
    chat = store.chats.create({
        "title": (info.custom_title or info.summary or info.first_prompt or "Conversa importada")[:80],
        "provider": "claude", "agent_id": None, "model": "default", "effort": None, "cwd": cwd,
        "permission_mode": "default", "sandbox": "workspace-write", "approval_policy": "on-request",
        "session_id": session_id, "thread_id": None, "auto_title": False,
        "source_id": f"claude:{session_id}", "fork_pending": True,
    })
    store.append_events(chat["id"], [{"kind": "system", "text": n} for n in notes] + events)
    return chat


# ------------------------------------------------------------------------------------------ Codex
async def list_codex(limit: int = 300) -> list[dict]:
    imported, owned = _imported_index(), _hub_owned()
    out, cursor = [], None
    while len(out) < limit:
        params = {"limit": 100, "sortKey": "updated_at"}
        if cursor:
            params["cursor"] = cursor
        res = await codex_server.request("thread/list", params, timeout=60)
        for t in res.get("data") or []:
            source = str(t.get("source") or "")
            if t.get("ephemeral") or source.startswith("subAgent") or f"codex:{t['id']}" in owned:
                continue
            out.append({
                "provider": "codex", "id": t["id"], "title": (t.get("name") or t.get("preview") or t["id"])[:120],
                "cwd": t.get("cwd"), "updated": t.get("updatedAt"), "source": source, "model": t.get("model"),
                "imported_chat_id": imported.get(f"codex:{t['id']}"),
            })
        cursor = res.get("nextCursor")
        if not cursor:
            break
    return out


async def import_codex(thread_id: str) -> dict:
    existing = _imported_index().get(f"codex:{thread_id}")
    if existing:
        return store.chats.get(existing)
    res = await codex_server.request("thread/read", {"threadId": thread_id, "includeTurns": True}, timeout=60)
    thread = res["thread"]
    events: list[dict] = []
    for turn in thread.get("turns") or []:
        for item in turn.get("items") or []:
            events.extend(codex_item_events(item, include_user=True))
        if turn.get("status") == "failed" and turn.get("error"):
            events.append({"kind": "error", "text": turn["error"].get("message", "Turno falhou.")})
    notes = [f"Importado do Codex · {sum(1 for e in events if e['kind'] == 'user')} mensagens suas. "
             "Ao continuar aqui, a conversa vira uma cópia: a original segue intacta no VS Code."]
    cwd = thread.get("cwd") or str(Path.home())
    if not os.path.isdir(cwd):
        notes.append(f"A pasta original ({cwd}) não existe mais; o chat vai continuar na sua pasta pessoal.")
        cwd = str(Path.home())
    chat = store.chats.create({
        "title": (thread.get("name") or thread.get("preview") or "Conversa importada")[:80],
        "provider": "codex", "agent_id": None, "model": thread.get("model"), "effort": thread.get("reasoningEffort"),
        "cwd": cwd, "permission_mode": "default", "sandbox": "workspace-write", "approval_policy": "on-request",
        "session_id": None, "thread_id": thread_id, "auto_title": False,
        "source_id": f"codex:{thread_id}", "fork_pending": True,
    })
    store.append_events(chat["id"], [{"kind": "system", "text": n} for n in notes] + events)
    return chat
