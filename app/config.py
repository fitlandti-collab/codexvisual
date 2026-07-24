"""
Configurações da API wrapper do Codex.
Tudo via variáveis de ambiente (.env).
"""
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    APP_NAME: str = "codex-api"

    # Binário do codex CLI (já instalado na imagem via npm)
    CODEX_BIN: str = Field(default="codex")

    # Diretório onde o codex vai trabalhar (mesma pasta o tempo todo,
    # montada como volume para persistir os arquivos entre chamadas)
    WORKSPACE_DIR: str = Field(default="/workspace")

    # Flags extras passadas em toda chamada de "codex exec".
    # Padrão: sandbox de escrita restrita ao workspace + nunca pedir aprovação
    # (necessário pois exec roda sem TTY/interação humana) + permite rodar
    # fora de um repo git.
    EXEC_FLAGS: str = Field(
        default='-s workspace-write -c approval_policy="never" --skip-git-repo-check'
    )

    # Timeout (segundos) para cada chamada ao codex exec
    EXEC_TIMEOUT_SECONDS: int = Field(default=600)

    # Onde persistir o mapeamento session_id (nosso) -> thread_id (do codex)
    SESSIONS_FILE: str = Field(default="/data/sessions.json")

    class Config:
        env_file = ".env"


settings = Settings()
