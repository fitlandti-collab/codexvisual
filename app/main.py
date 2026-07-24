from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.runtime_config import runtime_config
from app.history import store
from app.codex_client import run_codex, check_health, check_login_status, CodexError
from app.models import (
    ChatRequest, ChatResponse, SessionInfo, SessionSummary,
    ConfigModel, ConfigUpdateRequest, ChatMessage,
)

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


# --- Painel visual (arquivos estáticos) ---
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
