"""Gestão e inspeção das contas de IA (Claude Code e Codex), com suporte a múltiplos perfis,
login direto sem VS Code e alternância rápida de contas.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .store import DATA_DIR

HOME = Path.home()
SETTINGS_FILE = DATA_DIR / "hub_settings.json"
PROFILES_FILE = DATA_DIR / "account_profiles.json"


def _read_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_settings(data: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_profiles() -> dict:
    if not PROFILES_FILE.exists():
        return {"claude": [], "codex": [], "antigravity": []}
    try:
        data = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"claude": [], "codex": [], "antigravity": []}
        data.setdefault("claude", [])
        data.setdefault("codex", [])
        data.setdefault("antigravity", [])
        return data
    except Exception:
        return {"claude": [], "codex": [], "antigravity": []}


def _write_profiles(data: dict) -> None:
    PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROFILES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


PROVEDORES = ("claude", "codex", "antigravity")
CAMPOS_PADRAO = ("model", "effort", "permission_mode", "sandbox", "approval_policy")


def padroes_chat() -> dict:
    """Como os chats novos abrem, escolhido em Configurações → Contas: por IA (modelo, esforço, permissões; no Codex
    sandbox e aprovação) e uma pasta de trabalho para todas. Campo ausente = padrão do hub. Cada chat pode mudar o seu."""
    settings = _read_settings()
    salvos = settings.get("padroes_chat") if isinstance(settings.get("padroes_chat"), dict) else {}
    antigos = settings.get("modelos_padrao") if isinstance(settings.get("modelos_padrao"), dict) else {}  # versão anterior
    saida: dict = {"cwd": salvos.get("cwd") or ""}
    for prov in PROVEDORES:
        valores = dict(salvos.get(prov) or {})
        if not valores.get("model") and antigos.get(prov):
            valores["model"] = antigos[prov]
        saida[prov] = {k: str(v)[:100] for k, v in valores.items() if k in CAMPOS_PADRAO and v}
    return saida


def modelos_padrao() -> dict:
    return {p: v["model"] for p, v in padroes_chat().items() if p in PROVEDORES and v.get("model")}


def definir_padroes(provider: str | None, valores: dict) -> dict:
    """provider + valores dos campos da IA, ou provider None + {"cwd": pasta} para a pasta de todas."""
    import os

    atuais = padroes_chat()
    if provider is None:
        cwd = (valores.get("cwd") or "").strip()
        if cwd and not os.path.isdir(cwd):
            raise ValueError(f"A pasta não existe: {cwd}")
        atuais["cwd"] = os.path.normpath(cwd) if cwd else ""
    elif provider in PROVEDORES:
        atuais[provider] = {k: str(v)[:100] for k, v in valores.items() if k in CAMPOS_PADRAO and v not in (None, "")}
    else:
        raise ValueError(f"Provedor {provider} desconhecido.")
    settings = _read_settings()
    settings["padroes_chat"] = atuais
    settings.pop("modelos_padrao", None)  # já migrado para padroes_chat
    _write_settings(settings)
    return atuais


def find_bundled_claude() -> str | None:
    """Procura o executável do Claude (o do SDK no .venv ou no PATH)."""
    venv_claude = Path(__file__).resolve().parent.parent / ".venv" / "Lib" / "site-packages" / "claude_agent_sdk" / "_bundled" / "claude.exe"
    if venv_claude.exists():
        return str(venv_claude)
    which = shutil.which("claude")
    if which:
        return which
    return None


def carregar_chaves_ambiente() -> None:
    """Carrega chaves salvas em hub_settings.json para as variáveis de ambiente."""
    st = _read_settings()
    if st.get("anthropic_api_key") and "ANTHROPIC_API_KEY" not in os.environ:
        os.environ["ANTHROPIC_API_KEY"] = st["anthropic_api_key"]
    if st.get("openai_api_key") and "OPENAI_API_KEY" not in os.environ:
        os.environ["OPENAI_API_KEY"] = st["openai_api_key"]
    if st.get("gemini_api_key") and "GEMINI_API_KEY" not in os.environ:
        os.environ["GEMINI_API_KEY"] = st["gemini_api_key"]


def get_antigravity_account(meta_info: dict | None = None) -> dict:
    from .providers import find_antigravity

    gemini_dir = HOME / ".gemini"
    accounts_file = gemini_dir / "google_accounts.json"
    if not accounts_file.exists():
        accounts_file = gemini_dir / "google_accounts.json.backup"

    email = None
    if accounts_file.exists():
        try:
            data = json.loads(accounts_file.read_text(encoding="utf-8"))
            email = data.get("active")
        except Exception:
            pass

    settings = _read_settings()
    custom_key = settings.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY")
    masked_key = f"{custom_key[:7]}...{custom_key[-4:]}" if custom_key and len(custom_key) > 12 else ("Configurada" if custom_key else None)

    available = False
    agy_path = None
    models_count = 0
    if meta_info and "antigravity" in meta_info:
        available = meta_info["antigravity"].get("available", False)
        agy_path = meta_info["antigravity"].get("path")
        models_count = len(meta_info["antigravity"].get("models", []))
    else:
        agy_path = find_antigravity()
        available = agy_path is not None
        models_count = 6

    status = "connected" if (email or custom_key) and available else ("disconnected" if available else "unavailable")

    profiles_data = _read_profiles()
    ag_profiles = profiles_data.get("antigravity", [])
    if email:
        existing = next((p for p in ag_profiles if p.get("email") == email), None)
        if not existing:
            ag_profiles.append({
                "id": email,
                "label": f"Conta Google ({email})",
                "email": email,
                "type": "google_account",
                "saved_at": time.time(),
            })
            profiles_data["antigravity"] = ag_profiles
            _write_profiles(profiles_data)

    for p in ag_profiles:
        p["active"] = bool(email and p.get("email") == email) or bool(custom_key and p.get("type") == "api_key" and p.get("key") == custom_key)

    return {
        "id": "antigravity",
        "name": "Antigravity",
        "titulo": "Google Antigravity / Gemini CLI",
        "provider": "antigravity",
        "status": status,
        "available": available,
        "path": agy_path,
        "email": email,
        "models_count": models_count,
        "has_custom_key": bool(custom_key),
        "masked_key": masked_key,
        "config_path": str(gemini_dir),
        "profiles": ag_profiles,
    }


_STATUS_CLAUDE: dict = {"ts": 0.0, "dados": None}


def _claude_auth_status() -> dict | None:
    """Login real do Claude (`claude auth status`): {"loggedIn": bool, "email": ...}. Sem isto, o hub achava que
    estava conectado só porque o programa existe, e numa máquina nova o primeiro chat falhava em vez de pedir login.
    Cache de 5 s: a tela consulta a cada 2,5 s enquanto espera o login no navegador."""
    if time.time() - _STATUS_CLAUDE["ts"] < 5:
        return _STATUS_CLAUDE["dados"]
    dados = None
    exe = find_bundled_claude()
    if exe:
        try:
            r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            dados = json.loads(r.stdout[r.stdout.find("{"):]) if "{" in r.stdout else None
        except Exception:  # noqa: BLE001 - sem resposta: decide pelos sinais antigos
            dados = None
    _STATUS_CLAUDE.update(ts=time.time(), dados=dados)
    return dados


def get_claude_account(meta_info: dict | None = None) -> dict:
    claude_json = HOME / ".claude.json"
    data = {}
    if claude_json.exists():
        try:
            data = json.loads(claude_json.read_text(encoding="utf-8"))
        except Exception:
            pass

    user_id = data.get("userID")
    first_start = data.get("firstStartTime")
    settings = _read_settings()
    custom_key = settings.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
    masked_key = f"{custom_key[:7]}...{custom_key[-4:]}" if custom_key and len(custom_key) > 12 else ("Configurada" if custom_key else None)

    version = None
    models_count = 0
    error = None
    if meta_info and "claude" in meta_info:
        version = meta_info["claude"].get("version")
        models_count = len(meta_info["claude"].get("models", []))
        error = meta_info["claude"].get("error")

    auth = _claude_auth_status()
    # sem resposta do `auth status`, mantém o critério antigo para não bloquear quem já usa
    logado = bool(auth.get("loggedIn")) if isinstance(auth, dict) else bool(version or user_id)
    status = "connected" if (logado or custom_key) and not error else ("error" if error else "disconnected")

    profiles = _read_profiles().get("claude", [])

    return {
        "id": "claude",
        "name": "Claude",
        "titulo": "Claude Code / Agent SDK",
        "provider": "claude",
        "status": status,
        "auth_type": "api_key" if custom_key else "cli_login",
        "user_id": user_id,
        "email": (auth or {}).get("email") if isinstance(auth, dict) else None,
        "org": (auth or {}).get("orgName") if isinstance(auth, dict) else None,
        "first_start": first_start,
        "version": version,
        "error": error,
        "models_count": models_count,
        "has_custom_key": bool(custom_key),
        "masked_key": masked_key,
        "config_path": str(HOME / ".claude"),
        "profiles": profiles,
    }


def get_codex_account(meta_info: dict | None = None) -> dict:
    auth_json = HOME / ".codex" / "auth.json"
    data = {}
    email = None
    user_name = None
    auth_mode = None
    account_id = None
    last_refresh = None

    if auth_json.exists():
        try:
            data = json.loads(auth_json.read_text(encoding="utf-8"))
            auth_mode = data.get("auth_mode")
            last_refresh = data.get("last_refresh")
            tokens = data.get("tokens") or {}
            account_id = tokens.get("account_id")
            id_token = tokens.get("id_token")
            if id_token:
                parts = id_token.split(".")
                if len(parts) >= 2:
                    p = parts[1] + "=" * (-len(parts[1]) % 4)
                    claims = json.loads(base64.urlsafe_b64decode(p))
                    email = claims.get("email")
                    user_name = claims.get("name")
        except Exception:
            pass

    settings = _read_settings()
    custom_key = settings.get("openai_api_key") or os.environ.get("OPENAI_API_KEY")
    masked_key = f"{custom_key[:7]}...{custom_key[-4:]}" if custom_key and len(custom_key) > 12 else ("Configurada" if custom_key else None)

    available = False
    codex_path = None
    default_model = None
    if meta_info and "codex" in meta_info:
        available = meta_info["codex"].get("available", False)
        codex_path = meta_info["codex"].get("path")
        default_model = meta_info["codex"].get("defaultModel")

    status = "connected" if (email or account_id or custom_key or auth_mode) and available else "disconnected"

    # Salva automaticamente o perfil atual se ainda não estiver na lista de perfis salvos
    profiles_data = _read_profiles()
    codex_profiles = profiles_data.get("codex", [])
    if data and (email or account_id):
        p_id = account_id or email
        existing = next((p for p in codex_profiles if p.get("id") == p_id or (email and p.get("email") == email)), None)
        if not existing:
            label = f"{user_name} ({email})" if user_name and email else (email or user_name or "Conta ChatGPT")
            codex_profiles.append({
                "id": p_id,
                "label": label,
                "email": email,
                "user_name": user_name,
                "account_id": account_id,
                "auth_mode": auth_mode,
                "auth_data": data,
                "saved_at": time.time(),
            })
            profiles_data["codex"] = codex_profiles
            _write_profiles(profiles_data)
        else:
            # Mantém os dados da sessão atualizados
            existing["auth_data"] = data
            existing["last_refresh"] = last_refresh
            _write_profiles(profiles_data)

    # Identifica qual perfil está ativo
    for p in codex_profiles:
        p["active"] = bool(account_id and p.get("account_id") == account_id) or bool(email and p.get("email") == email)

    return {
        "id": "codex",
        "name": "Codex",
        "titulo": "Codex app-server / ChatGPT",
        "provider": "codex",
        "status": status,
        "available": available,
        "path": codex_path,
        "auth_mode": auth_mode or ("api_key" if custom_key else "none"),
        "email": email,
        "user_name": user_name,
        "account_id": account_id,
        "last_refresh": last_refresh,
        "default_model": default_model,
        "has_custom_key": bool(custom_key),
        "masked_key": masked_key,
        "config_path": str(HOME / ".codex"),
        "profiles": codex_profiles,
    }


def get_all_accounts(meta_info: dict | None = None) -> list[dict]:
    return [get_antigravity_account(meta_info), get_claude_account(meta_info), get_codex_account(meta_info)]


def set_account_key(provider: str, key: str | None, label: str | None = None) -> None:
    settings = _read_settings()
    key_map = {
        "claude": ("anthropic_api_key", "ANTHROPIC_API_KEY"),
        "codex": ("openai_api_key", "OPENAI_API_KEY"),
        "antigravity": ("gemini_api_key", "GEMINI_API_KEY"),
    }
    if provider not in key_map:
        raise ValueError(f"Provedor {provider} desconhecido.")
    key_name, env_var = key_map[provider]
    if key and key.strip():
        k = key.strip()
        settings[key_name] = k
        os.environ[env_var] = k
        # Também adiciona aos perfis do provedor
        profiles_data = _read_profiles()
        p_list = profiles_data.get(provider, [])
        p_id = f"key_{k[-6:]}"
        masked = f"{k[:7]}...{k[-4:]}" if len(k) > 12 else "Chave API"
        lbl = label or f"Chave API ({masked})"
        existing = next((p for p in p_list if p.get("id") == p_id), None)
        if not existing:
            p_list.append({
                "id": p_id,
                "label": lbl,
                "type": "api_key",
                "key": k,
                "masked_key": masked,
                "saved_at": time.time(),
            })
            profiles_data[provider] = p_list
            _write_profiles(profiles_data)
    else:
        settings.pop(key_name, None)
        os.environ.pop(env_var, None)
    _write_settings(settings)


def iniciar_login(provider: str) -> dict:
    """Dispara a janela interativa de login para o provedor escolhido sem precisar de VS Code."""
    from .providers import find_antigravity, find_codex

    _STATUS_CLAUDE["ts"] = 0.0  # a próxima consulta já pega o login novo

    if provider == "antigravity":
        agy_path = find_antigravity()
        if not agy_path:
            raise RuntimeError("agy.exe não encontrado no sistema.")
        subprocess.Popen(f'start "Google Antigravity" "{agy_path}"', shell=True)
        return {"status": "started", "message": "Google Antigravity iniciado."}

    if provider == "codex":
        codex_path = find_codex()
        if not codex_path:
            raise RuntimeError("codex.exe/codex.cmd não encontrado no sistema.")
        # Abre o cmd executando codex login (ele abrirá o navegador para a autenticação)
        subprocess.Popen(f'start "Login Codex" "{codex_path}" login', shell=True)
        return {"status": "started", "message": "Janela de login do Codex iniciada. Conclua no navegador."}

    if provider == "claude":
        claude_path = find_bundled_claude()
        if not claude_path:
            raise RuntimeError("claude.exe não encontrado no ambiente.")
        subprocess.Popen(f'start "Login Claude" "{claude_path}" auth login', shell=True)
        return {"status": "started", "message": "Janela de login do Claude iniciada. Conclua no navegador."}

    raise ValueError(f"Provedor {provider} não suportado para login.")


def fazer_logout(provider: str) -> dict:
    """Desconecta a conta ativa do provedor."""
    from .providers import find_codex

    _STATUS_CLAUDE["ts"] = 0.0

    if provider == "antigravity":
        set_account_key("antigravity", None)
        return {"status": "ok", "message": "Desconectado do Antigravity com sucesso."}

    if provider == "codex":
        codex_path = find_codex()
        auth_file = HOME / ".codex" / "auth.json"
        if auth_file.exists():
            try:
                auth_file.unlink()
            except Exception:
                pass
        if codex_path:
            try:
                subprocess.run(f'"{codex_path}" logout', shell=True, capture_output=True, timeout=8)
            except Exception:
                pass
        set_account_key("codex", None)
        return {"status": "ok", "message": "Desconectado do Codex com sucesso."}

    if provider == "claude":
        claude_path = find_bundled_claude()
        if claude_path:
            try:
                subprocess.run(f'"{claude_path}" auth logout', shell=True, capture_output=True, timeout=8)
            except Exception:
                pass
        set_account_key("claude", None)
        return {"status": "ok", "message": "Desconectado do Claude com sucesso."}

    raise ValueError(f"Provedor {provider} desconhecido.")


def switch_profile(provider: str, profile_id: str) -> dict:
    """Alterna a conta ativa para o perfil salvo correspondente."""
    profiles_data = _read_profiles()
    p_list = profiles_data.get(provider, [])
    target = next((p for p in p_list if p.get("id") == profile_id), None)
    if not target:
        raise ValueError("Perfil não encontrado.")

    if provider == "antigravity":
        if target.get("type") == "api_key":
            set_account_key("antigravity", target["key"])
        else:
            acc_file = HOME / ".gemini" / "google_accounts.json"
            if target.get("email"):
                try:
                    acc_file.parent.mkdir(parents=True, exist_ok=True)
                    acc_file.write_text(json.dumps({"active": target["email"], "old": []}, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
            set_account_key("antigravity", None)
        return {"status": "ok", "active": target["label"]}

    if provider == "codex":
        if target.get("type") == "api_key":
            set_account_key("codex", target["key"])
        else:
            auth_file = HOME / ".codex" / "auth.json"
            auth_file.parent.mkdir(parents=True, exist_ok=True)
            auth_file.write_text(json.dumps(target["auth_data"], ensure_ascii=False, indent=2), encoding="utf-8")
            set_account_key("codex", None)
        return {"status": "ok", "active": target["label"]}

    if provider == "claude":
        if target.get("type") == "api_key":
            set_account_key("claude", target["key"])
        return {"status": "ok", "active": target["label"]}

    raise ValueError(f"Provedor {provider} desconhecido.")


def delete_profile(provider: str, profile_id: str) -> dict:
    """Remove um perfil salvo da lista."""
    profiles_data = _read_profiles()
    p_list = profiles_data.get(provider, [])
    profiles_data[provider] = [p for p in p_list if p.get("id") != profile_id]
    _write_profiles(profiles_data)
    return {"status": "ok"}
