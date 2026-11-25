-- M1: owner login, sessions, conversations, messages (full-text searchable).
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE owner (
    id            smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    password_hash text        NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    id           uuid PRIMARY KEY DEFAULT uuidv7(),
    token_hash   bytea       NOT NULL UNIQUE,
    label        text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    revoked_at   timestamptz
);

CREATE TABLE conversations (
    id         uuid PRIMARY KEY DEFAULT uuidv7(),
    title      text        NOT NULL DEFAULT 'New chat',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE messages (
    id              uuid PRIMARY KEY DEFAULT uuidv7(),
    conversation_id uuid        NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            text        NOT NULL CHECK (role IN ('user', 'assistant')),
    content         text        NOT NULL,
    model           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    search          tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
);

CREATE INDEX messages_conversation_idx ON messages (conversation_id, created_at);
CREATE INDEX messages_search_idx ON messages USING gin (search);
