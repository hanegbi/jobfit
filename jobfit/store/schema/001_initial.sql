CREATE TABLE companies (
    id              TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    career_url      TEXT,
    review_decision TEXT,
    host            TEXT,
    industry        TEXT,
    size            TEXT,
    address_city    TEXT,
    last_checked    TEXT
);

CREATE TABLE jobs (
    id                   TEXT PRIMARY KEY,
    company_id           TEXT NOT NULL REFERENCES companies(id),
    title                TEXT NOT NULL,
    url                  TEXT,
    description          TEXT NOT NULL DEFAULT '',
    location             TEXT,
    city                 TEXT,
    is_remote            INTEGER NOT NULL DEFAULT 0,
    department           TEXT,
    employment_type      TEXT,
    status               TEXT NOT NULL,
    first_seen           TEXT,
    last_seen            TEXT,
    posted_at            TEXT,
    closed_at            TEXT,
    closed_reason        TEXT,
    years_required       INTEGER,
    is_referral          INTEGER NOT NULL DEFAULT 0,
    referral_contact     TEXT,
    source_language      TEXT,
    title_original       TEXT,
    description_original TEXT,
    scrape_source        TEXT,
    job_evidence         TEXT
);

CREATE TABLE job_scores (
    job_id      TEXT NOT NULL REFERENCES jobs(id),
    profile_id  TEXT NOT NULL,
    score       REAL,
    coverage    REAL,
    confidence  TEXT,
    matched     TEXT,
    cache_key   TEXT,
    PRIMARY KEY (job_id, profile_id)
);

CREATE INDEX idx_jobs_company ON jobs(company_id);
CREATE INDEX idx_jobs_status  ON jobs(status);
CREATE INDEX idx_jobs_city    ON jobs(city);
CREATE INDEX idx_scores_rank  ON job_scores(profile_id, score DESC);

-- External-content FTS: the index stores no copy of the text, so the
-- triggers below are what keep it true. Without them a title edit leaves
-- the old words searchable forever.
CREATE VIRTUAL TABLE jobs_fts USING fts5(title, description, content='jobs', content_rowid='rowid');

CREATE TRIGGER jobs_fts_insert AFTER INSERT ON jobs BEGIN
    INSERT INTO jobs_fts(rowid, title, description) VALUES (new.rowid, new.title, new.description);
END;

CREATE TRIGGER jobs_fts_delete AFTER DELETE ON jobs BEGIN
    INSERT INTO jobs_fts(jobs_fts, rowid, title, description) VALUES ('delete', old.rowid, old.title, old.description);
END;

CREATE TRIGGER jobs_fts_update AFTER UPDATE ON jobs BEGIN
    INSERT INTO jobs_fts(jobs_fts, rowid, title, description) VALUES ('delete', old.rowid, old.title, old.description);
    INSERT INTO jobs_fts(rowid, title, description) VALUES (new.rowid, new.title, new.description);
END;
