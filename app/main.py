import base64
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.runtime_config import runtime_config
from app.history import store
from app.codex_client import run_codex, check_health, check_login_status, CodexError
from app.media import transcribe_audio, synthesize_speech
from app.core import process_incoming
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
    session_id = req.session_id
    thread_id = store.get_thread_id(session_id) if session_id else None

    if session_id is None:
        session_id = store.new_session_id()

    store.append_message(session_id, "user", req.message)

    try:
        new_thread_id, reply = await run_codex(req.message, thread_id=thread_id)
    except CodexError as e:
        store.append_message(session_id, "error", str(e))
        raise HTTPException(status_code=502, detail=str(e))

    store.set_thread_id(session_id, new_thread_id)
    store.append_message(session_id, "assistant", reply)

    return ChatResponse(session_id=session_id, thread_id=new_thread_id, reply=reply)


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
    text_parts: list[str] = []

    if audio is not None:
        audio_bytes = await audio.read()
        transcribed = transcribe_audio(audio_bytes, audio.filename or "audio.webm")
        if transcribed:
            text_parts.append(transcribed)

    if message:
        text_parts.append(message)

    if not text_parts:
        raise HTTPException(
            status_code=400,
            detail="Envie 'message' e/ou 'audio' (a transcrição do áudio ficou vazia).",
        )

    final_message = "\n".join(text_parts)

    image_paths: list[str] = []
    if image is not None:
        ext = Path(image.filename or "").suffix.lower() or ".png"
        if ext not in _ALLOWED_IMAGE_EXT:
            raise HTTPException(
                status_code=400,
                detail=f"Formato de imagem '{ext}' não suportado pelo Codex. Use: {sorted(_ALLOWED_IMAGE_EXT)}",
            )
        image_bytes = await image.read()
        img_path = Path(settings.WORKSPACE_DIR) / f"upload_{uuid4().hex}{ext}"
        img_path.write_bytes(image_bytes)
        image_paths.append(str(img_path))

    thread_id = store.get_thread_id(session_id) if session_id else None
    if session_id is None:
        session_id = store.new_session_id()

    store.append_message(session_id, "user", final_message)

    try:
        new_thread_id, reply = await run_codex(
            final_message, thread_id=thread_id, image_paths=image_paths
        )
    except CodexError as e:
        store.append_message(session_id, "error", str(e))
        raise HTTPException(status_code=502, detail=str(e))

    store.set_thread_id(session_id, new_thread_id)
    store.append_message(session_id, "assistant", reply)

    audio_b64 = None
    if voice_reply:
        try:
            audio_bytes_out = synthesize_speech(reply)
            audio_b64 = base64.b64encode(audio_bytes_out).decode("ascii")
        except Exception as e:
            # Não derruba a resposta de texto se só o TTS falhar.
            store.append_message(session_id, "error", f"TTS falhou: {e}")

    return ChatResponse(
        session_id=session_id,
        thread_id=new_thread_id,
        reply=reply,
        audio_base64=audio_b64,
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

    if outgoing.audio_bytes:
        await whatsapp_channel.send_audio(incoming.session_id, outgoing.audio_bytes)
    else:
        await whatsapp_channel.send_text(incoming.session_id, outgoing.text)

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

    return {"status": "ok"}


# --- Painel visual (arquivos estáticos) ---
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
