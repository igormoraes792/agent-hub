"""Persistência simples em arquivos JSON: agentes, chats e o histórico de eventos de cada chat."""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CHATS_DIR = DATA_DIR / "chats"

_lock = threading.RLock()


def _now() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class Collection:
    """Lista de objetos com `id`, gravada inteira em um único arquivo JSON."""

    def __init__(self, filename: str):
        self.path = DATA_DIR / filename

    def all(self) -> list[dict]:
        with _lock:
            return _read_json(self.path, [])

    def get(self, item_id: str) -> dict | None:
        return next((x for x in self.all() if x["id"] == item_id), None)

    def create(self, data: dict) -> dict:
        with _lock:
            items = self.all()
            item = {**data, "id": new_id(), "created_at": _now(), "updated_at": _now()}
            items.append(item)
            _write_json(self.path, items)
            return item

    def update(self, item_id: str, changes: dict, touch: bool = True) -> dict | None:
        """touch=False grava sem mexer em updated_at (ex.: arquivar não sobe o chat na lista)."""
        with _lock:
            items = self.all()
            for i, x in enumerate(items):
                if x["id"] == item_id:
                    changes = {k: v for k, v in changes.items() if k not in ("id", "created_at")}
                    items[i] = {**x, **changes, "updated_at": _now() if touch else x.get("updated_at", _now())}
                    _write_json(self.path, items)
                    return items[i]
            return None

    def delete(self, item_id: str) -> bool:
        with _lock:
            items = self.all()
            kept = [x for x in items if x["id"] != item_id]
            _write_json(self.path, kept)
            return len(kept) != len(items)


agents = Collection("agents.json")
chats = Collection("chats.json")


def append_event(chat_id: str, event: dict) -> None:
    with _lock:
        CHATS_DIR.mkdir(parents=True, exist_ok=True)
        with (CHATS_DIR / f"{chat_id}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")


def append_events(chat_id: str, events: list[dict]) -> None:
    with _lock:
        CHATS_DIR.mkdir(parents=True, exist_ok=True)
        with (CHATS_DIR / f"{chat_id}.jsonl").open("a", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_events(chat_id: str) -> list[dict]:
    path = CHATS_DIR / f"{chat_id}.jsonl"
    with _lock:
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return out


def delete_events(chat_id: str) -> None:
    with _lock:
        (CHATS_DIR / f"{chat_id}.jsonl").unlink(missing_ok=True)


ANEXOS_DIR = DATA_DIR / "anexos"


def anexos_dir(chat_id: str) -> Path:
    """Pasta das imagens anexadas a um chat (apagada junto com o chat)."""
    return ANEXOS_DIR / chat_id


def delete_anexos(chat_id: str) -> None:
    import shutil
    shutil.rmtree(anexos_dir(chat_id), ignore_errors=True)
