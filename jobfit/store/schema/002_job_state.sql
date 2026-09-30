-- The user's own view of each job. A scrape never writes this table, and it
-- is the reason liked/hidden stopped living in one browser's localStorage.
CREATE TABLE job_state (
    job_id      TEXT PRIMARY KEY REFERENCES jobs(id),
    liked       INTEGER NOT NULL DEFAULT 0,
    hidden      INTEGER NOT NULL DEFAULT 0,
    sent        INTEGER NOT NULL DEFAULT 0,
    reached_out INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT
);

CREATE INDEX idx_state_liked  ON job_state(liked)  WHERE liked = 1;
CREATE INDEX idx_state_hidden ON job_state(hidden) WHERE hidden = 1;
