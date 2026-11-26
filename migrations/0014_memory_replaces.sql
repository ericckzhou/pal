-- A newer memory can replace an older one. The older one gets status 'replaced': it stays (restorable)
-- but is no longer used. pal may *suggest* a replacement (replace_state 'suggested'); only the owner applies it.
ALTER TABLE memories DROP CONSTRAINT memories_status_check;
ALTER TABLE memories ADD CONSTRAINT memories_status_check CHECK (status IN ('proposed', 'confirmed', 'replaced'));

ALTER TABLE memories ADD COLUMN replaces_id uuid REFERENCES memories(id) ON DELETE SET NULL;
ALTER TABLE memories ADD COLUMN replace_state text CHECK (replace_state IN ('suggested', 'applied'));
-- When the replacement check last looked at this memory (NULL = not yet). Existing rows count as checked,
-- so upgrading doesn't send the whole backlog through the local model at once.
ALTER TABLE memories ADD COLUMN replace_checked_at timestamptz;
UPDATE memories SET replace_checked_at = now();

CREATE INDEX memories_replaces_idx ON memories (replaces_id);
CREATE INDEX memories_unchecked_idx ON memories (created_at) WHERE replace_checked_at IS NULL;
