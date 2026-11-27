-- Connected outside services (Google Calendar, Drive, Gmail). One row per provider and service; the sign-in
-- tokens themselves live in the secret store, never here.
CREATE TABLE connections (
    id           uuid PRIMARY KEY DEFAULT uuidv7(),
    provider     text        NOT NULL,                -- 'google'
    service      text        NOT NULL,                -- 'calendar' | 'drive' | 'gmail'
    account      text,                                -- the account's email, shown to the owner
    scopes       text[]      NOT NULL DEFAULT '{}',   -- what the owner actually granted
    enabled      boolean     NOT NULL DEFAULT true,
    project_id   uuid        REFERENCES projects (id) ON DELETE SET NULL,
    config       jsonb       NOT NULL DEFAULT '{}',   -- chosen folders, calendars, labels
    cursor       text,                                -- where the next sync starts; saved only after a full success
    last_sync_at timestamptz,
    last_ok_at   timestamptz,
    last_error   text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, service)
);

-- Which pal document holds which outside item, so an edit or deletion over there reaches the right document.
CREATE TABLE connector_items (
    connection_id uuid NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
    external_id   text NOT NULL,
    document_id   uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    version       text,                               -- the service's etag or modified time
    PRIMARY KEY (connection_id, external_id)
);
CREATE INDEX connector_items_document_idx ON connector_items (document_id);
