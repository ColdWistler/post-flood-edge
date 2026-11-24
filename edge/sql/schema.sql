CREATE TABLE IF NOT EXISTS telemetry_buffer (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   INTEGER NOT NULL,                  -- original acquisition, epoch ms
    payload     TEXT    NOT NULL,                  -- {"ts": ..., "values": {...}}
    status      TEXT    NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','inflight','synced')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_buffer_status_ts ON telemetry_buffer(status, timestamp, id);
