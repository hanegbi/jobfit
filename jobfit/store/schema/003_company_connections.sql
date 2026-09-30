-- How many of the user's LinkedIn contacts work at this company. Derived from
-- the uploaded CSV and refreshed whenever it changes; a column rather than a
-- post-filter so "jobs where I know someone" is a query.
ALTER TABLE companies ADD COLUMN connection_count INTEGER NOT NULL DEFAULT 0;
CREATE INDEX idx_companies_connections ON companies(connection_count) WHERE connection_count > 0;
