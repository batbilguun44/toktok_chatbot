"""Application settings, loaded from environment variables (or a .env file)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Repo-root .env (shared with docker compose), then backend/.env overrides.
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    env: Literal["dev", "test", "prod"] = "dev"

    # --- LLM -------------------------------------------------------------
    # "anthropic" is the production provider. "fake" is a deterministic offline
    # provider for local UI work and tests (no API key needed).
    llm_provider: Literal["anthropic", "fake"] = "anthropic"
    agent_model: str = "claude-opus-5-5"
    classifier_model: str = "claude-haiku-5-5"
    # Chat traffic rarely benefits from deep reasoning; raise per measurement.
    agent_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    agent_max_tokens: int = 32000
    agent_max_iterations: int = 6
    # Server-side refusal fallback (Claude API / Claude Platform on AWS only).
    llm_refusal_fallbacks: bool = True

    # --- Search index ----------------------------------------------------
    # qdrant  : BM25 sparse + dense vectors in Qdrant, fused server-side (default).
    #           QDRANT_URL empty = embedded in-memory Qdrant (dev / tests only).
    # pgvector: in-process BM25 + pgvector (needs DATABASE_URL for vectors).
    # memory  : in-process BM25 + in-memory vectors (dev / tests only).
    vector_store: Literal["qdrant", "pgvector", "memory"] = "qdrant"
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_collection: str = "toktok_knowledge"

    # --- Embeddings (vector leg of hybrid search) ------------------------
    # "none" = BM25-only retrieval. "voyage" = Voyage AI multilingual embeddings.
    embedding_provider: Literal["none", "voyage"] = "none"
    voyage_api_key: str | None = None
    voyage_model: str = "voyage-3.5"
    embedding_dim: int = 1024

    # --- Storage ---------------------------------------------------------
    # Empty values fall back to in-process stores (dev / tests only).
    database_url: str | None = None  # postgresql://user:pass@host:5432/db
    redis_url: str | None = None  # redis://host:6379/0
    session_ttl_seconds: int = 60 * 60 * 24
    history_max_turns: int = 20

    # --- Knowledge base --------------------------------------------------
    knowledge_dir: Path = BACKEND_DIR / "knowledge"
    retrieval_top_k: int = 5

    # --- TokTok integration ----------------------------------------------
    # Only the mock exists until TokTok's API spec is available (see tools/gateway.py).
    toktok_gateway: Literal["mock"] = "mock"

    # Human support channels shown on escalation (per persona).
    operator_phone_driver: str = "7000-0000"
    operator_phone_client: str = "7000-0001"
    operator_phone_customer: str = "7000-0002"

    # --- Security --------------------------------------------------------
    admin_api_key: str = "change-me"
    # Salt for hashing user ids in analytics (keep secret, keep stable).
    user_ref_salt: str = "change-me-too"
    cors_origins: list[str] = ["http://localhost:3000"]
    max_message_chars: int = 2000
    rate_limit_per_minute: int = 20

    # --- Observability (Langfuse) ---------------------------------------
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_base_url: str | None = None

    def operator_phone(self, persona: str) -> str:
        return {
            "driver": self.operator_phone_driver,
            "client": self.operator_phone_client,
            "customer": self.operator_phone_customer,
        }[persona]


@lru_cache
def get_settings() -> Settings:
    return Settings()
