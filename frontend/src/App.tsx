import { useQuery } from "@tanstack/react-query";

import { fetchFacets, fetchJobs, fetchScoredProfiles } from "./api";
import { ActiveFilters } from "./components/ActiveFilters";
import { FiltersPanel } from "./components/Filters";
import { JobList } from "./components/JobList";
import { SearchBar } from "./components/SearchBar";
import { CardSkeleton } from "./components/Skeleton";
import { useFilters } from "./useFilters";
import { useLegacyFlags } from "./useLegacyFlags";

const PAGE_SIZE = 200;

export function App() {
  const { filters, update, reset, apply } = useFilters();
  const legacy = useLegacyFlags();

  const jobsQuery = useQuery({
    queryKey: ["jobs", filters],
    queryFn: () => fetchJobs(filters, PAGE_SIZE),
  });
  const facetsQuery = useQuery({
    queryKey: ["facets", { ...filters, page: 1 }],
    queryFn: () => fetchFacets(filters),
  });
  // The CV list changes only when a profile is added or rescored, so it is
  // fetched once rather than on every filter change.
  const profilesQuery = useQuery({ queryKey: ["scored-profiles"], queryFn: fetchScoredProfiles, staleTime: Infinity });

  const page = jobsQuery.data;
  const total = page?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const companiesShown = facetsQuery.data?.companies.length ?? 0;

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
          <ActiveFilters filters={filters} facets={facetsQuery.data} update={update} reset={reset} />
          {jobsQuery.isError && <p className="error">Could not reach the API. Is the server running?</p>}
          {jobsQuery.isLoading && <CardSkeleton />}
          {page && (
            <>
              <p className="stats">
                across <strong>{companiesShown.toLocaleString()}</strong> companies
                {total > page.jobs.length && (
                  <>
                    {" "}
                    · showing {((filters.page - 1) * PAGE_SIZE + 1).toLocaleString()} to{" "}
                    {((filters.page - 1) * PAGE_SIZE + page.jobs.length).toLocaleString()}
                  </>
                )}
              </p>
              <JobList jobs={page.jobs} group={filters.group} query={filters.q} onClearFilters={reset} />
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
            </>
          )}
        </main>
      </div>
    </div>
  );
}
