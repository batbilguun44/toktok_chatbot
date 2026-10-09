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

-- Staff accounts (SSO) and the audit trail of every admin / operator action.
CREATE TABLE IF NOT EXISTS staff_users (
    email          TEXT PRIMARY KEY,
    name           TEXT,
    roles          JSONB NOT NULL DEFAULT '[]',
    active         BOOLEAN NOT NULL DEFAULT true,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    target      TEXT,
    detail      JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Versioned knowledge CMS (replaces knowledge_docs, which is migrated once on startup).
CREATE TABLE IF NOT EXISTS kb_docs (
    id                 TEXT PRIMARY KEY,
    status             TEXT NOT NULL DEFAULT 'active',     -- active | archived
    published_version  INT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kb_versions (
    doc_id          TEXT NOT NULL,
    version         INT NOT NULL,
    persona         TEXT NOT NULL,
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    tags            JSONB NOT NULL DEFAULT '[]',
    visibility      TEXT NOT NULL DEFAULT 'public',
    state           TEXT NOT NULL,                       -- draft | in_review | rejected | published | superseded
    source          TEXT NOT NULL DEFAULT 'editor',
    author          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewer        TEXT,
    reviewed_at     TIMESTAMPTZ,
    review_comment  TEXT,
    PRIMARY KEY (doc_id, version)
);
CREATE INDEX IF NOT EXISTS kb_versions_state_idx ON kb_versions (state);

-- Live human handoff: escalations double as the operator queue.
ALTER TABLE escalations ADD COLUMN IF NOT EXISTS assignee TEXT;
ALTER TABLE escalations ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE escalations ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;
ALTER TABLE escalations ADD COLUMN IF NOT EXISTS resolution TEXT;
ALTER TABLE escalations ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'phone';
UPDATE escalations SET status = 'queued' WHERE status = 'open';
ALTER TABLE escalations ALTER COLUMN status SET DEFAULT 'queued';

CREATE TABLE IF NOT EXISTS handoff_messages (
    id             BIGSERIAL PRIMARY KEY,
    escalation_id  TEXT NOT NULL REFERENCES escalations(id) ON DELETE CASCADE,
    sender         TEXT NOT NULL,                 -- user | operator | system
    author         TEXT,                          -- operator email
    text           TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS handoff_messages_esc_idx ON handoff_messages (escalation_id, id);

-- Online evaluation: sampled production turns graded by an LLM judge.
CREATE TABLE IF NOT EXISTS judgements (
    turn_id            TEXT PRIMARY KEY REFERENCES turns(id) ON DELETE CASCADE,
    grounded           TEXT NOT NULL,           -- yes | partial | no | n/a
    answered           BOOLEAN NOT NULL,
    score              SMALLINT NOT NULL,
    unsupported_claims JSONB NOT NULL DEFAULT '[]',
    rationale          TEXT,
    model              TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Improvement loop runs (clustered unanswered questions + FAQ drafts).
CREATE TABLE IF NOT EXISTS insight_runs (
    id          TEXT PRIMARY KEY,
    actor       TEXT NOT NULL,
    stats       JSONB NOT NULL DEFAULT '{}',
    clusters    JSONB NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
