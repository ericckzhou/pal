-- M2: memories (stated or inferred; proposed until the user confirms) and per-conversation scan progress.
CREATE TABLE memories (
    id                     uuid PRIMARY KEY DEFAULT uuidv7(),
    text                   text        NOT NULL CHECK (length(text) BETWEEN 1 AND 1000),
    kind                   text        NOT NULL CHECK (kind IN ('fact', 'preference', 'person', 'project', 'decision')),
    origin                 text        NOT NULL CHECK (origin IN ('stated', 'inferred')),
    status                 text        NOT NULL CHECK (status IN ('proposed', 'confirmed')),
    pinned                 boolean     NOT NULL DEFAULT false,
    source_conversation_id uuid REFERENCES conversations(id) ON DELETE SET NULL,
    source_message_id      uuid REFERENCES messages(id) ON DELETE SET NULL,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now(),
    confirmed_at           timestamptz,
    search                 tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    embedding              vector(768)  -- filled in M3
);

CREATE INDEX memories_status_idx ON memories (status, updated_at DESC);
CREATE INDEX memories_search_idx ON memories USING gin (search);
CREATE INDEX memories_source_idx ON memories (source_conversation_id);

-- Messages up to this timestamp have already been scanned for memory proposals.
ALTER TABLE conversations ADD COLUMN memory_scanned_upto timestamptz;
