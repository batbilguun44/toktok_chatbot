"""Postgres (+pgvector) and Redis integration. Skipped unless TEST_DATABASE_URL / TEST_REDIS_URL are set."""

import os
import time

import pytest

from app.memory.repository import TurnLog
from app.memory.session import SessionState
from app.rag.documents import KnowledgeDoc

DB = os.getenv("TEST_DATABASE_URL")
REDIS = os.getenv("TEST_REDIS_URL")


@pytest.mark.skipif(not DB, reason="TEST_DATABASE_URL not set")
async def test_postgres_repository_and_pgvector_index():
    from psycopg_pool import AsyncConnectionPool

    from app.memory.repository import PostgresRepository
    from app.rag.knowledge_base import KnowledgeBase
    from app.rag.vector import PgVectorIndex

    async with AsyncConnectionPool(DB, min_size=1, max_size=2, open=False) as pool:
        async with pool.connection() as conn:
            await conn.execute("DROP TABLE IF EXISTS feedback, turns, conversations, escalations, "
                               "knowledge_docs, knowledge_chunks CASCADE")
        repo = PostgresRepository(pool, embedding_dim=3)
        await repo.init_schema()
        await repo.init_schema()  # idempotent

        await repo.log_turn(TurnLog("t1", "s1", "customer", "u1", "q", "a", "order_status", 0.9, "llm", "ok"))
        await repo.log_turn(TurnLog("t2", "s1", "customer", "u1", "q2", "a2", "human_request", 0.9, "llm", "ok",
                                    escalated=True))
        assert await repo.add_feedback("t1", 1, None)
        assert not await repo.add_feedback("missing", 1, None)
        eid = await repo.create_escalation("s1", "customer", "u1", "user_requested", "summary text")
        assert (await repo.list_escalations("open", 5))[0]["id"] == eid
        m = await repo.metrics(time.time() - 3600)
        assert m["conversations"] == 1 and m["turns"] == 2 and m["satisfaction"] == 1.0
        assert m["bot_resolution_rate"] == 0.0

        await repo.upsert_doc(KnowledgeDoc("d1", "driver", "Тест", "## Хэсэг\nапп төлбөр"))
        assert [d.id for d in await repo.list_docs()] == ["d1"]

        class Toy:
            dim = 3

            async def embed(self, texts, input_type):
                return [[1.0 if "апп" in t else 0.0, 1.0 if "төлбөр" in t else 0.0, 0.1] for t in texts]

        kb = KnowledgeBase(Toy(), PgVectorIndex(pool))
        docs = await repo.list_docs() + [KnowledgeDoc("d2", "customer", "Хэрэглэгч", "захиалга")]
        assert (await kb.rebuild(docs))["embedded"] == 2
        assert (await kb.rebuild(docs))["embedded"] == 0
        hits = await kb.search("апп", "driver", 3)
        assert hits[0].doc_id == "d1" and "vector" in hits[0].matched_by
        assert all(h.doc_id != "d2" for h in hits)
        await kb.rebuild(docs[:1])
        async with pool.connection() as conn:
            n = (await (await conn.execute("SELECT count(*) FROM knowledge_chunks")).fetchone())[0]
        assert n == 1
        assert await repo.delete_doc("d1")


@pytest.mark.skipif(not REDIS, reason="TEST_REDIS_URL not set")
async def test_redis_session_store():
    import redis.asyncio as aioredis

    from app.memory.session import RedisSessionStore

    r = aioredis.from_url(REDIS)
    store = RedisSessionStore(r, ttl_seconds=60)
    st = SessionState("s_redis_test", "customer", "c-1", "anthropic",
                      history=[{"role": "user", "content": [{"type": "text", "text": "сайн уу"}]}],
                      recent=[("user", "сайн уу")])
    await store.save(st)
    loaded = await store.get("s_redis_test")
    assert loaded.history == st.history and loaded.recent == [("user", "сайн уу")]
    assert await store.acquire("s_redis_test") and not await store.acquire("s_redis_test")
    await store.release("s_redis_test")
    assert await store.acquire("s_redis_test")
    await store.release("s_redis_test")
    key = f"k{time.time()}"
    assert [await store.hit_rate(key) for _ in range(3)] == [1, 2, 3]
    await r.aclose()
