import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { fetchFacets, fetchJobs, fetchScoredProfiles } from "./api";
import { ActiveFilters } from "./components/ActiveFilters";
import { FiltersPanel } from "./components/Filters";
import { JobList } from "./components/JobList";
import { SearchBar } from "./components/SearchBar";
import { CardSkeleton } from "./components/Skeleton";
import { toggleInSet, useFilters } from "./useFilters";
import { useLegacyFlags } from "./useLegacyFlags";

// 200 per page, Dan's call over the 50 this used to be: he scrolls a long
// list rather than paging through it. The list is virtualized, so the rows
// cost nothing to render; what a bigger page does cost is payload, and
// measured against the real store that is 228KB against 51KB for 40ms more
// (224ms -> 264ms on localhost, where the search is debounced anyway).
const PAGE_SIZE = 200;

export function App() {
  const { filters, update, reset, apply } = useFilters();
  const legacy = useLegacyFlags();

  const client = useQueryClient();

  // placeholderData is what makes typing feel immediate: the previous results
  // stay on screen, dimmed, while the next ones load. Without it every
  // keystroke unmounts the list and flashes skeletons, which reads as slower
  // than it is even when the request takes 200ms.
  const jobsQuery = useQuery({
    queryKey: ["jobs", filters],
    queryFn: () => fetchJobs(filters, PAGE_SIZE),
    placeholderData: keepPreviousData,
  });
  const facetsQuery = useQuery({
    queryKey: ["facets", { ...filters, page: 1 }],
    queryFn: () => fetchFacets(filters),
    placeholderData: keepPreviousData,
  });
  // The CV list changes only when a profile is added or rescored, so it is
  // fetched once rather than on every filter change.
  const profilesQuery = useQuery({ queryKey: ["scored-profiles"], queryFn: fetchScoredProfiles, staleTime: Infinity });

  const page = jobsQuery.data;
  const total = page?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  // The real count, not the length of the capped list the sidebar picks from.
  const companyCount = facetsQuery.data?.totals.companies ?? 0;

  // Every company id -> name this session has ever seen, not just this
  // query's own facets response - an excluded company has 0 matching jobs
  // by construction, so it drops out of the very next facets response,
  // which would otherwise make its own "Excluding: ..." chip regress to
  // showing the raw id the moment it takes effect.
  const companyNamesRef = useRef(new Map<string, string>());
  if (facetsQuery.data) {
    for (const company of facetsQuery.data.companies) companyNamesRef.current.set(company.id, company.name);
  }

  // Fetch the next page while the user reads this one, so "next" is instant.
  useEffect(() => {
    if (filters.page >= pageCount) return;
    const next = { ...filters, page: filters.page + 1 };
    client.prefetchQuery({ queryKey: ["jobs", next], queryFn: () => fetchJobs(next, PAGE_SIZE) });
  }, [client, filters, pageCount]);

  return (
    <div className="app">
      <header className="top">
        <h1>jobfit</h1>
        <SearchBar filters={filters} total={total} loading={jobsQuery.isFetching} update={update} />
        <a className="panel-link" href="/">
          control panel
        </a>
      </header>

      {legacy.found && (
        <div className="banner">
          <span>
            This browser still holds <strong>{legacy.found.count}</strong> liked/hidden/sent flags from the
            old page. They only exist here until you move them into the database.
          </span>
          <button type="button" className="primary" onClick={legacy.importThem}>
            Import them
          </button>
          <button type="button" onClick={legacy.dismiss}>
            Not now
          </button>
        </div>
      )}
      {legacy.imported !== null && (
        <div className="banner ok">
          Imported {legacy.imported} flag{legacy.imported === 1 ? "" : "s"} from this browser. They live in
          the database now, so they survive a reload and are the same from any browser here.
        </div>
      )}

      <div className="body">
        <FiltersPanel
          filters={filters}
          facets={facetsQuery.data}
          profiles={profilesQuery.data ?? []}
          update={update}
          apply={apply}
        />

        <main>
          <ActiveFilters filters={filters} companyNames={companyNamesRef.current} update={update} reset={reset} />
          {jobsQuery.isError && <p className="error">Could not reach the API. Is the server running?</p>}
          {jobsQuery.isPending && <CardSkeleton />}
          {page && (
            <div className={`results${jobsQuery.isPlaceholderData ? " stale" : ""}`}>
              <p className="stats">
                across <strong>{companyCount.toLocaleString()}</strong> companies
                {total > page.jobs.length && (
                  <>
                    {" "}
                    · showing {((filters.page - 1) * PAGE_SIZE + 1).toLocaleString()} to{" "}
                    {((filters.page - 1) * PAGE_SIZE + page.jobs.length).toLocaleString()}
                  </>
                )}
              </p>
              <JobList
                jobs={page.jobs}
                group={filters.group}
                query={filters.q}
                onClearFilters={reset}
                onExcludeCompany={(companyId, companyName) => {
                  companyNamesRef.current.set(companyId, companyName);
                  update({ excludeCompany: toggleInSet(filters.excludeCompany, companyId) });
                }}
              />
              {pageCount > 1 && (
                <nav className="paging">
                  <button type="button" disabled={filters.page <= 1} onClick={() => update({ page: filters.page - 1 })}>
                    ← previous
                  </button>
                  <span>
                    page {filters.page} of {pageCount.toLocaleString()}
                  </span>
                  <button
                    type="button"
                    disabled={filters.page >= pageCount}
                    onClick={() => update({ page: filters.page + 1 })}
                  >
                    next →
                  </button>
                </nav>
              )}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
