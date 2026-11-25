-- v1.1: weekly digests. One row per week; the body is a snapshot so later edits don't rewrite history.
CREATE TABLE digests (
    id         uuid PRIMARY KEY DEFAULT uuidv7(),
    week_start date        NOT NULL UNIQUE,  -- the Monday it was made for; it covers the 7 days before
    body       jsonb       NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    read_at    timestamptz
);
