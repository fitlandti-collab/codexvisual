"""
Adaptador de canal: WhatsApp via Evolution API (Baileys, não-oficial).

IMPORTANTE: o payload exato de webhook pode variar um pouco entre versões
da Evolution API (v1 vs v2, configs de "webhook_base64"). Este código cobre
o formato mais comum da v2 com webhook_base64=true. Se o seu payload vier
diferente, ajuste `parse_webhook` — o resto do fluxo não muda.

Docs: https://doc.evolution-api.com
"""
import httpx

from app.config import settings
from app.core import IncomingMessage


def parse_webhook(payload: dict) -> IncomingMessage | None:
    """
    Extrai uma IncomingMessage de um payload de webhook da Evolution API.
    Retorna None se o evento não for uma mensagem recebida útil (ex: eco
    de mensagem enviada por nós mesmos, atualização de status, etc).
    """
    if payload.get("event") not in ("messages.upsert", "MESSAGES_UPSERT"):
        return None

    data = payload.get("data", {}) or {}
    key = data.get("key", {}) or {}

    # Ignora mensagens que o próprio bot enviou (eco)
    if key.get("fromMe"):
        return None

    remote_jid = key.get("remoteJid", "")  # ex: "5511999999999@s.whatsapp.net"
    if not remote_jid:
        return None

    phone = remote_jid.split("@")[0]
    session_id = f"whatsapp:{phone}"

    message = data.get("message", {}) or {}

    text = (
        message.get("conversation")
        or (message.get("extendedTextMessage") or {}).get("text")
    )

    image_bytes = None
    image_ext = ".jpg"
    image_msg = message.get("imageMessage")
    if image_msg and data.get("base64"):
        import base64
        image_bytes = base64.b64decode(data["base64"])
        mimetype = image_msg.get("mimetype", "image/jpeg")
        image_ext = ".png" if "png" in mimetype else ".jpg"
        # Legenda da imagem (se houver) vira o texto do prompt
        text = text or image_msg.get("caption")

    audio_bytes = None
    audio_msg = message.get("audioMessage")
    if audio_msg and data.get("base64"):
        import base64
        audio_bytes = base64.b64decode(data["base64"])

    return IncomingMessage(
        session_id=session_id,
        text=text,
        image_bytes=image_bytes,
        image_ext=image_ext,
        audio_bytes=audio_bytes,
        audio_filename="audio.ogg",
        # Se o usuário mandou áudio, respondemos em áudio também (mais natural no WhatsApp)
        want_voice_reply=bool(audio_bytes),
    )


def _phone_from_session_id(session_id: str) -> str:
    return session_id.split(":", 1)[1]


async def send_text(session_id: str, text: str) -> None:
    phone = _phone_from_session_id(session_id)
    url = f"{settings.EVOLUTION_API_URL}/message/sendText/{settings.EVOLUTION_INSTANCE}"
    headers = {"apikey": settings.EVOLUTION_API_KEY}
    body = {"number": phone, "text": text}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()


async def send_audio(session_id: str, audio_bytes: bytes) -> None:
    import base64
    phone = _phone_from_session_id(session_id)
    url = f"{settings.EVOLUTION_API_URL}/message/sendWhatsAppAudio/{settings.EVOLUTION_INSTANCE}"
    headers = {"apikey": settings.EVOLUTION_API_KEY}
    body = {"number": phone, "audio": base64.b64encode(audio_bytes).decode("ascii")}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()


async def send_image(session_id: str, image_bytes: bytes, mimetype: str | None = None) -> None:
    """
    Manda uma imagem gerada pelo Codex de volta pro WhatsApp.

    Aviso de honestidade técnica: o endpoint de mídia genérica na Evolution
    API costuma ser `/message/sendMedia/{instance}` com `mediatype: "image"`,
    mas o nome exato do campo varia um pouco entre v1/v2 e forks. Se der 404
    ou erro de payload, confira a doc da sua versão e ajuste só esta função —
    o resto do fluxo não muda.
    """
    import base64
    phone = _phone_from_session_id(session_id)
    url = f"{settings.EVOLUTION_API_URL}/message/sendMedia/{settings.EVOLUTION_INSTANCE}"
    headers = {"apikey": settings.EVOLUTION_API_KEY}
    ext = "png" if not mimetype or "png" in mimetype else "jpg"
    body = {
        "number": phone,
        "mediatype": "image",
        "mimetype": mimetype or "image/png",
        "media": base64.b64encode(image_bytes).decode("ascii"),
        "fileName": f"imagem.{ext}",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()
