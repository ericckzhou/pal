-- Per-chat thinking level (off/low/medium/high). NULL = the Settings default.
ALTER TABLE conversations ADD COLUMN thinking text
    CHECK (thinking IS NULL OR thinking IN ('off', 'low', 'medium', 'high'));
