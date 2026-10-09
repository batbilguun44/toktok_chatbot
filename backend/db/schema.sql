-- TokTok chatbot schema (PostgreSQL 16 + pgvector).
-- Applied automatically at backend startup (idempotent).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS conversations (
    id            TEXT PRIMARY KEY,           -- session id
    persona       TEXT NOT NULL,
    user_ref      TEXT NOT NULL,              -- salted hash of the TokTok user id
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    turns         INT NOT NULL DEFAULT 0,
    escalated     BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS turns (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    persona         TEXT NOT NULL,
    user_text       TEXT NOT NULL,            -- PII-redacted
    bot_text        TEXT NOT NULL,            -- PII-redacted
    intent          TEXT,
    intent_confidence REAL,
    route_source    TEXT,
    safety          TEXT,
    tool_calls      JSONB NOT NULL DEFAULT '[]',
    sources         JSONB NOT NULL DEFAULT '[]',
    stop            TEXT,
    model           TEXT,
    latency_ms      INT,
    usage           JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS turns_created_idx ON turns (created_at);
CREATE INDEX IF NOT EXISTS turns_conv_idx ON turns (conversation_id);

CREATE TABLE IF NOT EXISTS feedback (
    turn_id     TEXT PRIMARY KEY REFERENCES turns(id) ON DELETE CASCADE,
    rating      SMALLINT NOT NULL CHECK (rating IN (-1, 1)),
    comment     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS escalations (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    persona         TEXT NOT NULL,
    user_ref        TEXT NOT NULL,
    category        TEXT NOT NULL,
    summary         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS escalations_status_idx ON escalations (status, created_at);

-- Knowledge documents edited by the Operation team via the admin API.
CREATE TABLE IF NOT EXISTS knowledge_docs (
    id          TEXT PRIMARY KEY,
    persona     TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    tags        JSONB NOT NULL DEFAULT '[]',
    visibility  TEXT NOT NULL DEFAULT 'public',
    owner       TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Chunk embeddings for the vector leg of hybrid search.
-- The dimension must match EMBEDDING_DIM (voyage-3.5 default: 1024).
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id            TEXT PRIMARY KEY,
    doc_id        TEXT NOT NULL,
    persona       TEXT NOT NULL,
    title         TEXT NOT NULL,
    content       TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    embedding     vector(1024) NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS knowledge_chunks_hnsw
    ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
