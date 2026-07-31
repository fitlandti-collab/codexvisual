"""
Camada que efetivamente chama o binário `codex` (via `codex exec`) e interpreta
a saída --json (JSONL) para extrair o thread_id e a resposta final do agente.
"""
import asyncio
import json
import shlex
from typing import Optional

from app.config import settings
from app.runtime_config import runtime_config

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


class CodexError(Exception):
    pass


def _build_args(
    message: str,
    thread_id: Optional[str],
    image_paths: Optional[list[str]] = None,
) -> list[str]:
    extra_flags = shlex.split(runtime_config.exec_flags)
    args = [settings.CODEX_BIN, "exec"] + extra_flags

    # Suporte nativo a imagem do Codex CLI (-i / --image). Aceita múltiplas
    # imagens separadas por vírgula. Não requer nenhuma chave/API extra: usa
    # o mesmo login (OAuth) do Codex já autenticado no container.
    if image_paths:
        args += ["-i", ",".join(image_paths)]

    if thread_id:
        args += ["resume", thread_id, "--json", message]
    else:
        args += ["--json", message]

    return args


def _parse_jsonl(stdout_text: str) -> tuple[Optional[str], str, list[str]]:
    """Retorna (thread_id, texto_da_resposta_final, caminhos_de_imagem_geradas)."""
    thread_id = None
    reply_parts: list[str] = []
    image_paths: list[str] = []

    for line in stdout_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        etype = event.get("type")

        if etype == "thread.started":
            thread_id = event.get("thread_id") or thread_id

        elif etype == "item.completed":
            item = event.get("item", {}) or {}
            # O Codex CLI usa a chave "type" dentro do item (ex: "agent_message"),
            # não "item_type". Mantemos os dois nomes de chave por segurança
            # (compatibilidade com versões antigas/futuras do CLI).
            item_kind = item.get("type") or item.get("item_type")

            if item_kind in ("assistant_message", "agent_message"):
                text = item.get("text")
                if text:
                    reply_parts.append(text)

            elif item_kind == "file_change":
                # Detecta imagens que o próprio Codex criou/alterou no workspace
                # (ex: gerou um gráfico, um diagrama, editou uma foto etc.)
                for change in item.get("changes", []) or []:
                    kind = change.get("kind")
                    path = change.get("path")
                    if not path or kind not in ("add", "update"):
                        continue
                    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
                    if ext in _IMAGE_EXT:
                        image_paths.append(path)

        elif etype == "turn.failed":
            error = event.get("error", {}) or {}
            raise CodexError(f"Codex reportou falha no turno: {error}")

    reply = reply_parts[-1] if reply_parts else ""
    return thread_id, reply, image_paths


async def run_codex(
    message: str,
    thread_id: Optional[str] = None,
    image_paths: Optional[list[str]] = None,
) -> tuple[str, str, list[str]]:
    """
    Executa o codex (nova thread ou resume de uma existente).
    Retorna (thread_id, reply, generated_image_paths).
    """
    args = _build_args(message, thread_id, image_paths)

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=settings.WORKSPACE_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=runtime_config.exec_timeout_seconds
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise CodexError(
            f"Codex excedeu o timeout de {runtime_config.exec_timeout_seconds}s."
        )
    except FileNotFoundError:
        raise CodexError(
            f"Binário '{settings.CODEX_BIN}' não encontrado no container."
        )

    stdout_text = stdout.decode("utf-8", errors="replace")
    stderr_text = stderr.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        raise CodexError(
            f"codex exec saiu com código {proc.returncode}: {stderr_text.strip() or stdout_text.strip()}"
        )

    new_thread_id, reply, generated_images = _parse_jsonl(stdout_text)
    final_thread_id = new_thread_id or thread_id

    if not final_thread_id:
        raise CodexError(
            "Não foi possível identificar o thread_id na saída do Codex. "
            f"Saída bruta: {stdout_text[:500]}"
        )

    if not reply:
        raise CodexError(
            "Codex não retornou nenhuma assistant_message. "
            f"Saída bruta: {stdout_text[:500]}"
        )

    return final_thread_id, reply, generated_images


async def check_health() -> dict:
    try:
        proc = await asyncio.create_subprocess_exec(
            settings.CODEX_BIN, "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        version = stdout.decode().strip() or stderr.decode().strip()
        return {"codex_installed": proc.returncode == 0, "version": version}
    except Exception as e:
        return {"codex_installed": False, "error": str(e)}


async def check_login_status() -> dict:
    try:
        proc = await asyncio.create_subprocess_exec(
            settings.CODEX_BIN, "login", "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        output = (stdout.decode() + stderr.decode()).strip()
        return {"logged_in": proc.returncode == 0, "detail": output}
    except Exception as e:
        return {"logged_in": False, "error": str(e)}
