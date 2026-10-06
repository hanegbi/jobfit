-- What the scrape actually saw when it opened the job's own page, and how
-- the posting itself describes where the work happens.
--
-- fetch_status is "ok" | "blocked" | "empty", written by the enricher
-- (jobfit/scrape/enrich.py). It is the difference between "this job has no
-- description because the posting is thin" and "because Cloudflare served
-- us a challenge page" - the second is a scrape to retry with Playwright,
-- the first is not, and without the column both looked identical.
--
-- work_mode is "onsite" | "hybrid" | "remote", read from the posting rather
-- than inferred from the company. is_remote stays: it is the boolean the
-- API and the old page filter on, and "hybrid" has no honest answer to it.
ALTER TABLE jobs ADD COLUMN fetch_status TEXT;
ALTER TABLE jobs ADD COLUMN work_mode TEXT;

CREATE INDEX idx_jobs_fetch_status ON jobs(fetch_status);
