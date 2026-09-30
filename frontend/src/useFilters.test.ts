import { describe, expect, it } from "vitest";

import { filtersToQuery, queryToFilters } from "./useFilters";

describe("filter state in the URL", () => {
  it("round-trips every filter", () => {
    const filters = {
      q: "mlops", city: "Tel Aviv", minScore: 60, liked: true, hasConnection: true,
      sort: "date" as const, page: 3,
    };
    expect(queryToFilters(filtersToQuery(filters))).toMatchObject(filters);
  });

  it("omits empty and default values so the URL stays readable", () => {
    expect(filtersToQuery({ q: "", city: null, page: 1, sort: "score", profile: "best" })).toBe("");
  });

  it("keeps a query with special characters intact", () => {
    expect(queryToFilters(filtersToQuery({ q: "C++ & node.js" })).q).toBe("C++ & node.js");
  });

  it("distinguishes false from unset", () => {
    expect(queryToFilters(filtersToQuery({ hidden: false })).hidden).toBe(false);
    expect(queryToFilters("").hidden).toBe(null);
  });

  it("ignores unknown parameters rather than throwing", () => {
    expect(queryToFilters("?nonsense=1&q=devops").q).toBe("devops");
  });

  it("falls back to page 1 and score sort on nonsense", () => {
    const filters = queryToFilters("?page=abc&sort=sideways");
    expect(filters.page).toBe(1);
    expect(filters.sort).toBe("score");
  });
});
