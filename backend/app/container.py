"""Builds and owns all long-lived services (dependency wiring)."""

import logging
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.llm.base import LLMProvider
from app.memory.repository import InMemoryRepository, PostgresRepository, Repository
from app.memory.session import InMemorySessionStore, RedisSessionStore, SessionStore
from app.observability.tracing import LangfuseTracer, NoopTracer, Tracer
from app.orchestrator.graph import Services
from app.orchestrator.service import ChatService
from app.rag.documents import KnowledgeDoc, load_directory
from app.rag.embeddings import Embedder, VoyageEmbedder
from app.rag.knowledge_base import KnowledgeBase
from app.rag.vector import InMemoryVectorIndex, PgVectorIndex, VectorIndex
from app.tools.definitions import build_registry
from app.tools.gateway import MockTokTokGateway

log = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    services: Services
    chat: ChatService
    _closers: list[Any] = field(default_factory=list)

    async def all_docs(self) -> list[KnowledgeDoc]:
        """Repository files, overridden by admin-edited docs with the same id."""
        docs = {d.id: d for d in load_directory(self.settings.knowledge_dir)}
        for d in await self.services.repo.list_docs():
            docs[d.id] = d
        return list(docs.values())

    async def reindex(self) -> dict[str, int]:
        return await self.services.kb.rebuild(await self.all_docs())

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


async def build_container(s: Settings, *, llm: LLMProvider | None = None) -> Container:
    if s.env == "prod":
        if s.admin_api_key in ("change-me", "") or s.user_ref_salt in ("change-me-too", ""):
            raise RuntimeError("Set ADMIN_API_KEY and USER_REF_SALT before running with ENV=prod")
        if s.llm_provider == "fake":
            raise RuntimeError("LLM_PROVIDER=fake is not allowed with ENV=prod")
    closers = []

    repo: Repository
    vectors: VectorIndex | None = None
    if s.database_url:
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(s.database_url, min_size=1, max_size=10, open=False)
        await pool.open()
        closers.append(pool.close)
        pg = PostgresRepository(pool, s.embedding_dim)
        await pg.init_schema()
        repo, vectors = pg, PgVectorIndex(pool)
    else:
        repo = InMemoryRepository()

    sessions: SessionStore
    if s.redis_url:
        import redis.asyncio as aioredis

        r = aioredis.from_url(s.redis_url)
        closers.append(r.aclose)
        sessions = RedisSessionStore(r, s.session_ttl_seconds)
    else:
        sessions = InMemorySessionStore(s.session_ttl_seconds)

    embedder: Embedder | None = None
    if s.embedding_provider == "voyage":
        if not s.voyage_api_key:
            raise RuntimeError("EMBEDDING_PROVIDER=voyage requires VOYAGE_API_KEY")
        embedder = VoyageEmbedder(s.voyage_api_key, s.voyage_model, s.embedding_dim)
        vectors = vectors or InMemoryVectorIndex()

    tracer: Tracer = NoopTracer()
    if s.langfuse_public_key and s.langfuse_secret_key:
        tracer = LangfuseTracer(s.langfuse_public_key, s.langfuse_secret_key, s.langfuse_base_url, s.env)

    services = Services(
        settings=s,
        llm=llm or _make_llm(s),
        kb=KnowledgeBase(embedder, vectors),
        tools=build_registry(),
        gateway=MockTokTokGateway(),
        repo=repo,
        sessions=sessions,
        tracer=tracer,
    )
    c = Container(settings=s, services=services, chat=ChatService(services), _closers=closers)
    await c.reindex()
    return c
