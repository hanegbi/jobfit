import { describe, expect, it } from "vitest";

import type { JobRow } from "../types";
import { toItems } from "./JobList";

function job(id: string, companyId: string, company: string): JobRow {
  return {
    id, company_id: companyId, company, title: `job ${id}`, url: `https://x/${id}`,
    location: null, city: null, is_remote: false, department: null, employment_type: null,
    status: "new", first_seen: "2026-09-01", last_seen: "2026-09-01", posted_at: null,
    years_required: null, is_referral: false, referral_contact: null, source_language: null,
    connection_count: 0, industry: null, best_score: 50,
    snippet: null, description_length: 0, contacts: [],
    liked: false, hidden: false, sent: false, reached_out: false,
  };
}

describe("grouping the list by company", () => {
  const jobs = [job("a", "wiz", "Wiz"), job("b", "orca", "Orca"), job("c", "wiz", "Wiz")];

  it("is a plain row list when off", () => {
    expect(toItems(jobs, false).map((i) => i.kind)).toEqual(["job", "job", "job"]);
  });

  it("keeps the company order the sort produced", () => {
    // Wiz first because its best job sorted first - not alphabetically.
    expect(toItems(jobs, true).map((i) => (i.kind === "header" ? `# ${i.company}` : i.job.id))).toEqual(
      ["# Wiz", "a", "c", "# Orca", "b"],
    );
  });

  it("counts the jobs under each heading", () => {
    const heads = toItems(jobs, true).filter((i) => i.kind === "header");
    expect(heads.map((h) => (h.kind === "header" ? h.n : 0))).toEqual([2, 1]);
  });
});
