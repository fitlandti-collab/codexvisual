import base64
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.runtime_config import runtime_config
from app.history import store
from app.codex_client import check_health, check_login_status, CodexError
from app.core import process_incoming, IncomingMessage
from app.channels import whatsapp as whatsapp_channel
from app.channels import telegram as telegram_channel
from fastapi import Request, Header
from app.models import (
    ChatRequest, ChatResponse, SessionInfo, SessionSummary,
    ConfigModel, ConfigUpdateRequest, ChatMessage,
)

# Formatos de imagem aceitos pelo Codex CLI (BMP/TIFF/SVG/HEIC não são suportados)
_ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

app = FastAPI(
    title=settings.APP_NAME,
    description="API que expõe o Codex CLI (OpenAI) com sessões persistentes de conversa.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    codex_info = await check_health()
    login_info = await check_login_status()
    return {"status": "ok", "codex": codex_info, "login": login_info}


@app.get("/config", response_model=ConfigModel)
async def get_config():
    data = runtime_config.as_dict()
    return ConfigModel(
        exec_flags=data["exec_flags"],
        exec_timeout_seconds=data["exec_timeout_seconds"],
        workspace_dir=settings.WORKSPACE_DIR,
        codex_bin=settings.CODEX_BIN,
    )


@app.put("/config", response_model=ConfigModel)
async def update_config(req: ConfigUpdateRequest):
    runtime_config.update(
        exec_flags=req.exec_flags,
        exec_timeout_seconds=req.exec_timeout_seconds,
    )
    data = runtime_config.as_dict()
    return ConfigModel(
        exec_flags=data["exec_flags"],
        exec_timeout_seconds=data["exec_timeout_seconds"],
        workspace_dir=settings.WORKSPACE_DIR,
        codex_bin=settings.CODEX_BIN,
    )


@app.post("/config/reset", response_model=ConfigModel)
async def reset_config():
    runtime_config.reset()
    data = runtime_config.as_dict()
    return ConfigModel(
        exec_flags=data["exec_flags"],
        exec_timeout_seconds=data["exec_timeout_seconds"],
        workspace_dir=settings.WORKSPACE_DIR,
        codex_bin=settings.CODEX_BIN,
    )


@app.get("/sessions", response_model=list[SessionSummary])
async def list_sessions():
    return [SessionSummary(**s) for s in store.list_sessions()]


@app.get("/sessions/{session_id}", response_model=SessionInfo)
async def get_session(session_id: str):
    if not store.exists(session_id):
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    return SessionInfo(
        session_id=session_id,
        thread_id=store.get_thread_id(session_id),
        title=store.get_title(session_id),
        messages=[ChatMessage(**m) for m in store.get_messages(session_id)],
    )


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    store.delete(session_id)
    return {"status": "deleted", "session_id": session_id}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    session_id = req.session_id or store.new_session_id()

    outgoing = await process_incoming(
        IncomingMessage(session_id=session_id, text=req.message)
    )

    if outgoing.is_error:
        raise HTTPException(status_code=502, detail=outgoing.text)

    thread_id = store.get_thread_id(session_id)
    image_b64 = base64.b64encode(outgoing.image_bytes).decode("ascii") if outgoing.image_bytes else None

    return ChatResponse(
        session_id=session_id,
        thread_id=thread_id,
        reply=outgoing.text,
        image_base64=image_b64,
        image_mimetype=outgoing.image_mimetype if image_b64 else None,
    )


@app.post("/chat/media", response_model=ChatResponse)
async def chat_media(
    session_id: Optional[str] = Form(None),
    message: Optional[str] = Form(None),
    voice_reply: bool = Form(False),
    image: Optional[UploadFile] = File(None),
    audio: Optional[UploadFile] = File(None),
):
    """
    Igual ao /chat, mas aceita mídia:
    - image: imagem (png/jpg/jpeg/gif/webp) que o Codex vai enxergar via -i.
    - audio: gravação de voz (webm/mp3/wav/ogg...), transcrita localmente
      (faster-whisper) e usada como texto do prompt.
    - voice_reply: se true, a resposta também vem em áudio (audio_base64),
      sintetizada localmente (Piper).
    Pelo menos um entre "message" e "audio" precisa ser enviado.
    """
    if not message and audio is None:
        raise HTTPException(
            status_code=400,
            detail="Envie 'message' e/ou 'audio'.",
        )

    image_bytes = None
    image_ext = ".png"
    if image is not None:
        image_ext = Path(image.filename or "").suffix.lower() or ".png"
        if image_ext not in _ALLOWED_IMAGE_EXT:
            raise HTTPException(
                status_code=400,
                detail=f"Formato de imagem '{image_ext}' não suportado pelo Codex. Use: {sorted(_ALLOWED_IMAGE_EXT)}",
            )
        image_bytes = await image.read()

    audio_bytes = await audio.read() if audio is not None else None

    if session_id is None:
        session_id = store.new_session_id()

    outgoing = await process_incoming(
        IncomingMessage(
            session_id=session_id,
            text=message,
            image_bytes=image_bytes,
            image_ext=image_ext,
            audio_bytes=audio_bytes,
            audio_filename=audio.filename if audio is not None else "audio.webm",
            want_voice_reply=voice_reply,
        )
    )

    if outgoing.is_error:
        raise HTTPException(status_code=502, detail=outgoing.text)

    thread_id = store.get_thread_id(session_id)
    audio_b64 = base64.b64encode(outgoing.audio_bytes).decode("ascii") if outgoing.audio_bytes else None
    image_b64 = base64.b64encode(outgoing.image_bytes).decode("ascii") if outgoing.image_bytes else None

    return ChatResponse(
        session_id=session_id,
        thread_id=thread_id,
        reply=outgoing.text,
        audio_base64=audio_b64,
        image_base64=image_b64,
        image_mimetype=outgoing.image_mimetype if image_b64 else None,
    )


# --- Webhooks de canais externos (WhatsApp, Telegram) ---

@app.post("/webhook/whatsapp")
async def webhook_whatsapp(request: Request, x_webhook_secret: Optional[str] = Header(None)):
    if settings.EVOLUTION_WEBHOOK_SECRET and x_webhook_secret != settings.EVOLUTION_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Webhook secret inválido.")

    payload = await request.json()
    incoming = whatsapp_channel.parse_webhook(payload)
    if incoming is None:
        # Evento irrelevante (eco, status, etc): responde 200 pra Evolution API não ficar retentando
        return {"status": "ignored"}

    outgoing = await process_incoming(incoming)

    # Manda texto (ou áudio, se pedido) sempre, e a imagem gerada por cima, se houver.
    if outgoing.audio_bytes:
        await whatsapp_channel.send_audio(incoming.session_id, outgoing.audio_bytes)
    else:
        await whatsapp_channel.send_text(incoming.session_id, outgoing.text)

    if outgoing.image_bytes:
        await whatsapp_channel.send_image(
            incoming.session_id, outgoing.image_bytes, outgoing.image_mimetype
        )

    return {"status": "ok"}


@app.post("/webhook/telegram")
async def webhook_telegram(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(None),
):
    if (
        settings.TELEGRAM_WEBHOOK_SECRET
        and x_telegram_bot_api_secret_token != settings.TELEGRAM_WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=401, detail="Webhook secret inválido.")

    payload = await request.json()
    incoming = await telegram_channel.parse_webhook(payload)
    if incoming is None:
        return {"status": "ignored"}

    outgoing = await process_incoming(incoming)

    if outgoing.audio_bytes:
        await telegram_channel.send_audio(incoming.session_id, outgoing.audio_bytes)
    else:
        await telegram_channel.send_text(incoming.session_id, outgoing.text)

    if outgoing.image_bytes:
        await telegram_channel.send_image(
            incoming.session_id, outgoing.image_bytes, outgoing.image_mimetype
        )

    return {"status": "ok"}


# --- Painel visual (arquivos estáticos) ---
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
