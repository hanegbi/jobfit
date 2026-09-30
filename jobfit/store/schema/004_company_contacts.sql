-- Who those contacts actually are. connection_count (003) answers "do I know
-- anyone here"; a job card has room to answer "who", which is the thing that
-- makes a referral happen. Derived from the same uploaded CSV and replaced
-- wholesale whenever it is re-read, so it is never stale relative to the count.
CREATE TABLE company_contacts (
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    position   TEXT,
    url        TEXT,
    PRIMARY KEY (company_id, name, position)
);
CREATE INDEX idx_company_contacts_company ON company_contacts(company_id);
