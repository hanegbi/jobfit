import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { fetchFacets, fetchJobs } from "./api";
import { FiltersPanel } from "./components/Filters";
import { JobDetail } from "./components/JobDetail";
import { JobList } from "./components/JobList";
import { useFilters } from "./useFilters";

const PAGE_SIZE = 200;

export function App() {
  const { filters, update, reset } = useFilters();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const jobsQuery = useQuery({
    queryKey: ["jobs", filters],
    queryFn: () => fetchJobs(filters, PAGE_SIZE),
  });
  const facetsQuery = useQuery({
    queryKey: ["facets", { ...filters, page: 1 }],
    queryFn: () => fetchFacets(filters),
  });

  const page = jobsQuery.data;
  const total = page?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="app">
      <header className="top">
        <h1>jobfit</h1>
        <input
          className="search"
          type="search"
          placeholder="Search titles and descriptions…"
          defaultValue={filters.q}
          onKeyDown={(event) => {
            if (event.key === "Enter") update({ q: (event.target as HTMLInputElement).value });
          }}
          onBlur={(event) => {
            if (event.target.value !== filters.q) update({ q: event.target.value });
          }}
        />
        <a className="panel-link" href="/">
          control panel
        </a>
      </header>

      <div className="body">
        <FiltersPanel
          filters={filters}
          facets={facetsQuery.data}
          total={total}
          update={update}
          reset={reset}
        />

        <main>
          {jobsQuery.isError && <p className="error">Could not reach the API. Is the server running?</p>}
          {jobsQuery.isLoading && <p className="muted">Loading…</p>}
          {page && (
            <>
              <JobList
                jobs={page.jobs}
                selectedId={selectedId}
                onSelect={setSelectedId}
                compact={selectedId !== null}
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
            </>
          )}
        </main>

        {selectedId && <JobDetail jobId={selectedId} onClose={() => setSelectedId(null)} />}
      </div>
    </div>
  );
}
