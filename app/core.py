"""
Lógica central de processamento de mensagens, compartilhada entre o painel
web (/chat, /chat/media) e os adaptadores de canal (WhatsApp, Telegram).

Cada canal só precisa: (1) transformar o payload dele em texto/imagem/áudio
brutos, (2) chamar process_incoming(), (3) transformar o resultado de volta
no formato de envio do canal.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import uuid4

from app.config import settings
from app.history import store
from app.codex_client import run_codex, CodexError
from app.media import transcribe_audio, synthesize_speech

_ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


@dataclass
class IncomingMessage:
    session_id: str          # chave estável do contato, ex: "whatsapp:5511999999999"
    text: Optional[str] = None
    image_bytes: Optional[bytes] = None
    image_ext: str = ".jpg"
    audio_bytes: Optional[bytes] = None
    audio_filename: str = "audio.ogg"
    want_voice_reply: bool = False


@dataclass
class OutgoingMessage:
    text: str
    audio_bytes: Optional[bytes] = None  # WAV, se want_voice_reply=True e TTS funcionou
    image_bytes: Optional[bytes] = None  # imagem que o Codex gerou/editou no workspace
    image_mimetype: Optional[str] = None
    is_error: bool = False


async def process_incoming(msg: IncomingMessage) -> OutgoingMessage:
    text_parts: list[str] = []

    if msg.audio_bytes:
        transcribed = transcribe_audio(msg.audio_bytes, msg.audio_filename)
        if transcribed:
            text_parts.append(transcribed)

    if msg.text:
        text_parts.append(msg.text)

    if not text_parts:
        return OutgoingMessage(text="Não entendi a mensagem (sem texto e sem fala reconhecida no áudio).")

    final_message = "\n".join(text_parts)

    image_paths: list[str] = []
    if msg.image_bytes:
        ext = msg.image_ext.lower() if msg.image_ext.lower() in _ALLOWED_IMAGE_EXT else ".jpg"
        img_path = Path(settings.WORKSPACE_DIR) / f"upload_{uuid4().hex}{ext}"
        img_path.write_bytes(msg.image_bytes)
        image_paths.append(str(img_path))

    thread_id = store.get_thread_id(msg.session_id)
    store.append_message(msg.session_id, "user", final_message)

    try:
        new_thread_id, reply, generated_images = await run_codex(
            final_message, thread_id=thread_id, image_paths=image_paths
        )
    except CodexError as e:
        store.append_message(msg.session_id, "error", str(e))
        return OutgoingMessage(text=f"Deu erro ao falar com o Codex: {e}", is_error=True)

    store.set_thread_id(msg.session_id, new_thread_id)
    store.append_message(msg.session_id, "assistant", reply)

    audio_out = None
    if msg.want_voice_reply:
        try:
            audio_out = synthesize_speech(reply)
        except Exception as e:
            store.append_message(msg.session_id, "error", f"TTS falhou: {e}")

    image_out, image_mimetype = _load_last_generated_image(generated_images, msg.session_id)

    return OutgoingMessage(
        text=reply,
        audio_bytes=audio_out,
        image_bytes=image_out,
        image_mimetype=image_mimetype,
    )


def _load_last_generated_image(
    image_paths: list[str], session_id: str
) -> tuple[Optional[bytes], Optional[str]]:
    """
    Lê do disco a última imagem que o Codex gerou/alterou nesta rodada
    (se houver mais de uma, manda só a mais recente — WhatsApp/Telegram
    tratam melhor uma imagem por mensagem).
    """
    if not image_paths:
        return None, None

    path_str = image_paths[-1]
    path = Path(path_str)
    if not path.is_absolute():
        path = Path(settings.WORKSPACE_DIR) / path

    try:
        data = path.read_bytes()
    except OSError as e:
        store.append_message(session_id, "error", f"Não consegui ler a imagem gerada ({path}): {e}")
        return None, None

    ext = path.suffix.lower()
    mimetype = _MIME_BY_EXT.get(ext, "image/png")
    return data, mimetype
