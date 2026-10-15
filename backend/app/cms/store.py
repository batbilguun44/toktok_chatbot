"""Versioned knowledge documents.

Every edit is a new version row; nothing is overwritten. A document has at most
one open version (draft / in_review / rejected) and at most one published
version, which is what the chatbot uses.

    draft ──submit──▶ in_review ──approve──▶ published ──(next publish)──▶ superseded
      ▲                  │
      └──── edit ◀── rejected
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from app.rag.documents import KnowledgeDoc

State = Literal["draft", "in_review", "rejected", "published", "superseded"]
OPEN_STATES = ("draft", "in_review", "rejected")


@dataclass
class DocVersion:
    doc_id: str
    version: int
    persona: str
    title: str
    body: str
    tags: list[str] = field(default_factory=list)
    visibility: str = "public"
    state: State = "draft"
    source: str = "editor"  # editor | git | import | ai-suggestion | rollback | legacy
    author: str = ""
    created_at: str = ""
    updated_at: str = ""
    reviewer: str | None = None
    reviewed_at: str | None = None
    review_comment: str | None = None

    def to_doc(self) -> KnowledgeDoc:
        return KnowledgeDoc(id=self.doc_id, persona=self.persona, title=self.title, body=self.body,
                            tags=self.tags, visibility=self.visibility, updated_at=self.updated_at[:10],
                            owner=self.author, source=self.source)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DocRecord:
    id: str
    status: Literal["active", "archived"] = "active"
    published_version: int | None = None
    updated_at: str = ""


class KnowledgeStore(Protocol):
    async def get_doc(self, doc_id: str) -> DocRecord | None: ...
    async def list_docs(self) -> list[DocRecord]: ...
    async def versions(self, doc_id: str) -> list[DocVersion]: ...
    async def latest_versions(self) -> dict[str, list[DocVersion]]:
        """doc_id -> [published, open] versions (whichever exist) for listing."""
        ...
    async def insert_version(self, v: DocVersion) -> None: ...
    async def update_version(self, v: DocVersion) -> None: ...
    async def delete_version(self, doc_id: str, version: int) -> None: ...
    async def upsert_doc(self, d: DocRecord) -> None: ...
    async def revision(self) -> str:
        """Changes whenever the published set changes (cheap; polled by replicas)."""
        ...


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InMemoryKnowledgeStore:
    def __init__(self) -> None:
        self.docs: dict[str, DocRecord] = {}
        self.vers: dict[str, dict[int, DocVersion]] = {}
        self._rev = 0

    async def get_doc(self, doc_id):
        return self.docs.get(doc_id)

    async def list_docs(self):
        return sorted(self.docs.values(), key=lambda d: d.id)

    async def versions(self, doc_id):
        return sorted(self.vers.get(doc_id, {}).values(), key=lambda v: v.version, reverse=True)

    async def latest_versions(self):
        out = {}
        for doc_id, vs in self.vers.items():
            out[doc_id] = [v for v in vs.values() if v.state == "published" or v.state in OPEN_STATES]
        return out

    async def insert_version(self, v):
        self.vers.setdefault(v.doc_id, {})[v.version] = v

    async def update_version(self, v):
        self.vers[v.doc_id][v.version] = v

    async def delete_version(self, doc_id, version):
        self.vers.get(doc_id, {}).pop(version, None)

    async def upsert_doc(self, d):
        self.docs[d.id] = d
        self._rev += 1

    async def revision(self):
        return str(self._rev)


_V_COLS = ("doc_id, version, persona, title, body, tags, visibility, state, source, author, created_at, "
           "updated_at, reviewer, reviewed_at, review_comment")


def _row_to_version(r) -> DocVersion:
    iso = lambda x: x.isoformat() if x else None  # noqa: E731
    return DocVersion(doc_id=r[0], version=r[1], persona=r[2], title=r[3], body=r[4], tags=list(r[5] or []),
                      visibility=r[6], state=r[7], source=r[8], author=r[9], created_at=iso(r[10]),
                      updated_at=iso(r[11]), reviewer=r[12], reviewed_at=iso(r[13]), review_comment=r[14])


class PostgresKnowledgeStore:
    """`prefix` selects the table pair: "kb" (knowledge) or "prompt" (bot instructions)."""

    def __init__(self, pool, prefix: str = "kb") -> None:
        if prefix not in ("kb", "prompt"):
            raise ValueError(prefix)
        self.pool = pool
        self.docs_t, self.vers_t = f"{prefix}_docs", f"{prefix}_versions"

    async def migrate_legacy(self) -> int:
        """Import docs edited through the pre-CMS admin API as published versions."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                WITH legacy AS (
                  SELECT k.* FROM knowledge_docs k WHERE NOT EXISTS (SELECT 1 FROM kb_docs d WHERE d.id = k.id)
                ), ins AS (
                  INSERT INTO kb_versions (doc_id, version, persona, title, body, tags, visibility, state, source, author)
                  SELECT id, 1, persona, title, body, tags, visibility, 'published', 'legacy', COALESCE(owner, 'legacy')
                  FROM legacy RETURNING doc_id
                )
                INSERT INTO kb_docs (id, status, published_version) SELECT doc_id, 'active', 1 FROM ins
                """
            )
            return cur.rowcount

    async def get_doc(self, doc_id):
        async with self.pool.connection() as conn:
            r = await (await conn.execute(
                f"SELECT id, status, published_version, updated_at FROM {self.docs_t} WHERE id = %s", (doc_id,))).fetchone()
        return DocRecord(r[0], r[1], r[2], r[3].isoformat()) if r else None

    async def list_docs(self):
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                f"SELECT id, status, published_version, updated_at FROM {self.docs_t} ORDER BY id")).fetchall()
        return [DocRecord(r[0], r[1], r[2], r[3].isoformat()) for r in rows]

    async def versions(self, doc_id):
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                f"SELECT {_V_COLS} FROM {self.vers_t} WHERE doc_id = %s ORDER BY version DESC", (doc_id,))).fetchall()
        return [_row_to_version(r) for r in rows]

    async def latest_versions(self):
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                f"SELECT {_V_COLS} FROM {self.vers_t} WHERE state IN ('published', 'draft', 'in_review', 'rejected')"
            )).fetchall()
        out: dict[str, list[DocVersion]] = {}
        for r in rows:
            out.setdefault(r[0], []).append(_row_to_version(r))
        return out

    async def insert_version(self, v):
        async with self.pool.connection() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.vers_t} (doc_id, version, persona, title, body, tags, visibility, state, source,
                  author, reviewer, reviewed_at, review_comment)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (v.doc_id, v.version, v.persona, v.title, v.body, json.dumps(v.tags, ensure_ascii=False),
                 v.visibility, v.state, v.source, v.author, v.reviewer, v.reviewed_at, v.review_comment),
            )

    async def update_version(self, v):
        async with self.pool.connection() as conn:
            await conn.execute(
                f"""
                UPDATE {self.vers_t} SET persona=%s, title=%s, body=%s, tags=%s, visibility=%s, state=%s,
                  reviewer=%s, reviewed_at=%s, review_comment=%s, updated_at=now()
                WHERE doc_id=%s AND version=%s
                """,
                (v.persona, v.title, v.body, json.dumps(v.tags, ensure_ascii=False), v.visibility, v.state,
                 v.reviewer, v.reviewed_at, v.review_comment, v.doc_id, v.version),
            )

    async def delete_version(self, doc_id, version):
        async with self.pool.connection() as conn:
            await conn.execute(f"DELETE FROM {self.vers_t} WHERE doc_id=%s AND version=%s", (doc_id, version))

    async def upsert_doc(self, d):
        async with self.pool.connection() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.docs_t} (id, status, published_version) VALUES (%s,%s,%s)
                ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status,
                  published_version=EXCLUDED.published_version, updated_at=now()
                """,
                (d.id, d.status, d.published_version),
            )

    async def revision(self):
        async with self.pool.connection() as conn:
            r = await (await conn.execute(f"SELECT count(*), max(updated_at) FROM {self.docs_t}")).fetchone()
        return f"{r[0]}:{r[1].isoformat() if r[1] else ''}"
