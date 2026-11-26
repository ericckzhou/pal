-- Per-chat opt-in: a Claude chat may also send its matching memories, documents and notes (off by default).
ALTER TABLE conversations ADD COLUMN claude_context boolean NOT NULL DEFAULT false;
