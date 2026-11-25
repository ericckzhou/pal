-- M4: projects, tasks, notes (a decision is a note with kind='decision'), reminders, fired notifications,
-- and action proposals the model makes (the owner confirms them; the model never can, S6).
CREATE TABLE projects (
    id         uuid PRIMARY KEY DEFAULT uuidv7(),
    name       text        NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    summary    text        NOT NULL DEFAULT '' CHECK (length(summary) <= 2000),
    status     text        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX projects_name_idx ON projects (lower(name));

CREATE TABLE tasks (
    id                     uuid PRIMARY KEY DEFAULT uuidv7(),
    project_id             uuid REFERENCES projects(id) ON DELETE SET NULL,
    title                  text        NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
    status                 text        NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
    due_date               date,
    source_conversation_id uuid REFERENCES conversations(id) ON DELETE SET NULL,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now(),
    done_at                timestamptz
);
CREATE INDEX tasks_open_idx ON tasks (status, due_date NULLS LAST, created_at);
CREATE INDEX tasks_project_idx ON tasks (project_id);

CREATE TABLE notes (
    id         uuid PRIMARY KEY DEFAULT uuidv7(),
    project_id uuid REFERENCES projects(id) ON DELETE SET NULL,
    kind       text        NOT NULL DEFAULT 'note' CHECK (kind IN ('note', 'decision')),
    text       text        NOT NULL CHECK (length(text) BETWEEN 1 AND 5000),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    search     tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    embedding  vector(768)
);
CREATE INDEX notes_project_idx ON notes (project_id, created_at DESC);
CREATE INDEX notes_search_idx ON notes USING gin (search);
CREATE INDEX notes_embedding_idx ON notes USING hnsw (embedding vector_cosine_ops);

CREATE TABLE reminders (
    id         uuid PRIMARY KEY DEFAULT uuidv7(),
    title      text        NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
    due_at     timestamptz NOT NULL,           -- the next time it fires
    rrule      text,                           -- e.g. FREQ=WEEKLY;BYDAY=MO (NULL = once)
    anchor     timestamp,                      -- local wall-clock start the rule repeats from
    active     boolean     NOT NULL DEFAULT true,
    task_id    uuid REFERENCES tasks(id) ON DELETE CASCADE,
    project_id uuid REFERENCES projects(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX reminders_due_idx ON reminders (due_at) WHERE active;

CREATE TABLE notifications (
    id           uuid PRIMARY KEY DEFAULT uuidv7(),
    reminder_id  uuid REFERENCES reminders(id) ON DELETE CASCADE,
    title        text        NOT NULL,
    fired_at     timestamptz NOT NULL DEFAULT now(),
    dismissed_at timestamptz
);
CREATE INDEX notifications_open_idx ON notifications (fired_at DESC) WHERE dismissed_at IS NULL;

CREATE TABLE proposals (
    id              uuid PRIMARY KEY DEFAULT uuidv7(),
    conversation_id uuid        NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    message_id      uuid REFERENCES messages(id) ON DELETE SET NULL,
    action          text        NOT NULL CHECK (action IN ('create_task', 'set_reminder')),
    payload         jsonb       NOT NULL,
    status          text        NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'confirmed')),
    result_id       uuid,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX proposals_conversation_idx ON proposals (conversation_id, status);

ALTER TABLE conversations ADD COLUMN project_id uuid REFERENCES projects(id) ON DELETE SET NULL;
