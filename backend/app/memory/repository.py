"""Durable storage: turn logs, feedback, escalations / live handoffs, LLM-judge
results, improvement-loop runs, and the metrics computed from them.
(Knowledge documents live in app/cms/store.py.)"""

import hashlib
import json
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

OPEN_HANDOFF = ("queued", "active")


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Repository(Protocol):
    async def log_turn(self, t: TurnLog) -> None: ...
    async def add_feedback(self, turn_id: str, rating: int, comment: str | None) -> bool: ...
    async def metrics(self, since_epoch: float) -> dict[str, Any]: ...

    # escalations / live handoff
    async def create_escalation(self, conversation_id: str, persona: str, user_ref: str, category: str,
                                summary: str, mode: str = "phone") -> str: ...
    async def get_escalation(self, escalation_id: str) -> dict[str, Any] | None: ...
    async def list_escalations(self, status: str | None, limit: int, assignee: str | None = None) -> list[dict[str, Any]]: ...
    async def claim(self, escalation_id: str, operator: str) -> bool: ...
    async def resolve(self, escalation_id: str, operator: str, note: str | None) -> bool: ...
    async def add_handoff_message(self, escalation_id: str, sender: str, text: str, author: str | None = None) -> int: ...
    async def list_handoff_messages(self, escalation_id: str, after_id: int = 0) -> list[dict[str, Any]]: ...

    # improvement loop / online evals
    async def candidate_questions(self, since_epoch: float, limit: int) -> list[dict[str, Any]]: ...
    async def save_judgement(self, turn_id: str, j: dict[str, Any]) -> None: ...
    async def list_judgements(self, flagged_only: bool, limit: int) -> list[dict[str, Any]]: ...
    async def save_insight_run(self, run: dict[str, Any]) -> None: ...
    async def list_insight_runs(self, limit: int) -> list[dict[str, Any]]: ...


def compute_metrics(turns: list[dict[str, Any]], feedback: dict[str, int],
                    judgements: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """RFP success metrics, computed from turn logs + feedback + judge results.

    - bot_resolution_rate: conversations that never escalated to a human
    - satisfaction: share of thumbs-up among rated answers
    - groundedness: share of judged answers fully supported by knowledge / tool data
    """
    judgements = judgements or {}
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
    judged = [judgements[t["id"]] for t in turns if t["id"] in judgements and judgements[t["id"]]["grounded"] != "n/a"]
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
        "judged_answers": len(judged),
        "groundedness": round(sum(1 for j in judged if j["grounded"] == "yes") / len(judged), 3) if judged else None,
        "judge_score_avg": round(sum(j["score"] for j in judged) / len(judged), 2) if judged else None,
        "latency_ms_p50": latencies[len(latencies) // 2] if latencies else None,
        "latency_ms_p90": latencies[int(len(latencies) * 0.9)] if latencies else None,
        "top_intents": [
            {"persona": p, "intent": i, "count": n} for (p, i), n in intents.most_common(15)
        ],
        "by_persona": {p: dict(c) for p, c in per_persona.items()},
    }


# ===================================================================== memory
class InMemoryRepository:
    def __init__(self) -> None:
        self.turns: list[TurnLog] = []
        self.feedback: dict[str, tuple[int, str | None]] = {}
        self.escalations: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.judgements: dict[str, dict[str, Any]] = {}
        self.runs: list[dict[str, Any]] = []

    async def log_turn(self, t: TurnLog) -> None:
        self.turns.append(t)

    async def add_feedback(self, turn_id: str, rating: int, comment: str | None) -> bool:
        if not any(t.id == turn_id for t in self.turns):
            return False
        self.feedback[turn_id] = (rating, comment)
        return True

    async def metrics(self, since_epoch: float) -> dict[str, Any]:
        turns = [asdict(t) for t in self.turns if t.created_at >= since_epoch]
        return compute_metrics(turns, {k: v[0] for k, v in self.feedback.items()}, self.judgements)

    async def create_escalation(self, conversation_id, persona, user_ref, category, summary, mode="phone") -> str:
        eid = new_id("esc")
        ts = _now()
        self.escalations.append({
            "id": eid, "conversation_id": conversation_id, "persona": persona, "user_ref": user_ref,
            "category": category, "summary": summary, "status": "queued", "mode": mode, "assignee": None,
            "created_at": ts, "updated_at": ts, "resolved_at": None, "resolution": None,
        })
        return eid

    async def get_escalation(self, escalation_id):
        return next((dict(e) for e in self.escalations if e["id"] == escalation_id), None)

    async def list_escalations(self, status, limit, assignee=None):
        rows = [dict(e) for e in reversed(self.escalations)
                if (status is None or e["status"] == status) and (assignee is None or e["assignee"] == assignee)]
        return rows[:limit]

    def _esc(self, eid):
        return next((e for e in self.escalations if e["id"] == eid), None)

    async def claim(self, escalation_id, operator) -> bool:
        e = self._esc(escalation_id)
        if not e or e["status"] not in OPEN_HANDOFF or (e["assignee"] not in (None, operator)):
            return False
        e.update(status="active", assignee=operator, updated_at=_now())
        return True

    async def resolve(self, escalation_id, operator, note) -> bool:
        e = self._esc(escalation_id)
        if not e or e["status"] == "resolved":
            return False
        e.update(status="resolved", assignee=e["assignee"] or operator, resolution=note,
                 resolved_at=_now(), updated_at=_now())
        return True

    async def add_handoff_message(self, escalation_id, sender, text, author=None) -> int:
        mid = len(self.messages) + 1
        self.messages.append({"id": mid, "escalation_id": escalation_id, "sender": sender, "author": author,
                              "text": text, "created_at": _now()})
        e = self._esc(escalation_id)
        if e:
            e["updated_at"] = _now()
        return mid

    async def list_handoff_messages(self, escalation_id, after_id=0):
        return [dict(m) for m in self.messages if m["escalation_id"] == escalation_id and m["id"] > after_id]

    async def candidate_questions(self, since_epoch, limit):
        out = []
        for t in self.turns:
            if t.created_at < since_epoch or t.intent in ("greeting",):
                continue
            reasons = []
            if self.feedback.get(t.id, (0,))[0] < 0:
                reasons.append("thumbs_down")
            if t.escalated:
                reasons.append("escalated")
            if self.judgements.get(t.id, {}).get("grounded") in ("no", "partial"):
                reasons.append("ungrounded")
            if reasons:
                out.append({"turn_id": t.id, "persona": t.persona, "intent": t.intent, "user_text": t.user_text,
                            "bot_text": t.bot_text, "reasons": reasons})
        return out[-limit:]

    async def save_judgement(self, turn_id, j):
        self.judgements[turn_id] = {**j, "turn_id": turn_id, "created_at": _now()}

    async def list_judgements(self, flagged_only, limit):
        turns = {t.id: t for t in self.turns}
        rows = [
            {**j, "persona": turns[j["turn_id"]].persona, "user_text": turns[j["turn_id"]].user_text,
             "bot_text": turns[j["turn_id"]].bot_text}
            for j in reversed(list(self.judgements.values()))
            if j["turn_id"] in turns and (not flagged_only or j["grounded"] in ("no", "partial") or not j["answered"])
        ]
        return rows[:limit]

    async def save_insight_run(self, run):
        self.runs.append(run)

    async def list_insight_runs(self, limit):
        return list(reversed(self.runs))[:limit]


# =================================================================== postgres
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

    async def metrics(self, since_epoch: float) -> dict[str, Any]:
        since = datetime.fromtimestamp(since_epoch, tz=timezone.utc)
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                """
                SELECT t.id, t.conversation_id, t.persona, t.intent, t.latency_ms,
                       c.escalated, f.rating, j.grounded, j.score
                FROM turns t JOIN conversations c ON c.id = t.conversation_id
                LEFT JOIN feedback f ON f.turn_id = t.id
                LEFT JOIN judgements j ON j.turn_id = t.id
                WHERE t.created_at >= %s
                """,
                (since,),
            )).fetchall()
        turns = [
            {"id": r[0], "conversation_id": r[1], "persona": r[2], "intent": r[3], "latency_ms": r[4], "escalated": r[5]}
            for r in rows
        ]
        return compute_metrics(
            turns, {r[0]: r[6] for r in rows if r[6] is not None},
            {r[0]: {"grounded": r[7], "score": r[8]} for r in rows if r[7] is not None},
        )

    # --------------------------------------------------------- escalations
    _ESC_COLS = ("id, conversation_id, persona, user_ref, category, summary, status, mode, assignee, "
                 "created_at, updated_at, resolved_at, resolution")

    def _esc_row(self, r) -> dict[str, Any]:
        d = dict(zip([c.strip() for c in self._ESC_COLS.split(",")], r))
        for k in ("created_at", "updated_at", "resolved_at"):
            d[k] = d[k].isoformat() if d[k] else None
        return d

    async def create_escalation(self, conversation_id, persona, user_ref, category, summary, mode="phone") -> str:
        eid = new_id("esc")
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO escalations (id, conversation_id, persona, user_ref, category, summary, status, mode) "
                "VALUES (%s,%s,%s,%s,%s,%s,'queued',%s)",
                (eid, conversation_id, persona, user_ref, category, summary, mode),
            )
        return eid

    async def get_escalation(self, escalation_id):
        async with self.pool.connection() as conn:
            r = await (await conn.execute(
                f"SELECT {self._ESC_COLS} FROM escalations WHERE id = %s", (escalation_id,))).fetchone()
        return self._esc_row(r) if r else None

    async def list_escalations(self, status, limit, assignee=None):
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                f"""SELECT {self._ESC_COLS} FROM escalations
                    WHERE (%s::text IS NULL OR status = %s) AND (%s::text IS NULL OR assignee = %s)
                    ORDER BY created_at DESC LIMIT %s""",
                (status, status, assignee, assignee, limit),
            )).fetchall()
        return [self._esc_row(r) for r in rows]

    async def claim(self, escalation_id, operator) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE escalations SET status = 'active', assignee = %s, updated_at = now()
                   WHERE id = %s AND status IN ('queued', 'active') AND (assignee IS NULL OR assignee = %s)""",
                (operator, escalation_id, operator),
            )
            return cur.rowcount > 0

    async def resolve(self, escalation_id, operator, note) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE escalations SET status = 'resolved', assignee = COALESCE(assignee, %s), resolution = %s,
                     resolved_at = now(), updated_at = now()
                   WHERE id = %s AND status <> 'resolved'""",
                (operator, note, escalation_id),
            )
            return cur.rowcount > 0

    async def add_handoff_message(self, escalation_id, sender, text, author=None) -> int:
        async with self.pool.connection() as conn:
            r = await (await conn.execute(
                "INSERT INTO handoff_messages (escalation_id, sender, author, text) VALUES (%s,%s,%s,%s) RETURNING id",
                (escalation_id, sender, author, text),
            )).fetchone()
            await conn.execute("UPDATE escalations SET updated_at = now() WHERE id = %s", (escalation_id,))
        return int(r[0])

    async def list_handoff_messages(self, escalation_id, after_id=0):
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT id, escalation_id, sender, author, text, created_at FROM handoff_messages "
                "WHERE escalation_id = %s AND id > %s ORDER BY id",
                (escalation_id, after_id),
            )).fetchall()
        return [{"id": r[0], "escalation_id": r[1], "sender": r[2], "author": r[3], "text": r[4],
                 "created_at": r[5].isoformat()} for r in rows]

    # ------------------------------------------------- improvement / judge
    async def candidate_questions(self, since_epoch, limit):
        since = datetime.fromtimestamp(since_epoch, tz=timezone.utc)
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                """
                SELECT t.id, t.persona, t.intent, t.user_text, t.bot_text,
                       (f.rating = -1) AS down,
                       (t.tool_calls @> '[{"name": "escalate_to_human"}]') AS esc,
                       (j.grounded IN ('no', 'partial')) AS ungrounded
                FROM turns t
                LEFT JOIN feedback f ON f.turn_id = t.id
                LEFT JOIN judgements j ON j.turn_id = t.id
                WHERE t.created_at >= %s AND COALESCE(t.intent, '') <> 'greeting'
                  AND (f.rating = -1 OR t.tool_calls @> '[{"name": "escalate_to_human"}]'
                       OR j.grounded IN ('no', 'partial'))
                ORDER BY t.created_at DESC LIMIT %s
                """,
                (since, limit),
            )).fetchall()
        return [{"turn_id": r[0], "persona": r[1], "intent": r[2], "user_text": r[3], "bot_text": r[4],
                 "reasons": [n for n, v in zip(("thumbs_down", "escalated", "ungrounded"), r[5:8]) if v]}
                for r in rows]

    async def save_judgement(self, turn_id, j):
        async with self.pool.connection() as conn:
            await conn.execute(
                """INSERT INTO judgements (turn_id, grounded, answered, score, unsupported_claims, rationale, model)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (turn_id) DO NOTHING""",
                (turn_id, j["grounded"], j["answered"], j["score"],
                 json.dumps(j.get("unsupported_claims", []), ensure_ascii=False), j.get("rationale"), j.get("model")),
            )

    async def list_judgements(self, flagged_only, limit):
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                """
                SELECT j.turn_id, j.grounded, j.answered, j.score, j.unsupported_claims, j.rationale, j.model,
                       j.created_at, t.persona, t.user_text, t.bot_text
                FROM judgements j JOIN turns t ON t.id = j.turn_id
                WHERE NOT %s OR j.grounded IN ('no', 'partial') OR NOT j.answered
                ORDER BY j.created_at DESC LIMIT %s
                """,
                (flagged_only, limit),
            )).fetchall()
        keys = ("turn_id", "grounded", "answered", "score", "unsupported_claims", "rationale", "model",
                "created_at", "persona", "user_text", "bot_text")
        return [{**dict(zip(keys, r)), "created_at": r[7].isoformat()} for r in rows]

    async def save_insight_run(self, run):
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO insight_runs (id, actor, stats, clusters) VALUES (%s,%s,%s,%s)",
                (run["id"], run["actor"], json.dumps(run["stats"]), json.dumps(run["clusters"], ensure_ascii=False)),
            )

    async def list_insight_runs(self, limit):
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT id, actor, stats, clusters, created_at FROM insight_runs ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )).fetchall()
        return [{"id": r[0], "actor": r[1], "stats": r[2], "clusters": r[3], "created_at": r[4].isoformat()}
                for r in rows]
