"""
Configurações que podem ser alteradas em runtime pelo painel web,
sem precisar reiniciar/rebuildar o container.
São inicializadas a partir do .env (app.config.settings) e, se já existir
um runtime_config.json salvo (de um ajuste anterior), ele tem prioridade.
"""
import json
import os
import threading

from app.config import settings

RUNTIME_CONFIG_PATH = "/data/runtime_config.json"

_DEFAULTS = {
    "exec_flags": settings.EXEC_FLAGS,
    "exec_timeout_seconds": settings.EXEC_TIMEOUT_SECONDS,
}


class RuntimeConfig:
    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        self._data = dict(_DEFAULTS)
        self._load()

    def _load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self._data.update(saved)
            except (json.JSONDecodeError, OSError):
                pass
        else:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def as_dict(self) -> dict:
        return dict(self._data)

    def update(self, exec_flags: str | None = None, exec_timeout_seconds: int | None = None):
        with self._lock:
            if exec_flags is not None:
                self._data["exec_flags"] = exec_flags
            if exec_timeout_seconds is not None:
                self._data["exec_timeout_seconds"] = exec_timeout_seconds
            self._save()

    def reset(self):
        with self._lock:
            self._data = dict(_DEFAULTS)
            self._save()

    @property
    def exec_flags(self) -> str:
        return self._data["exec_flags"]

    @property
    def exec_timeout_seconds(self) -> int:
        return int(self._data["exec_timeout_seconds"])


runtime_config = RuntimeConfig(RUNTIME_CONFIG_PATH)
