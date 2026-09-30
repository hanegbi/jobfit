import type { Facets, Filters, JobDetail, JobPage, JobState } from "./types";
import { filtersToQuery } from "./useFilters";

/** Every request goes through here so a failure is one message, not a
 * silently-undefined field somewhere in a component. */
async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${path} -> ${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export function fetchJobs(filters: Filters, size = 50): Promise<JobPage> {
  const query = filtersToQuery(filters, true);
  const page = filters.page > 1 ? `&page=${filters.page}` : "";
  return get<JobPage>(`/api/jobs${query ? `${query}&` : "?"}size=${size}${page}`);
}

export function fetchFacets(filters: Filters): Promise<Facets> {
  // Facets describe the whole filtered set, so paging and sorting are dropped.
  const { page: _page, sort: _sort, ...rest } = filters;
  return get<Facets>(`/api/facets${filtersToQuery(rest as Filters, true)}`);
}

export function fetchScoredProfiles(): Promise<string[]> {
  return get<string[]>("/api/profiles/scored");
}

export function fetchJob(jobId: string): Promise<JobDetail> {
  return get<JobDetail>(`/api/jobs/${encodeURIComponent(jobId)}`);
}

export async function setJobState(jobId: string, patch: Partial<JobState>): Promise<JobState> {
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/state`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`failed to save: ${res.status}`);
  return (await res.json()) as JobState;
}
