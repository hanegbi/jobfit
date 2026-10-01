-- Scrape-time role-family classification (see jobfit/ats_scorer/job_classifier.py).
-- Written once by classify_job() right after enrichment, in service.scrape();
-- scoring only ever reads these back, it never classifies. taxonomy_version
-- is a content hash of the taxonomy data files - a backfill compares it
-- against job_classifier.taxonomy_version() to find rows that need
-- reclassifying after a taxonomy edit, with no network at all.
ALTER TABLE jobs ADD COLUMN family TEXT;
ALTER TABLE jobs ADD COLUMN canonical_title TEXT;
ALTER TABLE jobs ADD COLUMN family_confidence TEXT;
ALTER TABLE jobs ADD COLUMN taxonomy_version TEXT;

CREATE INDEX idx_jobs_family ON jobs(family);
CREATE INDEX idx_jobs_taxonomy_version ON jobs(taxonomy_version);
