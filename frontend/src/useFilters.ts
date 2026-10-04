import { useCallback, useEffect, useState } from "react";

import type { Filters, SortKey } from "./types";

export const EMPTY_FILTERS: Filters = {
  q: "",
  scope: "all",
  exclude: "",
  company: null,
  excludeCompany: null,
  city: null,
  department: null,
  industry: null,
  language: null,
  remote: null,
  minScore: null,
  maxYears: null,
  postedAfter: null,
  hasConnection: null,
  hasDescription: null,
  referral: null,
  liked: null,
  hidden: null,
  sent: null,
  reachedOut: null,
  profile: "best",
  sort: "score",
  group: false,
  page: 1,
};

// One place naming every filter, so a new one cannot be half-wired: the URL,
// the request and the UI all read this map.
const PARAMS: Record<string, keyof Filters> = {
  q: "q",
  scope: "scope",
  exclude: "exclude",
  company: "company",
  exclude_company: "excludeCompany",
  city: "city",
  department: "department",
  industry: "industry",
  language: "language",
  remote: "remote",
  min_score: "minScore",
  max_years: "maxYears",
  posted_after: "postedAfter",
  has_connection: "hasConnection",
  has_description: "hasDescription",
  referral: "referral",
  liked: "liked",
  hidden: "hidden",
  sent: "sent",
  reached_out: "reachedOut",
  profile: "profile",
  sort: "sort",
  group: "group",
  page: "page",
};
const BOOLEANS: (keyof Filters)[] = [
  "remote", "hasConnection", "hasDescription", "referral", "liked", "hidden", "sent", "reachedOut", "group",
];
const NUMBERS: (keyof Filters)[] = ["minScore", "maxYears", "page"];
// Filters the API takes as a comma-separated set.
export const MULTI: (keyof Filters)[] = [
  "company", "excludeCompany", "city", "department", "industry", "language",
];

/** The filters as a query string, omitting anything at its default so the URL
 * stays short enough to read and to share. `group` is UI-only and is dropped
 * before the request; the server has no opinion about grouping. */
export function filtersToQuery(filters: Partial<Filters>, forRequest = false): string {
  const params = new URLSearchParams();
  for (const [param, key] of Object.entries(PARAMS)) {
    const value = filters[key];
    if (value === null || value === undefined || value === "") continue;
    // Only `group` is a plain on/off. The rest are tri-state, where false
    // means "no" - dropping it would make "jobs I have NOT hidden" unaskable.
    if (key === "group" && value === false) continue;
    if (key === "profile" && value === "best") continue;
    if (key === "sort" && value === "score") continue;
    if (key === "scope" && value === "all") continue;
    if (key === "page" && value === 1) continue;
    if (forRequest && (key === "group" || key === "page")) continue;
    params.set(param, String(value));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

/** Whatever is in the URL, as filters. Unknown parameters are ignored rather
 * than throwing: a link someone edited by hand should still open. */
export function queryToFilters(query: string): Filters {
  const params = new URLSearchParams(query.startsWith("?") ? query.slice(1) : query);
  const filters: Filters = { ...EMPTY_FILTERS };
  for (const [param, key] of Object.entries(PARAMS)) {
    const raw = params.get(param);
    if (raw === null) continue;
    if (BOOLEANS.includes(key)) {
      (filters[key] as boolean | null) = raw === "true" ? true : raw === "false" ? false : null;
    } else if (NUMBERS.includes(key)) {
      const parsed = Number(raw);
      (filters[key] as number | null) = Number.isFinite(parsed) ? parsed : null;
    } else if (key === "sort") {
      filters.sort = (["score", "date", "company", "title"].includes(raw) ? raw : "score") as SortKey;
    } else {
      (filters[key] as string) = raw;
    }
  }
  if (!filters.page || filters.page < 1) filters.page = 1;
  return filters;
}

/** Add or remove one value from a comma-separated filter - how the old page's
 * tickable company and city lists worked. */
export function toggleInSet(current: string | null, value: string): string | null {
  const values = (current ?? "").split(",").filter(Boolean);
  const next = values.includes(value) ? values.filter((v) => v !== value) : [...values, value];
  return next.length ? next.join(",") : null;
}

export function isInSet(current: string | null, value: string): boolean {
  return (current ?? "").split(",").filter(Boolean).includes(value);
}

/** Filter state, kept in the URL so a filtered view is a link. */
export function useFilters() {
  const [filters, setFilters] = useState<Filters>(() => queryToFilters(window.location.search));

  useEffect(() => {
    const onPop = () => setFilters(queryToFilters(window.location.search));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const update = useCallback((patch: Partial<Filters>) => {
    setFilters((current) => {
      // Any change but paging returns to page 1: staying on page 7 of a
      // different result set shows nothing and looks broken.
      const next = { ...current, ...patch, page: "page" in patch ? (patch.page ?? 1) : 1 };
      window.history.pushState(null, "", filtersToQuery(next) || window.location.pathname);
      return next;
    });
  }, []);

  const reset = useCallback(() => {
    window.history.pushState(null, "", window.location.pathname);
    setFilters({ ...EMPTY_FILTERS });
  }, []);

  const apply = useCallback((next: Filters) => {
    window.history.pushState(null, "", filtersToQuery(next) || window.location.pathname);
    setFilters(next);
  }, []);

  return { filters, update, reset, apply };
}
