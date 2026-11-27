-- Events from connected calendars, read-only copies. Each sync re-reads a fixed window (30 days back, 180 ahead)
-- and replaces it, so cancelled or moved events simply disappear. Repeating events are stored one row per
-- occurrence. Timed events and all-day events are stored differently on purpose: an all-day event is a date range
-- (end date not included), never a timestamp, so it can't shift a day when shown in another time zone.
CREATE TABLE calendar_events (
    id                 uuid PRIMARY KEY DEFAULT uuidv7(),
    connection_id      uuid        NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
    calendar_id        text        NOT NULL,
    event_id           text        NOT NULL,     -- the occurrence's id
    recurring_event_id text,                     -- the series it belongs to, if any
    title              text        NOT NULL,
    location           text,
    description        text,
    all_day            boolean     NOT NULL,
    start_at           timestamptz,              -- timed events
    end_at             timestamptz,
    start_date         date,                     -- all-day events; end_date is the day after the last day
    end_date           date,
    time_zone          text,                     -- the event's own time zone, as Google gave it
    attendees          integer     NOT NULL DEFAULT 0,
    status             text        NOT NULL,     -- confirmed | tentative
    updated            timestamptz,
    UNIQUE (connection_id, calendar_id, event_id),
    CHECK (CASE WHEN all_day
                THEN start_date IS NOT NULL AND end_date IS NOT NULL AND start_at IS NULL AND end_at IS NULL
                ELSE start_at IS NOT NULL AND end_at IS NOT NULL AND start_date IS NULL AND end_date IS NULL END)
);
CREATE INDEX calendar_events_start_at_idx ON calendar_events (start_at) WHERE NOT all_day;
CREATE INDEX calendar_events_start_date_idx ON calendar_events (start_date) WHERE all_day;
