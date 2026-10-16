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


def _iso(ts: float | datetime | str | None) -> str | None:
    if ts is None or isinstance(ts, str):
        return ts
    if isinstance(ts, datetime):
        return ts.isoformat()
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


@dataclass
class ConversationFilter:
    persona: str | None = None
    escalated: bool | None = None
    negative_feedback: bool | None = None  # only conversations with a 👎
    q: str | None = None                   # substring of a message, conversation id or ticket number
    intent: str | None = None              # router topic of any turn
    category: str | None = None            # escalation reason
    assignee: str | None = None            # operator who handled a handoff
    handoff_status: str | None = None      # "open" (queued/active) | "resolved"
    errors_only: bool | None = None        # a turn the bot could not finish (stop != "end")
    since_epoch: float | None = None
    until_epoch: float | None = None
    limit: int = 50
    offset: int = 0


EXPORT_COLUMNS = ["conversation_id", "turn_id", "created_at", "persona", "intent", "intent_confidence",
                  "user_text", "bot_text", "sources", "tools", "stop", "model", "latency_ms", "rating",
                  "conversation_escalated"]


def _handoff_summary(escs: list[dict[str, Any]]) -> dict[str, Any]:
    """List columns for a conversation's escalations (latest last)."""
    statuses = {e["status"] for e in escs}
    return {
        "handoff_status": "open" if statuses & set(OPEN_HANDOFF) else "resolved" if statuses else None,
        "categories": sorted({e["category"] for e in escs}),
        "assignees": sorted({e["assignee"] for e in escs if e.get("assignee")}),
        "tickets": [e["id"] for e in escs],
    }


def _conv_matches(f: "ConversationFilter", c: dict[str, Any], intents: set[str],
                  escs: list[dict[str, Any]], text: str) -> bool:
    """In-memory twin of PostgresRepository._conv_where (same semantics)."""
    return not (
        (f.escalated is not None and c["escalated"] != f.escalated)
        or (f.negative_feedback and not c["negative_feedback"])
        or (f.errors_only and not c["errors"])
        or (f.intent and f.intent not in intents)
        or (f.category and f.category not in c["categories"])
        or (f.assignee and f.assignee not in c["assignees"])
        or (f.handoff_status and c["handoff_status"] != f.handoff_status)
        or (f.q and f.q.lower() not in text)
    )


def _export_row(t: dict[str, Any], escalated: bool) -> dict[str, Any]:
    return {
        "conversation_id": t["conversation_id"], "turn_id": t["id"], "created_at": t["created_at"],
        "persona": t["persona"], "intent": t.get("intent"), "intent_confidence": t.get("intent_confidence"),
        "user_text": t["user_text"], "bot_text": t["bot_text"], "sources": ";".join(t.get("sources") or []),
        "tools": ";".join(c.get("name", "") for c in t.get("tool_calls") or []), "stop": t.get("stop"),
        "model": t.get("model"), "latency_ms": t.get("latency_ms"), "rating": t.get("rating"),
        "conversation_escalated": escalated,
    }


def merge_events(turns: list[dict[str, Any]], escalations: list[dict[str, Any]],
                 messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One time-ordered list: bot turns, handoff starts and operator/user/system handoff messages."""
    ev: list[dict[str, Any]] = []
    for t in turns:
        ev.append({"kind": "turn", "at": t["created_at"], "turn_id": t["id"], "user_text": t["user_text"],
                   "bot_text": t["bot_text"], "intent": t.get("intent"), "sources": t.get("sources") or [],
                   "tools": [c.get("name") for c in t.get("tool_calls") or []], "stop": t.get("stop"),
                   "latency_ms": t.get("latency_ms"), "rating": t.get("rating")})
    for e in escalations:
        ev.append({"kind": "escalation", "at": e["created_at"], "escalation_id": e["id"], "category": e["category"],
                   "summary": e["summary"], "status": e["status"], "assignee": e.get("assignee"),
                   "resolution": e.get("resolution")})
    for m in messages:
        ev.append({"kind": "handoff", "at": m["created_at"], "id": m["id"], "escalation_id": m["escalation_id"],
                   "sender": m["sender"], "author": m.get("author"), "text": m["text"]})
    # A turn is logged when it ends, but a handoff it starts is written mid-turn: order turns by their
    # start time (end - latency) so "question → bot reply → handoff messages" reads in order.
    def key(x: dict[str, Any]) -> tuple[float, int]:
        t = datetime.fromisoformat(x["at"]).timestamp() if x["at"] else 0.0
        if x["kind"] == "turn":
            t -= (x.get("latency_ms") or 0) / 1000
        return (t, {"turn": 0, "escalation": 1, "handoff": 2}[x["kind"]])

    return sorted(ev, key=key)


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
    async def resolve(self, escalation_id: str, operator: str | None, note: str | None) -> bool: ...
    async def add_handoff_message(self, escalation_id: str, sender: str, text: str, author: str | None = None) -> int: ...
    async def list_handoff_messages(self, escalation_id: str, after_id: int = 0) -> list[dict[str, Any]]: ...

    # conversation history (chat window restore, /admin conversations, analysis export)
    async def list_conversations(self, f: "ConversationFilter") -> dict[str, Any]: ...
    async def conversation_log(self, conversation_id: str) -> dict[str, Any] | None: ...
    async def export_turns(self, f: "ConversationFilter") -> list[dict[str, Any]]: ...
    async def kb_usage(self, since_epoch: float) -> dict[str, dict[str, Any]]:
        """doc_id -> {uses, negative, last_used}: answers that relied on the document, 👎 among them."""
        ...

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

    # ------------------------------------------------ conversation history
    def _turn_dicts(self, f: ConversationFilter) -> list[dict[str, Any]]:
        rows = []
        for t in self.turns:
            if f.persona and t.persona != f.persona:
                continue
            if f.since_epoch and t.created_at < f.since_epoch or f.until_epoch and t.created_at >= f.until_epoch:
                continue
            d = asdict(t)
            d["created_at"] = _iso(t.created_at)
            d["rating"] = self.feedback.get(t.id, (None,))[0]
            rows.append(d)
        return rows

    def _conversations(self, f: ConversationFilter) -> list[dict[str, Any]]:
        convs: dict[str, dict[str, Any]] = {}
        for t in self._turn_dicts(f):
            c = convs.setdefault(t["conversation_id"], {
                "id": t["conversation_id"], "persona": t["persona"], "started_at": t["created_at"],
                "last_at": t["created_at"], "turns": 0, "escalated": False, "preview": t["user_text"],
                "negative_feedback": 0, "errors": 0, "_text": t["conversation_id"], "_intents": set()})
            c["turns"] += 1
            c["last_at"] = t["created_at"]
            c["escalated"] |= bool(t["escalated"])
            c["negative_feedback"] += int(t["rating"] == -1)
            c["errors"] += int(t.get("stop") not in (None, "end"))
            c["_text"] += f"\n{t['user_text']}\n{t['bot_text']}"
            if t.get("intent"):
                c["_intents"].add(t["intent"])
        out = []
        for c in convs.values():
            escs = [e for e in self.escalations if e["conversation_id"] == c["id"]]
            c.update(_handoff_summary(escs))
            c["escalated"] |= bool(escs)
            text = (c.pop("_text") + "\n" + "\n".join(e["id"] for e in escs)).lower()
            if _conv_matches(f, c, c.pop("_intents"), escs, text):
                out.append(c)
        return sorted(out, key=lambda c: c["last_at"], reverse=True)

    async def list_conversations(self, f: ConversationFilter) -> dict[str, Any]:
        rows = self._conversations(f)
        return {"total": len(rows), "items": rows[f.offset:f.offset + f.limit]}

    async def conversation_log(self, conversation_id: str) -> dict[str, Any] | None:
        conv = next((c for c in self._conversations(ConversationFilter()) if c["id"] == conversation_id), None)
        if not conv:
            return None
        turns = [t for t in self._turn_dicts(ConversationFilter()) if t["conversation_id"] == conversation_id]
        escs = [dict(e) for e in self.escalations if e["conversation_id"] == conversation_id]
        ids = {e["id"] for e in escs}
        msgs = [dict(m) for m in self.messages if m["escalation_id"] in ids]
        return {"conversation": conv, "events": merge_events(turns, escs, msgs)}

    async def kb_usage(self, since_epoch: float) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for t in self.turns:
            if t.created_at < since_epoch:
                continue
            neg = self.feedback.get(t.id, (None,))[0] == -1
            for doc in dict.fromkeys(t.sources):
                u = out.setdefault(doc, {"uses": 0, "negative": 0, "last_used": None})
                u["uses"] += 1
                u["negative"] += int(neg)
                u["last_used"] = max(u["last_used"] or "", _iso(t.created_at))
        return out

    async def export_turns(self, f: ConversationFilter) -> list[dict[str, Any]]:
        keep = {c["id"]: c for c in self._conversations(ConversationFilter(**{**f.__dict__, "limit": 10**9,
                                                                             "offset": 0}))}
        return [_export_row(t, keep[t["conversation_id"]]["escalated"])
                for t in self._turn_dicts(f) if t["conversation_id"] in keep]

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

    # ------------------------------------------------ conversation history
    _CONV_ESCALATED = "(c.escalated OR EXISTS (SELECT 1 FROM escalations e WHERE e.conversation_id = c.id))"
    _CONV_ERRORS = ("(SELECT count(*) FROM turns te WHERE te.conversation_id = c.id "
                    "AND te.stop IS NOT NULL AND te.stop <> 'end')")
    _CONV_HANDOFF = ("(CASE WHEN EXISTS (SELECT 1 FROM escalations eo WHERE eo.conversation_id = c.id "
                     "AND eo.status IN ('queued', 'active')) THEN 'open' "
                     "WHEN EXISTS (SELECT 1 FROM escalations er WHERE er.conversation_id = c.id) THEN 'resolved' END)")
    _CONV_LISTS = ("ARRAY(SELECT DISTINCT category FROM escalations e WHERE e.conversation_id = c.id ORDER BY 1), "
                   "ARRAY(SELECT DISTINCT assignee FROM escalations e WHERE e.conversation_id = c.id "
                   "AND assignee IS NOT NULL ORDER BY 1), "
                   "ARRAY(SELECT id FROM escalations e WHERE e.conversation_id = c.id ORDER BY created_at)")

    def _conv_where(self, f: ConversationFilter, time_col: str = "c.started_at") -> tuple[str, list[Any]]:
        sql, args = ["TRUE"], []
        if f.persona:
            sql.append("c.persona = %s")
            args.append(f.persona)
        if f.escalated is not None:
            sql.append(f"{self._CONV_ESCALATED} = %s")
            args.append(f.escalated)
        if f.negative_feedback:
            sql.append("EXISTS (SELECT 1 FROM turns t2 JOIN feedback f2 ON f2.turn_id = t2.id "
                       "WHERE t2.conversation_id = c.id AND f2.rating = -1)")
        if f.q:
            sql.append("(c.id ILIKE %s OR EXISTS (SELECT 1 FROM turns t3 WHERE t3.conversation_id = c.id "
                       "AND (t3.user_text ILIKE %s OR t3.bot_text ILIKE %s)) "
                       "OR EXISTS (SELECT 1 FROM escalations e3 WHERE e3.conversation_id = c.id AND e3.id ILIKE %s))")
            args += [f"%{f.q}%"] * 4
        if f.intent:
            sql.append("EXISTS (SELECT 1 FROM turns t4 WHERE t4.conversation_id = c.id AND t4.intent = %s)")
            args.append(f.intent)
        if f.errors_only:
            sql.append(f"{self._CONV_ERRORS} > 0")
        if f.category:
            sql.append("EXISTS (SELECT 1 FROM escalations e4 WHERE e4.conversation_id = c.id AND e4.category = %s)")
            args.append(f.category)
        if f.assignee:
            sql.append("EXISTS (SELECT 1 FROM escalations e5 WHERE e5.conversation_id = c.id AND e5.assignee = %s)")
            args.append(f.assignee)
        if f.handoff_status:
            sql.append(f"{self._CONV_HANDOFF} = %s")
            args.append(f.handoff_status)
        if f.since_epoch:
            sql.append(f"{time_col} >= %s")
            args.append(datetime.fromtimestamp(f.since_epoch, tz=timezone.utc))
        if f.until_epoch:
            sql.append(f"{time_col} < %s")
            args.append(datetime.fromtimestamp(f.until_epoch, tz=timezone.utc))
        return " AND ".join(sql), args

    async def list_conversations(self, f: ConversationFilter) -> dict[str, Any]:
        where, args = self._conv_where(f)
        async with self.pool.connection() as conn:
            total = (await (await conn.execute(f"SELECT count(*) FROM conversations c WHERE {where}", args)).fetchone())[0]
            rows = await (await conn.execute(
                f"""SELECT c.id, c.persona, c.started_at, c.last_at, c.turns, {self._CONV_ESCALATED},
                      (SELECT user_text FROM turns t WHERE t.conversation_id = c.id ORDER BY created_at LIMIT 1),
                      (SELECT count(*) FROM turns t JOIN feedback fb ON fb.turn_id = t.id
                        WHERE t.conversation_id = c.id AND fb.rating = -1),
                      {self._CONV_ERRORS}, {self._CONV_HANDOFF}, {self._CONV_LISTS}
                    FROM conversations c WHERE {where}
                    ORDER BY c.last_at DESC LIMIT %s OFFSET %s""",
                [*args, f.limit, f.offset],
            )).fetchall()
        return {"total": total, "items": [
            {"id": r[0], "persona": r[1], "started_at": _iso(r[2]), "last_at": _iso(r[3]), "turns": r[4],
             "escalated": r[5], "preview": r[6] or "", "negative_feedback": r[7], "errors": r[8],
             "handoff_status": r[9], "categories": list(r[10]), "assignees": list(r[11]), "tickets": list(r[12])}
            for r in rows]}

    async def conversation_log(self, conversation_id: str) -> dict[str, Any] | None:
        page = await self._conv_page(conversation_id)
        if not page:
            return None
        async with self.pool.connection() as conn:
            turns = await (await conn.execute(
                """SELECT t.id, t.user_text, t.bot_text, t.intent, t.sources, t.tool_calls, t.stop, t.latency_ms,
                          t.created_at, fb.rating
                   FROM turns t LEFT JOIN feedback fb ON fb.turn_id = t.id
                   WHERE t.conversation_id = %s ORDER BY t.created_at""", (conversation_id,))).fetchall()
            escs = await (await conn.execute(
                f"SELECT {self._ESC_COLS} FROM escalations WHERE conversation_id = %s ORDER BY created_at",
                (conversation_id,))).fetchall()
            esc_rows = [self._esc_row(r) for r in escs]
            msgs = await (await conn.execute(
                "SELECT id, escalation_id, sender, author, text, created_at FROM handoff_messages "
                "WHERE escalation_id = ANY(%s) ORDER BY id", ([e["id"] for e in esc_rows],))).fetchall()
        return {
            "conversation": page,
            "events": merge_events(
                [{"id": r[0], "user_text": r[1], "bot_text": r[2], "intent": r[3], "sources": r[4] or [],
                  "tool_calls": r[5] or [], "stop": r[6], "latency_ms": r[7], "created_at": _iso(r[8]),
                  "rating": r[9]} for r in turns],
                esc_rows,
                [{"id": r[0], "escalation_id": r[1], "sender": r[2], "author": r[3], "text": r[4],
                  "created_at": _iso(r[5])} for r in msgs],
            ),
        }

    async def _conv_page(self, conversation_id: str) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            r = await (await conn.execute(
                f"""SELECT c.id, c.persona, c.started_at, c.last_at, c.turns, {self._CONV_ESCALATED},
                           {self._CONV_ERRORS}, {self._CONV_HANDOFF}, {self._CONV_LISTS}
                    FROM conversations c WHERE c.id = %s""", (conversation_id,))).fetchone()
        return {"id": r[0], "persona": r[1], "started_at": _iso(r[2]), "last_at": _iso(r[3]), "turns": r[4],
                "escalated": r[5], "errors": r[6], "handoff_status": r[7], "categories": list(r[8]),
                "assignees": list(r[9]), "tickets": list(r[10])} if r else None

    async def kb_usage(self, since_epoch: float) -> dict[str, dict[str, Any]]:
        since = datetime.fromtimestamp(since_epoch, tz=timezone.utc)
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                """SELECT s.doc, count(*), count(*) FILTER (WHERE fb.rating = -1), max(t.created_at)
                   FROM turns t CROSS JOIN LATERAL (SELECT DISTINCT jsonb_array_elements_text(t.sources) AS doc) s
                   LEFT JOIN feedback fb ON fb.turn_id = t.id
                   WHERE t.created_at >= %s GROUP BY s.doc""", (since,))).fetchall()
        return {r[0]: {"uses": r[1], "negative": r[2], "last_used": _iso(r[3])} for r in rows}

    async def export_turns(self, f: ConversationFilter) -> list[dict[str, Any]]:
        where, args = self._conv_where(f, time_col="t.created_at")
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                f"""SELECT t.conversation_id, t.id, t.created_at, t.persona, t.intent, t.intent_confidence,
                           t.user_text, t.bot_text, t.sources, t.tool_calls, t.stop, t.model, t.latency_ms,
                           fb.rating, {self._CONV_ESCALATED}
                    FROM turns t JOIN conversations c ON c.id = t.conversation_id
                    LEFT JOIN feedback fb ON fb.turn_id = t.id
                    WHERE {where} ORDER BY t.created_at""", args)).fetchall()
        return [_export_row({"conversation_id": r[0], "id": r[1], "created_at": _iso(r[2]), "persona": r[3],
                             "intent": r[4], "intent_confidence": r[5], "user_text": r[6], "bot_text": r[7],
                             "sources": r[8] or [], "tool_calls": r[9] or [], "stop": r[10], "model": r[11],
                             "latency_ms": r[12], "rating": r[13]}, r[14]) for r in rows]

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
