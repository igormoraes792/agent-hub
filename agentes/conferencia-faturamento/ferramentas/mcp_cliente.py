"""Cliente MCP mínimo (stdio) usado pelos scripts de extração.

Abre o mesmo servidor MCP que o Claude Code usa (lido de ~/.claude.json, com ~/.codex/config.toml como
reserva), chama uma ferramenta e devolve o resultado. Assim os dados grandes vão direto para arquivos,
sem passar pela conversa com a IA.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import tomllib
from pathlib import Path

HOME = Path.home()


def config_servidor(nome: str) -> dict:
    try:
        cj = json.loads((HOME / ".claude.json").read_text(encoding="utf-8"))
        if nome in (cj.get("mcpServers") or {}):
            return cj["mcpServers"][nome]
    except (OSError, json.JSONDecodeError):
        pass
    try:
        cx = tomllib.loads((HOME / ".codex" / "config.toml").read_text(encoding="utf-8"))
        if nome in (cx.get("mcp_servers") or {}):
            return cx["mcp_servers"][nome]
    except (OSError, tomllib.TOMLDecodeError):
        pass
    raise SystemExit(f"Servidor MCP '{nome}' não encontrado em ~/.claude.json nem em ~/.codex/config.toml.")


class ClienteMCP:
    def __init__(self, nome: str):
        cfg = config_servidor(nome)
        env = {**os.environ, **(cfg.get("env") or {})}
        self.nome = nome
        self.proc = subprocess.Popen(
            [cfg["command"], *(cfg.get("args") or [])], env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.fila: queue.Queue = queue.Queue()
        self.stderr: list[str] = []
        threading.Thread(target=self._ler, daemon=True).start()
        threading.Thread(target=self._ler_erros, daemon=True).start()
        self._id = 0
        self._pedido("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "conferencia-faturamento", "version": "1.0"}}, 120)
        self._enviar({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _ler(self):
        for linha in self.proc.stdout:
            try:
                self.fila.put(json.loads(linha))
            except json.JSONDecodeError:
                continue
        self.fila.put(None)

    def _ler_erros(self):
        for linha in self.proc.stderr:
            self.stderr.append(linha.decode("utf-8", "replace").rstrip())
            del self.stderr[:-30]

    def _enviar(self, msg: dict):
        self.proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def _pedido(self, metodo: str, params: dict, timeout: float) -> dict:
        self._id += 1
        meu_id = self._id
        self._enviar({"jsonrpc": "2.0", "id": meu_id, "method": metodo, "params": params})
        while True:
            try:
                msg = self.fila.get(timeout=timeout)
            except queue.Empty:
                raise TimeoutError(f"{self.nome}: sem resposta em {timeout}s para {metodo}")
            if msg is None:
                raise RuntimeError(f"{self.nome} encerrou: " + " | ".join(self.stderr[-5:]))
            if "method" in msg and "id" in msg:  # pedido do servidor (ex.: roots/list): recusa educadamente
                self._enviar({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "não suportado"}})
                continue
            if msg.get("id") == meu_id:
                if "error" in msg:
                    raise RuntimeError(f"{self.nome}: {msg['error'].get('message')}")
                return msg.get("result") or {}

    def chamar(self, ferramenta: str, argumentos: dict, timeout: float = 900) -> str:
        """Chama uma ferramenta e devolve o texto do resultado (levanta erro se a ferramenta falhar)."""
        res = self._pedido("tools/call", {"name": ferramenta, "arguments": argumentos}, timeout)
        texto = "\n".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text")
        if res.get("isError"):
            raise RuntimeError(f"{ferramenta} falhou: {texto[:2000]}")
        return texto

    def fechar(self):
        try:
            self.proc.stdin.close()
            self.proc.terminate()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.fechar()
