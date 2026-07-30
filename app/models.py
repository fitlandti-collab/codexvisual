from typing import Optional
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: Optional[str] = Field(
        default=None,
        description="Se não enviar, uma nova sessão (e uma nova thread no Codex) é criada."
    )
    message: str = Field(..., description="Instrução/prompt para o Codex.")


class ChatMessage(BaseModel):
    role: str
    content: str
    ts: float


class ChatResponse(BaseModel):
    session_id: str
    thread_id: str
    reply: str
    audio_base64: Optional[str] = Field(
        default=None,
        description="Resposta em áudio (WAV, base64), presente só quando voice_reply=True.",
    )


class SessionInfo(BaseModel):
    session_id: str
    thread_id: Optional[str]
    title: Optional[str]
    messages: list[ChatMessage]


class SessionSummary(BaseModel):
    session_id: str
    thread_id: Optional[str]
    title: str
    created_at: Optional[float]
    message_count: int


class ConfigModel(BaseModel):
    exec_flags: str
    exec_timeout_seconds: int
    workspace_dir: str
    codex_bin: str


class ConfigUpdateRequest(BaseModel):
    exec_flags: Optional[str] = None
    exec_timeout_seconds: Optional[int] = None
