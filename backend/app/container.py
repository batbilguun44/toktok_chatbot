"""Builds and owns all long-lived services (dependency wiring)."""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.cms.service import CMSService
from app.cms.store import InMemoryKnowledgeStore, KnowledgeStore, PostgresKnowledgeStore
from app.config import Settings
from app.handoff.service import HandoffService, NoopNotifier, WebhookNotifier
from app.llm.base import LLMProvider
from app.memory.repository import InMemoryRepository, PostgresRepository, Repository
from app.memory.session import InMemorySessionStore, RedisSessionStore, SessionStore
from app.observability.tracing import LangfuseTracer, NoopTracer, Tracer
from app.orchestrator.graph import Services
from app.orchestrator.service import ChatService
from app.rag.contextual import Contextualizer
from app.rag.documents import load_directory
from app.rag.embeddings import Embedder, VoyageEmbedder
from app.rag.knowledge_base import KnowledgeBase, LocalHybridIndex, SearchIndex
from app.rag.rerank import VoyageReranker
from app.rag.vector import InMemoryVectorIndex, PgVectorIndex
from app.staff.auth import OIDCClient, Signer
from app.staff.store import InMemoryStaffStore, PostgresStaffStore, StaffStore
from app.tools.definitions import build_registry
from app.tools.gateway import MockTokTokGateway

log = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    services: Services
    staff: StaffStore
    signer: Signer
    oidc: OIDCClient | None
    cms: CMSService = None  # type: ignore[assignment]
    chat: ChatService = None  # type: ignore[assignment]
    _closers: list[Any] = field(default_factory=list)
    _kb_revision: str | None = None
    _kb_checked_at: float = 0.0

    async def reindex(self) -> dict[str, int]:
        rev = await self.cms.store.revision()
        stats = await self.services.kb.rebuild(await self.cms.published_docs())
        self._kb_revision, self._kb_checked_at = rev, time.time()
        # Share the current packs so any replica can rebuild a session's frozen system prompt.
        for pack in self.services.kb.packs.values():
            await self.services.sessions.put_blob(
                f"kbpack:{pack.id}", json.dumps(pack.to_json(), ensure_ascii=False),
                self.settings.session_ttl_seconds * 2,
            )
        return stats

    async def ensure_fresh(self) -> None:
        """Pick up knowledge published through another replica (cheap revision check)."""
        if time.time() - self._kb_checked_at < self.settings.kb_revision_check_seconds:
            return
        self._kb_checked_at = time.time()
        if await self.cms.store.revision() != self._kb_revision:
            log.info("knowledge revision changed; reindexing")
            await self.reindex()

    async def aclose(self) -> None:
        self.services.tracer.flush()
        for c in self._closers:
            await c()


def _make_llm(s: Settings) -> LLMProvider:
    if s.llm_provider == "fake":
        from app.llm.fake import FakeProvider

        log.warning("LLM_PROVIDER=fake: offline demo provider, not for production")
        return FakeProvider()
    from app.llm.anthropic_provider import AnthropicProvider

    return AnthropicProvider(s)


async def _make_index(s: Settings, embedder: Embedder | None, pool, closers: list) -> SearchIndex:
    if s.vector_store == "qdrant":
        from qdrant_client import AsyncQdrantClient

        from app.rag.qdrant_index import QdrantHybridIndex

        if s.qdrant_url:
            client = AsyncQdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key)
        else:
            log.warning("QDRANT_URL not set: using embedded in-memory Qdrant (dev / tests only)")
            client = AsyncQdrantClient(location=":memory:")
        closers.append(client.close)
        index = QdrantHybridIndex(client, s.qdrant_collection, embedder, s.embedding_dim)
        await index.ensure_collection()
        return index
    if s.vector_store == "pgvector":
        if embedder and not pool:
            raise RuntimeError("VECTOR_STORE=pgvector requires DATABASE_URL")
        return LocalHybridIndex(embedder, PgVectorIndex(pool) if pool else None)
    return LocalHybridIndex(embedder, InMemoryVectorIndex())


def _check_prod(s: Settings) -> None:
    if s.env != "prod":
        return
    if (s.admin_api_key in ("change-me", "") or s.user_ref_salt in ("change-me-too", "")
            or s.staff_session_secret in ("change-me-session", "") or len(s.staff_session_secret) < 32):
        raise RuntimeError("Set ADMIN_API_KEY, USER_REF_SALT and STAFF_SESSION_SECRET (32+ chars) for ENV=prod")
    if not s.sso_enabled:
        raise RuntimeError("ENV=prod requires OIDC_ISSUER / OIDC_CLIENT_ID / OIDC_CLIENT_SECRET for staff login")
    if s.llm_provider == "fake":
        raise RuntimeError("LLM_PROVIDER=fake is not allowed with ENV=prod")
    if not s.database_url or not s.redis_url:
        raise RuntimeError("ENV=prod requires DATABASE_URL and REDIS_URL")


async def build_container(s: Settings, *, llm: LLMProvider | None = None) -> Container:
    _check_prod(s)
    closers: list = []

    pool = None
    repo: Repository
    staff: StaffStore
    kstore: KnowledgeStore
    if s.database_url:
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(s.database_url, min_size=1, max_size=10, open=False)
        await pool.open()
        closers.append(pool.close)
        pg = PostgresRepository(pool, s.embedding_dim)
        await pg.init_schema()
        repo, staff, kstore = pg, PostgresStaffStore(pool), PostgresKnowledgeStore(pool)
        if migrated := await kstore.migrate_legacy():
            log.info("migrated %d legacy admin-edited docs into the CMS", migrated)
    else:
        repo, staff, kstore = InMemoryRepository(), InMemoryStaffStore(), InMemoryKnowledgeStore()

    sessions: SessionStore
    if s.redis_url:
        import redis.asyncio as aioredis

        r = aioredis.from_url(s.redis_url)
        closers.append(r.aclose)
        sessions = RedisSessionStore(r, s.session_ttl_seconds)
    else:
        sessions = InMemorySessionStore(s.session_ttl_seconds)

    if (s.embedding_provider == "voyage" or s.rerank_provider == "voyage") and not s.voyage_api_key:
        raise RuntimeError("Voyage embeddings / rerank require VOYAGE_API_KEY")
    embedder: Embedder | None = (
        VoyageEmbedder(s.voyage_api_key, s.voyage_model, s.embedding_dim) if s.embedding_provider == "voyage" else None
    )
    reranker = VoyageReranker(s.voyage_api_key, s.voyage_rerank_model) if s.rerank_provider == "voyage" else None

    tracer: Tracer = NoopTracer()
    if s.langfuse_public_key and s.langfuse_secret_key:
        tracer = LangfuseTracer(s.langfuse_public_key, s.langfuse_secret_key, s.langfuse_base_url, s.env)

    the_llm = llm or _make_llm(s)
    kb = KnowledgeBase(
        await _make_index(s, embedder, pool, closers), mode=s.knowledge_mode,
        full_max_tokens=s.full_context_max_tokens, reranker=reranker,
        contextualizer=Contextualizer(the_llm) if s.contextual_retrieval else None,
    )
    notifier = (WebhookNotifier(s.handoff_webhook_url, s.handoff_webhook_secret)
                if s.handoff_webhook_url else NoopNotifier())
    services = Services(
        settings=s, llm=the_llm, kb=kb, tools=build_registry(), gateway=MockTokTokGateway(), repo=repo,
        sessions=sessions, tracer=tracer, handoff=HandoffService(repo, staff, notifier),
    )
    oidc = (OIDCClient(s.oidc_issuer, s.oidc_client_id, s.oidc_client_secret, s.oidc_redirect_uri, s.oidc_scopes)
            if s.sso_enabled else None)

    c = Container(settings=s, services=services, staff=staff, signer=Signer(s.staff_session_secret), oidc=oidc,
                  _closers=closers)
    c.cms = CMSService(kstore, staff, on_publish=c.reindex, four_eyes=s.cms_four_eyes)
    c.chat = ChatService(services, ensure_fresh=c.ensure_fresh)
    if seeded := await c.cms.seed_from_files(load_directory(s.knowledge_dir)):
        log.info("seeded %d knowledge docs from %s", seeded, s.knowledge_dir)
    await c.reindex()
    return c
