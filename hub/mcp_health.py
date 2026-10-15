"""Verifica os servidores MCP configurados no Claude e no Codex e corrige caminhos quebrados.

Caso típico: um MCP que roda de dentro de uma extensão do VS Code (ex.: Power BI). O caminho inclui a
versão da extensão; quando ela atualiza, a pasta muda e o MCP para de abrir. Aqui detectamos isso e
apontamos para a versão instalada mais nova.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import tomllib
from pathlib import Path

HOME = Path.home()
CLAUDE_JSON = HOME / ".claude.json"
CODEX_TOML = HOME / ".codex" / "config.toml"

_EXT_RE = re.compile(
    r"^(?P<root>.*[\\/]\.vscode[\\/]extensions[\\/])(?P<ext>[^\\/]+?)-(?P<ver>\d+(?:\.\d+)+)(?P<plat>-[a-z0-9]+-[a-z0-9]+)?(?P<rest>[\\/].*)$",
    re.I)
_FILE_ARG_RE = re.compile(r"\.(ps1|py|js|mjs|exe|cmd|bat)$", re.I)


def _version_key(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def _newer_extension_path(path: str) -> str | None:
    """Se `path` está dentro de uma extensão do VS Code, devolve o mesmo arquivo na versão instalada mais nova."""
    m = _EXT_RE.match(path)
    if not m:
        return None
    root, ext, plat, rest = m["root"], m["ext"], m["plat"] or "", m["rest"]
    best, best_ver = None, None
    for d in Path(root).glob(f"{ext}-*"):
        dm = re.fullmatch(re.escape(ext) + r"-(\d+(?:\.\d+)+)" + re.escape(plat), d.name, re.I)
        if not dm or not (Path(str(d) + rest)).exists():
            continue
        if best_ver is None or _version_key(dm[1]) > _version_key(best_ver):
            best, best_ver = root + d.name + rest, dm[1]
    if best and _version_key(best_ver) > _version_key(m["ver"]):
        return best.replace("\\", "/") if "/" in path else best
    return None


def _exists(cmd: str) -> bool:
    if "/" in cmd or "\\" in cmd:
        return Path(cmd).exists()
    return shutil.which(cmd) is not None


def _check(ia: str, scope: str, name: str, cfg: dict, origin: str, project: str | None = None) -> dict:
    entry = {"ia": ia, "scope": scope, "name": name, "origin": origin, "project": project,
             "command": cfg.get("command") or cfg.get("url"), "status": "ok", "detail": "", "fix": None}
    if not cfg.get("command"):
        entry["status"], entry["detail"] = "remote", "servidor remoto (HTTP), não há executável local"
        return entry
    paths = [cfg["command"]] + [a for a in cfg.get("args") or [] if isinstance(a, str) and _FILE_ARG_RE.search(a)
                                and ("/" in a or "\\" in a)]
    missing = [p for p in paths if not _exists(p)]
    replacements = {p: n for p in paths if (n := _newer_extension_path(p))}
    if missing:
        entry["status"] = "missing"
        entry["detail"] = "não encontrado: " + ", ".join(missing)
    elif replacements:
        entry["status"] = "outdated"
        entry["detail"] = "existe uma versão mais nova da extensão; a pasta antiga será apagada pelo VS Code"
    if replacements:
        entry["fix"] = replacements
    elif missing:
        entry["detail"] += " (nenhuma versão instalada encontrada para corrigir sozinho)"
    return entry


def collect() -> list[dict]:
    out: list[dict] = []
    try:
        cj = json.loads(CLAUDE_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cj = {}
    for name, cfg in (cj.get("mcpServers") or {}).items():
        out.append(_check("claude", "user", name, cfg, str(CLAUDE_JSON)))
    for proj, pdata in (cj.get("projects") or {}).items():
        for name, cfg in (pdata.get("mcpServers") or {}).items():
            out.append(_check("claude", "local", name, cfg, str(CLAUDE_JSON), proj))
        mcp_file = Path(proj) / ".mcp.json"
        if mcp_file.is_file():
            try:
                servers = json.loads(mcp_file.read_text(encoding="utf-8")).get("mcpServers") or {}
            except (OSError, json.JSONDecodeError):
                servers = {}
            for name, cfg in servers.items():
                out.append(_check("claude", "project", name, cfg, str(mcp_file), proj))
    try:
        codex = tomllib.loads(CODEX_TOML.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        codex = {}
    for name, cfg in (codex.get("mcp_servers") or {}).items():
        out.append(_check("codex", "global", name, cfg, str(CODEX_TOML)))
    return out


def issues() -> list[dict]:
    return [e for e in collect() if e["status"] in ("missing", "outdated")]


def _backup(path: Path) -> Path:
    bak = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, bak)
    return bak


def _apply_replacements(cfg: dict, repl: dict[str, str]) -> dict:
    cfg = dict(cfg)
    cfg["command"] = repl.get(cfg["command"], cfg["command"])
    cfg["args"] = [repl.get(a, a) if isinstance(a, str) else a for a in cfg.get("args") or []]
    return cfg


def _claude_cli() -> str:
    import claude_agent_sdk
    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled"
    exe = bundled / "claude.exe"
    return str(exe if exe.exists() else bundled / "claude")


def fix(ia: str, scope: str, name: str, project: str | None = None) -> dict:
    entry = next((e for e in collect() if e["ia"] == ia and e["scope"] == scope and e["name"] == name
                  and e.get("project") == project), None)
    if not entry or not entry["fix"]:
        raise ValueError("nada para corrigir neste servidor")
    repl = entry["fix"]

    if ia == "codex":
        bak = _backup(CODEX_TOML)
        text = CODEX_TOML.read_text(encoding="utf-8")
        for old, new in repl.items():
            if old in text:
                text = text.replace(old, new)
            else:  # caminho gravado com barras invertidas escapadas
                text = text.replace(old.replace("\\", "\\\\"), new.replace("\\", "\\\\"))
        CODEX_TOML.write_text(text, encoding="utf-8")
        return {"backup": str(bak), "replaced": repl}

    if scope == "project":
        path = Path(entry["origin"])
        bak = _backup(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["mcpServers"][name] = _apply_replacements(data["mcpServers"][name], repl)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"backup": str(bak), "replaced": repl}

    # Claude, escopo user/local: usa a própria CLI do Claude (o arquivo é reescrito pelo Claude Code em uso).
    bak = _backup(CLAUDE_JSON)
    cj = json.loads(CLAUDE_JSON.read_text(encoding="utf-8"))
    current = (cj.get("mcpServers") or {}).get(name) if scope == "user" else \
        ((cj.get("projects") or {}).get(project) or {}).get("mcpServers", {}).get(name)
    new_cfg = _apply_replacements(current, repl)
    cli, cwd = _claude_cli(), project if scope == "local" else str(HOME)
    for cmd in ([cli, "mcp", "remove", "-s", scope, name],
                [cli, "mcp", "add-json", "-s", scope, name, json.dumps(new_cfg)]):
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(f"falha ao rodar '{' '.join(cmd[1:4])}': {r.stderr or r.stdout}")
    return {"backup": str(bak), "replaced": repl}
