"""Connectors: outside services (Google Calendar, Drive, Gmail) that pal reads from, read-only.

Google is a source here, not a new foundation: synced items land in pal's own tables through the same paths as local
files, and pal's database, retrieval, memory and projects stay authoritative.

- store.py: the connections and connector_items tables, and removing everything a connection brought in.
- sync.py: running a connector so a failed or interrupted sync can simply run again (writes are idempotent and the
  cursor is saved only after every item succeeded).
- hosts.py: the outside hosts each service needs, for the egress guard.
"""
