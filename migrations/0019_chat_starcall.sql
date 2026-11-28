-- Per-chat switch for searching the user's StarcallOS library (`/starcall on|off`). Off by default: unlike Gmail,
-- configuring the StarcallOS connection is not consent for any chat to use it, and in a Claude chat the switch only
-- takes effect together with that chat's existing claude_context permission.
ALTER TABLE conversations ADD COLUMN starcall boolean NOT NULL DEFAULT false;
