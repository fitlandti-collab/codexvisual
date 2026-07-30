"""
Adaptador de canal: Telegram via Bot API oficial.
Docs: https://core.telegram.org/bots/api
"""
import httpx

from app.config import settings
from app.core import IncomingMessage

_API_BASE = "https://api.telegram.org/bot{token}"


async def _download_file(file_id: str) -> bytes:
    base = _API_BASE.format(token=settings.TELEGRAM_BOT_TOKEN)
    async with httpx.AsyncClient(timeout=60) as client:
        info = await client.get(f"{base}/getFile", params={"file_id": file_id})
        info.raise_for_status()
        file_path = info.json()["result"]["file_path"]
        file_url = f"https://api.telegram.org/file/bot{settings.TELEGRAM_BOT_TOKEN}/{file_path}"
        file_resp = await client.get(file_url)
        file_resp.raise_for_status()
        return file_resp.content


async def parse_webhook(payload: dict) -> IncomingMessage | None:
    message = payload.get("message") or payload.get("edited_message")
    if not message:
        return None

    chat_id = message.get("chat", {}).get("id")
    if chat_id is None:
        return None

    session_id = f"telegram:{chat_id}"
    text = message.get("text") or message.get("caption")

    image_bytes = None
    if message.get("photo"):
        # "photo" é uma lista de resoluções; pega a maior (última)
        file_id = message["photo"][-1]["file_id"]
        image_bytes = await _download_file(file_id)

    audio_bytes = None
    want_voice = False
    voice = message.get("voice") or message.get("audio")
    if voice:
        audio_bytes = await _download_file(voice["file_id"])
        want_voice = True

    return IncomingMessage(
        session_id=session_id,
        text=text,
        image_bytes=image_bytes,
        image_ext=".jpg",
        audio_bytes=audio_bytes,
        audio_filename="audio.ogg",
        want_voice_reply=want_voice,
    )


async def send_text(session_id: str, text: str) -> None:
    chat_id = session_id.split(":", 1)[1]
    base = _API_BASE.format(token=settings.TELEGRAM_BOT_TOKEN)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{base}/sendMessage", json={"chat_id": chat_id, "text": text})
        resp.raise_for_status()


async def send_audio(session_id: str, audio_bytes: bytes) -> None:
    chat_id = session_id.split(":", 1)[1]
    base = _API_BASE.format(token=settings.TELEGRAM_BOT_TOKEN)
    async with httpx.AsyncClient(timeout=60) as client:
        files = {"voice": ("reply.wav", audio_bytes, "audio/wav")}
        resp = await client.post(f"{base}/sendVoice", data={"chat_id": chat_id}, files=files)
        resp.raise_for_status()
