-- Per-chat switch for searching Gmail (`/gmail off`). On by default: connecting Gmail is the opt-in, and pal still
-- searches only when a message is about mail, on a local model.
ALTER TABLE conversations ADD COLUMN gmail boolean NOT NULL DEFAULT true;
