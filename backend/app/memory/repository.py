"""Durable storage: turn logs, feedback, escalations, admin-edited knowledge, metrics."""

import hashlib
import json
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from app.rag.documents import KnowledgeDoc


@dataclass
class TurnLog:
    id: str
    conversation_id: str
    persona: str
    user_ref: str
    user_text: str
    bot_text: str
    intent: str | None
    intent_confidence: float | None
    route_source: str | None
    safety: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    stop: str | None = None
    model: str | None = None
    latency_ms: int | None = None
    usage: dict[str, int] = field(default_factory=dict)
    escalated: bool = False
    created_at: float = field(default_factory=time.time)


def user_ref(user_id: str, salt: str) -> str:
    """Analytics never store the raw TokTok user id."""
    return hashlib.sha256(f"{salt}:{user_id}".encode()).hexdigest()[:24]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Repository(Protocol):
    async def log_turn(self, t: TurnLog) -> None: ...
    async def add_feedback(self, turn_id: str, rating: int, comment: str | None) -> bool: ...
    async def create_escalation(
        self, conversation_id: str, persona: str, user_ref: str, category: str, summary: str
    ) -> str: ...
    async def list_escalations(self, status: str | None, limit: int) -> list[dict[str, Any]]: ...
    async def metrics(self, since_epoch: float) -> dict[str, Any]: ...
    async def list_docs(self) -> list[KnowledgeDoc]: ...
    async def upsert_doc(self, doc: KnowledgeDoc) -> None: ...
    async def delete_doc(self, doc_id: str) -> bool: ...


def compute_metrics(turns: list[dict[str, Any]], feedback: dict[str, int]) -> dict[str, Any]:
    """RFP success metrics, computed from turn logs + feedback.

    - bot_resolution_rate: conversations that never escalated to a human
    - satisfaction: share of thumbs-up among rated answers
    """
    by_conv: dict[str, dict[str, Any]] = {}
    intents: Counter = Counter()
    per_persona: dict[str, Counter] = defaultdict(Counter)
    latencies = []
    for t in turns:
        c = by_conv.setdefault(t["conversation_id"], {"persona": t["persona"], "escalated": False})
        c["escalated"] |= bool(t["escalated"])
        if t["intent"]:
            intents[(t["persona"], t["intent"])] += 1
        per_persona[t["persona"]]["turns"] += 1
        if t.get("latency_ms"):
            latencies.append(t["latency_ms"])
    for c in by_conv.values():
        per_persona[c["persona"]]["conversations"] += 1
        per_persona[c["persona"]]["escalated"] += int(c["escalated"])
    rated = [feedback[t["id"]] for t in turns if t["id"] in feedback]
    convs = len(by_conv)
    escalated = sum(1 for c in by_conv.values() if c["escalated"])
    latencies.sort()
    return {
        "conversations": convs,
        "turns": len(turns),
        "bot_resolution_rate": round(1 - escalated / convs, 3) if convs else None,
        "escalated_conversations": escalated,
        "rated_answers": len(rated),
        "satisfaction": round(sum(1 for r in rated if r > 0) / len(rated), 3) if rated else None,
        "latency_ms_p50": latencies[len(latencies) // 2] if latencies else None,
        "latency_ms_p90": latencies[int(len(latencies) * 0.9)] if latencies else None,
        "top_intents": [
            {"persona": p, "intent": i, "count": n} for (p, i), n in intents.most_common(15)
        ],
        "by_persona": {p: dict(c) for p, c in per_persona.items()},
    }


class InMemoryRepository:
    def __init__(self) -> None:
        self.turns: list[TurnLog] = []
        self.feedback: dict[str, tuple[int, str | None]] = {}
        self.escalations: list[dict[str, Any]] = []
        self.docs: dict[str, KnowledgeDoc] = {}

    async def log_turn(self, t: TurnLog) -> None:
        self.turns.append(t)

    async def add_feedback(self, turn_id: str, rating: int, comment: str | None) -> bool:
        if not any(t.id == turn_id for t in self.turns):
            return False
        self.feedback[turn_id] = (rating, comment)
        return True

    async def create_escalation(self, conversation_id, persona, user_ref, category, summary) -> str:
        eid = new_id("esc")
        self.escalations.append(
            {
                "id": eid,
                "conversation_id": conversation_id,
                "persona": persona,
                "user_ref": user_ref,
                "category": category,
                "summary": summary,
                "status": "open",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return eid

    async def list_escalations(self, status, limit):
        rows = [e for e in reversed(self.escalations) if status is None or e["status"] == status]
        return rows[:limit]

    async def metrics(self, since_epoch: float) -> dict[str, Any]:
        turns = [asdict(t) for t in self.turns if t.created_at >= since_epoch]
        return compute_metrics(turns, {k: v[0] for k, v in self.feedback.items()})

    async def list_docs(self) -> list[KnowledgeDoc]:
        return list(self.docs.values())

    async def upsert_doc(self, doc: KnowledgeDoc) -> None:
        self.docs[doc.id] = doc

    async def delete_doc(self, doc_id: str) -> bool:
        return self.docs.pop(doc_id, None) is not None


class PostgresRepository:
    def __init__(self, pool, embedding_dim: int):  # psycopg_pool.AsyncConnectionPool
        self.pool = pool
        self.embedding_dim = embedding_dim

    async def init_schema(self) -> None:
        sql = (Path(__file__).resolve().parents[2] / "db" / "schema.sql").read_text()
        sql = sql.replace("vector(1024)", f"vector({int(self.embedding_dim)})")
        async with self.pool.connection() as conn:
            await conn.execute(sql)

    async def log_turn(self, t: TurnLog) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO conversations (id, persona, user_ref, turns, escalated)
                VALUES (%s, %s, %s, 1, %s)
                ON CONFLICT (id) DO UPDATE SET last_at = now(), turns = conversations.turns + 1,
                  escalated = conversations.escalated OR EXCLUDED.escalated
                """,
                (t.conversation_id, t.persona, t.user_ref, t.escalated),
            )
            await conn.execute(
                """
                INSERT INTO turns (id, conversation_id, persona, user_text, bot_text, intent,
                  intent_confidence, route_source, safety, tool_calls, sources, stop, model,
                  latency_ms, usage)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    t.id, t.conversation_id, t.persona, t.user_text, t.bot_text, t.intent,
                    t.intent_confidence, t.route_source, t.safety, json.dumps(t.tool_calls, ensure_ascii=False),
                    json.dumps(t.sources), t.stop, t.model, t.latency_ms, json.dumps(t.usage),
                ),
            )

    async def add_feedback(self, turn_id: str, rating: int, comment: str | None) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO feedback (turn_id, rating, comment)
                SELECT id, %s, %s FROM turns WHERE id = %s
                ON CONFLICT (turn_id) DO UPDATE SET rating = EXCLUDED.rating,
                  comment = EXCLUDED.comment, created_at = now()
                """,
                (rating, comment, turn_id),
            )
            return cur.rowcount > 0

    async def create_escalation(self, conversation_id, persona, user_ref, category, summary) -> str:
        eid = new_id("esc")
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO escalations (id, conversation_id, persona, user_ref, category, summary) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (eid, conversation_id, persona, user_ref, category, summary),
            )
        return eid

    async def list_escalations(self, status, limit):
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, conversation_id, persona, user_ref, category, summary, status, created_at "
                "FROM escalations WHERE (%s::text IS NULL OR status = %s) ORDER BY created_at DESC LIMIT %s",
                (status, status, limit),
            )
            cols = [d.name for d in cur.description]
            rows = await cur.fetchall()
        return [{**dict(zip(cols, r)), "created_at": r[-1].isoformat()} for r in rows]

    async def metrics(self, since_epoch: float) -> dict[str, Any]:
        since = datetime.fromtimestamp(since_epoch, tz=timezone.utc)
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT t.id, t.conversation_id, t.persona, t.intent, t.latency_ms,
                       c.escalated, f.rating
                FROM turns t JOIN conversations c ON c.id = t.conversation_id
                LEFT JOIN feedback f ON f.turn_id = t.id
                WHERE t.created_at >= %s
                """,
                (since,),
            )
            rows = await cur.fetchall()
        turns = [
            {"id": r[0], "conversation_id": r[1], "persona": r[2], "intent": r[3], "latency_ms": r[4], "escalated": r[5]}
            for r in rows
        ]
        return compute_metrics(turns, {r[0]: r[6] for r in rows if r[6] is not None})

    async def list_docs(self) -> list[KnowledgeDoc]:
        async with self.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT id, persona, title, body, tags, visibility, owner, updated_at FROM knowledge_docs"
                )
            ).fetchall()
        return [
            KnowledgeDoc(
                id=r[0], persona=r[1], title=r[2], body=r[3], tags=list(r[4] or []), visibility=r[5],
                owner=r[6], updated_at=r[7].date().isoformat(), source="admin",
            )
            for r in rows
        ]

    async def upsert_doc(self, doc: KnowledgeDoc) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO knowledge_docs (id, persona, title, body, tags, visibility, owner)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO UPDATE SET persona = EXCLUDED.persona, title = EXCLUDED.title,
                  body = EXCLUDED.body, tags = EXCLUDED.tags, visibility = EXCLUDED.visibility,
                  owner = EXCLUDED.owner, updated_at = now()
                """,
                (doc.id, doc.persona, doc.title, doc.body, json.dumps(doc.tags, ensure_ascii=False), doc.visibility, doc.owner),
            )

    async def delete_doc(self, doc_id: str) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute("DELETE FROM knowledge_docs WHERE id = %s", (doc_id,))
            return cur.rowcount > 0
