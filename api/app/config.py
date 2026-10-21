from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE) if _ENV_FILE.exists() else None, extra="ignore")

    DATABASE_URL: str = "postgresql://pii:pii@localhost:54329/pii"
    API_PORT: int = 8100
    MERCHANT_URL: str = "http://localhost:4100"
    WORKER_TOKEN: str = "change-me-worker-token"
    WORKER_EMBEDDED: bool = True
    WORKER_POLL_SECONDS: float = 1.0
    WORKER_STALE_LOCK_SECONDS: int = 120

    AUTH_MODE: str = "dev"  # dev | supabase
    SUPABASE_JWT_SECRET: str = ""

    AGENT_MODE: str = "auto"  # auto | adk | deterministic
    GOOGLE_API_KEY: str = ""
    ADK_MODEL: str = "gemini-2.5-flash"
    PROMPT_VERSION: str = "v1"
    AGENT_MAX_TOOL_CALLS: int = 24
    AGENT_TIMEOUT_SECONDS: int = 180

    STRIPE_SECRET_KEY: str = ""
    APPROVAL_TTL_MINUTES: int = 30
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def sqlalchemy_url(self) -> str:
        return self.DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

    @property
    def effective_agent_mode(self) -> str:
        if self.AGENT_MODE == "auto":
            return "adk" if (self.GOOGLE_API_KEY or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").upper() == "TRUE") else "deterministic"
        return self.AGENT_MODE


@lru_cache
def settings() -> Settings:
    return Settings()
