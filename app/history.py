"""
Guarda os dados de cada sessão: thread_id do Codex + histórico de mensagens
(pra exibir no painel), tudo persistido em JSON simples.
"""
import json
import os
import threading
import time
import uuid
from typing import Optional

from app.config import settings


class SessionStore:
    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def new_session_id(self) -> str:
        return str(uuid.uuid4())

    def exists(self, session_id: str) -> bool:
        return session_id in self._data

    def get_thread_id(self, session_id: str) -> Optional[str]:
        entry = self._data.get(session_id)
        return entry.get("thread_id") if entry else None

    def get_messages(self, session_id: str) -> list[dict]:
        entry = self._data.get(session_id)
        return entry.get("messages", []) if entry else []

    def get_title(self, session_id: str) -> Optional[str]:
        entry = self._data.get(session_id)
        return entry.get("title") if entry else None

    def set_thread_id(self, session_id: str, thread_id: str):
        with self._lock:
            entry = self._data.setdefault(session_id, {
                "thread_id": thread_id,
                "created_at": time.time(),
                "title": None,
                "messages": [],
            })
            entry["thread_id"] = thread_id
            self._save()

    def append_message(self, session_id: str, role: str, content: str):
        with self._lock:
            entry = self._data.setdefault(session_id, {
                "thread_id": None,
                "created_at": time.time(),
                "title": None,
                "messages": [],
            })
            entry["messages"].append({
                "role": role,
                "content": content,
                "ts": time.time(),
            })
            if entry["title"] is None and role == "user":
                entry["title"] = content[:60]
            self._save()

    def delete(self, session_id: str):
        with self._lock:
            self._data.pop(session_id, None)
            self._save()

    def list_sessions(self) -> list[dict]:
        result = []
        for session_id, entry in self._data.items():
            result.append({
                "session_id": session_id,
                "thread_id": entry.get("thread_id"),
                "title": entry.get("title") or "(sem mensagens)",
                "created_at": entry.get("created_at"),
                "message_count": len(entry.get("messages", [])),
            })
        result.sort(key=lambda s: s["created_at"] or 0, reverse=True)
        return result


store = SessionStore(settings.SESSIONS_FILE)
