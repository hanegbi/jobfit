import { useCallback, useEffect, useState } from "react";

import type { Filters, SortKey } from "./types";

export const EMPTY_FILTERS: Filters = {
  q: "",
  company: null,
  city: null,
  status: "open",
  remote: null,
  minScore: null,
  hasConnection: null,
  liked: null,
  hidden: null,
  sent: null,
  profile: "best",
  sort: "score",
  page: 1,
};

// One place naming every filter, so a new one cannot be half-wired: the URL,
// the request and the UI all read this map.
const PARAMS: Record<string, keyof Filters> = {
  q: "q",
  company: "company",
  city: "city",
  status: "status",
  remote: "remote",
  min_score: "minScore",
  has_connection: "hasConnection",
  liked: "liked",
  hidden: "hidden",
  sent: "sent",
  profile: "profile",
  sort: "sort",
  page: "page",
};
const BOOLEANS: (keyof Filters)[] = ["remote", "hasConnection", "liked", "hidden", "sent"];
const NUMBERS: (keyof Filters)[] = ["minScore", "page"];

/** The filters as a query string, omitting anything at its default so the URL
 * stays short enough to read and to share. */
export function filtersToQuery(filters: Partial<Filters>): string {
  const params = new URLSearchParams();
  for (const [param, key] of Object.entries(PARAMS)) {
    const value = filters[key];
    if (value === null || value === undefined || value === "") continue;
    if (key === "profile" && value === "best") continue;
    if (key === "sort" && value === "score") continue;
    if (key === "page" && value === 1) continue;
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
  // An explicit status=all means "including closed"; absent means the default.
  if (params.get("status") === "all") filters.status = null;
  for (const [param, key] of Object.entries(PARAMS)) {
    const raw = params.get(param);
    if (raw === null) continue;
    if (BOOLEANS.includes(key)) {
      (filters[key] as boolean | null) = raw === "true" ? true : raw === "false" ? false : null;
    } else if (NUMBERS.includes(key)) {
      const parsed = Number(raw);
      (filters[key] as number | null) = Number.isFinite(parsed) ? parsed : null;
    } else if (key === "sort") {
      filters.sort = (["score", "date", "company"].includes(raw) ? raw : "score") as SortKey;
    } else {
      (filters[key] as string) = raw;
    }
  }
  if (!filters.page || filters.page < 1) filters.page = 1;
  return filters;
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

  return { filters, update, reset };
}
