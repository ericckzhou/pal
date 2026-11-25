-- M5: paired devices, their passkeys, one-time pairing codes; sessions belong to a device (NULL = this PC, password).
CREATE TABLE devices (
    id           uuid PRIMARY KEY DEFAULT uuidv7(),
    name         text        NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
    kind         text        NOT NULL CHECK (kind IN ('phone', 'laptop', 'tablet', 'other')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz,
    revoked_at   timestamptz
);

CREATE TABLE passkeys (
    credential_id bytea PRIMARY KEY,
    device_id     uuid        NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    public_key    bytea       NOT NULL,
    sign_count    bigint      NOT NULL DEFAULT 0,
    rp_id         text        NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_used_at  timestamptz
);
CREATE INDEX passkeys_device_idx ON passkeys (device_id);

CREATE TABLE pairing_codes (
    code_hash   bytea PRIMARY KEY,
    device_name text        NOT NULL,
    kind        text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    used_at     timestamptz
);

ALTER TABLE sessions ADD COLUMN device_id uuid REFERENCES devices(id) ON DELETE CASCADE;
CREATE INDEX sessions_device_idx ON sessions (device_id);
