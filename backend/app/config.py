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
    # Seed only: Markdown files are imported once as published v1; afterwards the CMS is the source of truth.
    knowledge_dir: Path = BACKEND_DIR / "knowledge"
    retrieval_top_k: int = 5
    # full: the persona's whole published knowledge in the cached system prompt (no retrieval misses).
    # rag : hybrid retrieval per turn. auto: full while it fits FULL_CONTEXT_MAX_TOKENS, else rag.
    knowledge_mode: Literal["auto", "full", "rag"] = "auto"
    full_context_max_tokens: int = 50_000
    rerank_provider: Literal["none", "voyage"] = "none"
    voyage_rerank_model: str = "rerank-2.5"
    contextual_retrieval: bool = False  # LLM-written chunk context at index time (RAG quality)
    cms_four_eyes: bool = True  # authors cannot approve their own changes (admins can)
    kb_revision_check_seconds: float = 5.0  # how often replicas check for newly published knowledge

    # --- Human handoff ---------------------------------------------------
    # inchat: operator joins the same chat via the operator console. phone: ticket + phone number only.
    handoff_mode: Literal["inchat", "phone"] = "inchat"
    handoff_webhook_url: str | None = None  # mirror handoff events to a helpdesk / automation
    handoff_webhook_secret: str = ""

    # --- Online evaluation -----------------------------------------------
    judge_sample_rate: float = 0.1  # share of production turns graded by the LLM judge
    judge_model: str | None = None  # default: CLASSIFIER_MODEL

    # --- TokTok integration ----------------------------------------------
    # Only the mock exists until TokTok's API spec is available (see tools/gateway.py).
    toktok_gateway: Literal["mock"] = "mock"

    # Human support channels shown on escalation (per persona).
    operator_phone_driver: str = "7000-0000"
    operator_phone_client: str = "7000-0001"
    operator_phone_customer: str = "7000-0002"

    # --- Staff SSO (OpenID Connect: Google Workspace / Microsoft Entra ID) ---
    # Google: https://accounts.google.com
    # Microsoft: https://login.microsoftonline.com/<tenant-id>/v2.0
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str = "http://localhost:8000/api/auth/callback"
    oidc_scopes: str = "openid email profile"
    # Only these email domains may sign in (empty = any account the IdP accepts).
    oidc_allowed_domains: list[str] = []
    # Created as admins on their first SSO login; everyone else must be added by an admin.
    staff_bootstrap_admins: list[str] = []
    staff_session_secret: str = "change-me-session"
    staff_session_hours: int = 12
    frontend_base_url: str = "http://localhost:3000"
    cookie_secure: bool = False  # true behind HTTPS (forced in prod)

    # --- Security --------------------------------------------------------
    # Break-glass / automation key (CI, cron). Acts as an admin service account.
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

    @property
    def sso_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id and self.oidc_client_secret)

    @property
    def dev_login_enabled(self) -> bool:
        return self.env != "prod"

    def operator_phone(self, persona: str) -> str:
        return {
            "driver": self.operator_phone_driver,
            "client": self.operator_phone_client,
            "customer": self.operator_phone_customer,
        }[persona]


@lru_cache
def get_settings() -> Settings:
    return Settings()
