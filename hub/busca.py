"""Busca no texto das conversas (o que o usuário escreveu e as respostas da IA).

Índice em memória: na primeira busca lê o histórico de cada chat; depois relê só o que foi acrescentado ao arquivo
(o histórico é só de acréscimo). Ferramentas, saídas de comandos e raciocínio interno ficam de fora: são a maior
parte dos arquivos e trariam resultados que o usuário não vê na conversa.
"""
from __future__ import annotations

import json
import threading
import unicodedata

from . import store

TIPOS = {"user": "Você", "assistant": "IA"}
_MARCAS = tuple(f'"kind": "{k}"'.encode() for k in TIPOS)  # filtro rápido antes do json.loads
_TRAVA = threading.Lock()
_indice: dict[str, dict] = {}  # chat_id -> {"pos": bytes lidos, "tam": tamanho, "msgs": [(tipo, ts, texto, normalizado)]}


def _tabela() -> dict[int, str]:
    """Letra acentuada -> letra base (á -> a, ç -> c). Mantém o tamanho do texto, para o trecho bater com o original."""
    t = {}
    for cp in range(0xC0, 0x250):
        base = unicodedata.normalize("NFD", chr(cp))[0]
        if base != chr(cp) and base.isascii():
            t[cp] = base
    return t


_SEM_ACENTO = _tabela()


def normalizar(texto: str) -> str:
    n = texto.lower()
    if len(n) != len(texto):  # raro (ex.: "İ" vira 2 letras): mantém 1 letra por letra para as marcas baterem
        n = "".join(c.lower()[0] for c in texto)
    return n if n.isascii() else n.translate(_SEM_ACENTO)


def _atualizar(chat_id: str) -> list:
    caminho = store.CHATS_DIR / f"{chat_id}.jsonl"
    item = _indice.get(chat_id)
    try:
        tam = caminho.stat().st_size
    except OSError:
        _indice.pop(chat_id, None)
        return []
    if item is None or tam < item["tam"]:  # novo ou reescrito: lê do começo
        item = _indice[chat_id] = {"pos": 0, "tam": 0, "msgs": []}
    if tam > item["pos"]:
        with caminho.open("rb") as f:
            f.seek(item["pos"])
            bloco = f.read(tam - item["pos"])
        fim = bloco.rfind(b"\n") + 1  # uma linha ainda sendo gravada fica para a próxima busca
        for linha in bloco[:fim].splitlines():
            if not any(m in linha for m in _MARCAS):
                continue
            try:
                ev = json.loads(linha)
            except ValueError:
                continue
            texto = ev.get("text")
            if ev.get("kind") in TIPOS and isinstance(texto, str) and texto.strip():
                item["msgs"].append((ev["kind"], ev.get("ts") or 0, texto, normalizar(texto)))
        item["pos"] += fim
    item["tam"] = tam
    return item["msgs"]


def _trecho(texto: str, norm: str, termos: list[str], largura: int = 70) -> dict:
    ini = min(norm.find(t) for t in termos)
    a, b = max(0, ini - largura // 2), min(len(texto), ini + largura + largura // 2)
    a = texto.rfind(" ", 0, a) + 1 if a else 0  # não corta palavra no começo
    pedaco, pnorm = texto[a:b], norm[a:b]
    marcas = []
    for t in termos:
        i = pnorm.find(t)
        while i >= 0:
            marcas.append([i, i + len(t)])
            i = pnorm.find(t, i + len(t))
    limpo = pedaco.replace("\n", " ")  # mesmo tamanho: as marcas continuam valendo
    return {"texto": limpo, "marcas": sorted(marcas), "inicio": a > 0, "fim": b < len(texto)}


def buscar(consulta: str, chat_ids: list[str], max_trechos: int = 2) -> dict:
    """Mensagens que têm todas as palavras da busca (sem diferenciar maiúsculas nem acentos)."""
    termos = [t for t in normalizar(consulta).split() if t]
    if not termos:
        return {"termos": [], "chats": {}}
    saida = {}
    with _TRAVA:
        for cid in list(_indice):
            if cid not in chat_ids:
                del _indice[cid]  # chat apagado
        for cid in chat_ids:
            achados = [(tipo, ts, txt, n) for tipo, ts, txt, n in _atualizar(cid) if all(t in n for t in termos)]
            if achados:
                saida[cid] = {"total": len(achados),
                              "trechos": [{"quem": TIPOS[tipo], **_trecho(txt, n, termos)}
                                          for tipo, ts, txt, n in achados[:max_trechos]]}
    return {"termos": termos, "chats": saida}
