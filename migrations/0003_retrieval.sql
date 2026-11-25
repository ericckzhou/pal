-- M3: documents and chunks; vectors for messages (memories already have a column); sources on replies.
CREATE TABLE documents (
    id          uuid PRIMARY KEY DEFAULT uuidv7(),
    title       text        NOT NULL,
    filename    text        NOT NULL,
    source_path text UNIQUE,  -- absolute path for files added from this PC; NULL for uploads
    kind        text        NOT NULL CHECK (kind IN ('pdf', 'markdown', 'text')),
    sha256      bytea       NOT NULL,
    size_bytes  integer     NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE chunks (
    id          uuid PRIMARY KEY DEFAULT uuidv7(),
    document_id uuid    NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ord         integer NOT NULL,
    heading     text,
    page        integer,
    content     text    NOT NULL,
    search      tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(heading, '') || ' ' || content)) STORED,
    embedding   vector(768)
);

CREATE INDEX chunks_document_idx ON chunks (document_id, ord);
CREATE INDEX chunks_search_idx ON chunks USING gin (search);
CREATE INDEX chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);

ALTER TABLE messages ADD COLUMN embedding vector(768), ADD COLUMN sources jsonb;
CREATE INDEX messages_embedding_idx ON messages USING hnsw (embedding vector_cosine_ops);
CREATE INDEX memories_embedding_idx ON memories USING hnsw (embedding vector_cosine_ops);
