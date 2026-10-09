"""Short-term conversation memory (Redis in production, in-process for dev)."""

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass
class SessionState:
    session_id: str
    persona: str
    user_id: str
    provider: str
    # Provider wire-format history (append-only, see llm/anthropic_provider.py).
    history: list[dict[str, Any]] = field(default_factory=list)
    # Plain-text transcript tail for the router: [(role, text), ...]
    recent: list[tuple[str, str]] = field(default_factory=list)
    turn_index: int = 0
    # Scratch space for tools (pending confirmations etc.).
    scratch: dict[str, Any] = field(default_factory=dict)
    escalated: bool = False
    created_at: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str | bytes) -> "SessionState":
        d = json.loads(raw)
        d["recent"] = [tuple(x) for x in d.get("recent", [])]
        return cls(**d)


class SessionStore(Protocol):
    async def get(self, session_id: str) -> SessionState | None: ...

    async def save(self, state: SessionState) -> None: ...

    async def acquire(self, session_id: str, ttl: int = 120) -> bool:
        """Per-session turn lock so two tabs cannot interleave one history."""
        ...

    async def release(self, session_id: str) -> None: ...

    async def hit_rate(self, key: str, window_seconds: int = 60) -> int:
        """Increment and return the request count for `key` in the current window."""
        ...


class InMemorySessionStore:
    def __init__(self, ttl_seconds: int):
        self.ttl = ttl_seconds
        self._data: dict[str, tuple[float, str]] = {}
        self._locks: dict[str, float] = {}
        self._rates: dict[tuple[str, int], int] = {}

    async def get(self, session_id: str) -> SessionState | None:
        item = self._data.get(session_id)
        if not item or item[0] < time.time():
            self._data.pop(session_id, None)
            return None
        return SessionState.from_json(item[1])

    async def save(self, state: SessionState) -> None:
        self._data[state.session_id] = (time.time() + self.ttl, state.to_json())

    async def acquire(self, session_id: str, ttl: int = 120) -> bool:
        now = time.time()
        if self._locks.get(session_id, 0) > now:
            return False
        self._locks[session_id] = now + ttl
        return True

    async def release(self, session_id: str) -> None:
        self._locks.pop(session_id, None)

    async def hit_rate(self, key: str, window_seconds: int = 60) -> int:
        bucket = (key, int(time.time() // window_seconds))
        self._rates[bucket] = self._rates.get(bucket, 0) + 1
        return self._rates[bucket]


class RedisSessionStore:
    PREFIX = "toktok:session:"

    def __init__(self, redis_client, ttl_seconds: int):  # redis.asyncio.Redis
        self.r = redis_client
        self.ttl = ttl_seconds

    async def get(self, session_id: str) -> SessionState | None:
        raw = await self.r.get(self.PREFIX + session_id)
        return SessionState.from_json(raw) if raw else None

    async def save(self, state: SessionState) -> None:
        await self.r.set(self.PREFIX + state.session_id, state.to_json(), ex=self.ttl)

    async def acquire(self, session_id: str, ttl: int = 120) -> bool:
        return bool(await self.r.set(f"toktok:lock:{session_id}", "1", nx=True, ex=ttl))

    async def release(self, session_id: str) -> None:
        await self.r.delete(f"toktok:lock:{session_id}")

    async def hit_rate(self, key: str, window_seconds: int = 60) -> int:
        k = f"toktok:rate:{key}:{int(time.time() // window_seconds)}"
        n = await self.r.incr(k)
        if n == 1:
            await self.r.expire(k, window_seconds * 2)
        return int(n)
