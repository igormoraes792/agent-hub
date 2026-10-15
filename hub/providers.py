"""Descoberta dos motores (Claude Agent SDK e Codex CLI) e dos modelos disponíveis."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

from .store import DATA_DIR

HOME = Path.home()
CLAUDE_META_CACHE = DATA_DIR / "claude_meta.json"

# Usados até a primeira consulta ao Claude terminar (ou se ela falhar).
CLAUDE_FALLBACK_MODELS = [
    {"value": "default", "displayName": "Padrão (recomendado)", "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
    {"value": "opus", "displayName": "Opus", "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
    {"value": "sonnet", "displayName": "Sonnet", "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
    {"value": "haiku", "displayName": "Haiku", "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
]

CLAUDE_PERMISSION_MODES = [
    {"value": "default", "label": "Perguntar antes de agir"},
    {"value": "acceptEdits", "label": "Aceitar edições automaticamente"},
    {"value": "plan", "label": "Modo plano (só planeja)"},
    {"value": "auto", "label": "Auto (classificador decide)"},
    {"value": "bypassPermissions", "label": "Sem perguntar nada (perigoso)"},
]

CODEX_SANDBOX_MODES = [
    {"value": "read-only", "label": "Somente leitura"},
    {"value": "workspace-write", "label": "Pode editar a pasta do chat"},
    {"value": "danger-full-access", "label": "Acesso total (perigoso)"},
]

# O Claude Code informa estes textos em inglês. O nome técnico do subagente (chave) não muda,
# porque é por ele que o Claude chama o subagente; só o que aparece na tela é traduzido.
CLAUDE_AGENT_PT = {
    "claude": ("Geral", "Agente para qualquer tarefa que não se encaixe em um agente mais específico. "
                        "É o usado quando nenhum agente é indicado."),
    "Explore": ("Explorador", "Busca rápida e somente leitura para localizar código: encontra arquivos por padrão "
                              "(ex.: \"src/**/*.tsx\"), procura nomes e palavras-chave e responde \"onde X está definido\" "
                              "ou \"quais arquivos usam Y\". Não serve para revisão de código nem análises que exigem ler "
                              "arquivos inteiros."),
    "general-purpose": ("Uso geral", "Pesquisa perguntas complexas, procura código e executa tarefas de várias etapas. "
                                     "Indicado quando a busca por um arquivo ou termo provavelmente vai exigir várias tentativas."),
    "Plan": ("Planejador", "Arquiteto de software que monta planos de implementação: estratégia passo a passo, "
                           "arquivos críticos e prós e contras de cada abordagem."),
    "statusline-setup": ("Linha de status", "Configura a linha de status do Claude Code."),
}

_MODEL_TEXT_PT = {
    "Default (recommended)": "Padrão (recomendado)",
    "Best for everyday, complex tasks": "Melhor para tarefas complexas do dia a dia",
    "For complex work and everyday tasks": "Para trabalhos complexos e do dia a dia",
    "For your toughest challenges": "Para os desafios mais difíceis",
    "Most efficient for simpler tasks": "Mais eficiente para tarefas simples",
    "Fastest for quick answers": "O mais rápido, para respostas curtas",
    "Efficient for routine tasks": "Eficiente para tarefas de rotina",
    "Most capable for your hardest and longest-running tasks": "O mais capaz, para tarefas difíceis e longas",
}


def _pt(text: str | None) -> str | None:
    """Traduz textos conhecidos do Claude Code; partes separadas por ' · ' são traduzidas uma a uma."""
    if not text:
        return text
    return " · ".join(_MODEL_TEXT_PT.get(part.strip(), part.strip()) for part in text.split("·"))


def _claude_agents_pt(agents: list[dict]) -> list[dict]:
    out = []
    for a in agents:
        label, desc = CLAUDE_AGENT_PT.get(a.get("name"), (None, None))
        out.append({**a, "label": label, "description": desc or a.get("description", "")})
    return out


def _claude_models_pt(models: list[dict]) -> list[dict]:
    return [{**m, "displayName": _pt(m.get("displayName")), "description": _pt(m.get("description"))} for m in models]


EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"]
CODEX_EFFORT_LEVELS = ["minimal", "low", "medium", "high", "xhigh"]


def find_codex() -> str | None:
    """Prefere CODEX_PATH, depois `codex` no PATH, depois o binário da extensão do VS Code mais recente."""
    env = os.environ.get("CODEX_PATH")
    if env and Path(env).exists():
        return env
    on_path = shutil.which("codex")
    if on_path:
        return on_path
    candidates = sorted(
        (HOME / ".vscode" / "extensions").glob("openai.chatgpt-*/bin/*/codex.exe"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return str(candidates[0]) if candidates else None


def codex_models() -> list[dict]:
    try:
        data = json.loads((HOME / ".codex" / "models_cache.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return [
        {"value": m["slug"], "displayName": m.get("display_name", m["slug"]),
         "defaultEffort": m.get("default_reasoning_level")}
        for m in data.get("models", [])
        if m.get("visibility", "list") == "list"
    ]


def codex_default_model() -> str | None:
    cfg = HOME / ".codex" / "config.toml"
    try:
        import tomllib
        return tomllib.loads(cfg.read_text(encoding="utf-8")).get("model")
    except Exception:
        return None


class ClaudeMeta:
    """Modelos, comandos (/slash) e subagentes que o Claude Code expõe. Consultado uma vez em segundo plano."""

    def __init__(self):
        self.data: dict = {}
        try:
            self.data = json.loads(CLAUDE_META_CACHE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        self.error: str | None = None
        self._task: asyncio.Task | None = None

    def refresh_in_background(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._refresh())

    async def _refresh(self) -> None:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        try:
            opts = ClaudeAgentOptions(setting_sources=["user", "project", "local"], cwd=str(HOME))
            async with ClaudeSDKClient(opts) as client:
                info = await client.get_server_info() or {}
            self.data = {
                "models": info.get("models") or [],
                "commands": [
                    {"name": c.get("name"), "description": (c.get("description") or "")[:200],
                     "argumentHint": c.get("argumentHint", "")}
                    for c in info.get("commands") or []
                ],
                "agents": [
                    {"name": a.get("name"), "description": a.get("description") or ""}
                    for a in info.get("agents") or []
                ],
                "version": info.get("claude_code_version"),
            }
            CLAUDE_META_CACHE.parent.mkdir(parents=True, exist_ok=True)
            CLAUDE_META_CACHE.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            self.error = None
        except Exception as e:  # noqa: BLE001 - mostrado na interface
            self.error = f"{type(e).__name__}: {e}"

    def models(self) -> list[dict]:
        return self.data.get("models") or CLAUDE_FALLBACK_MODELS


claude_meta = ClaudeMeta()


def meta() -> dict:
    codex_path = find_codex()
    return {
        "claude": {
            "available": True,
            "version": claude_meta.data.get("version"),
            "error": claude_meta.error,
            "models": _claude_models_pt(claude_meta.models()),
            "commands": claude_meta.data.get("commands", []),
            "agents": _claude_agents_pt(claude_meta.data.get("agents", [])),
            "permissionModes": CLAUDE_PERMISSION_MODES,
            "effortLevels": EFFORT_LEVELS,
        },
        "codex": {
            "available": codex_path is not None,
            "path": codex_path,
            "models": codex_models(),
            "defaultModel": codex_default_model(),
            "sandboxModes": CODEX_SANDBOX_MODES,
            "effortLevels": CODEX_EFFORT_LEVELS,
        },
        "home": str(HOME),
    }
