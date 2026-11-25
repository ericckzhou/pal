-- Dashboard: token use per reply, step cards, per-chat model, paused reminders, editable settings.
ALTER TABLE messages
    ADD COLUMN prompt_tokens     integer,
    ADD COLUMN completion_tokens integer,
    ADD COLUMN steps             jsonb;

ALTER TABLE conversations ADD COLUMN model text;  -- NULL = the default model from settings

ALTER TABLE reminders ADD COLUMN paused boolean NOT NULL DEFAULT false;
DROP INDEX IF EXISTS reminders_due_idx;
CREATE INDEX reminders_due_idx ON reminders (due_at) WHERE active AND NOT paused;

CREATE INDEX messages_created_idx ON messages (created_at) WHERE role = 'assistant';

CREATE TABLE app_settings (
    key        text PRIMARY KEY,
    value      jsonb       NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
