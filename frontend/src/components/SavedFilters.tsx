import { useState } from "react";

import type { Filters } from "../types";
import { filtersToQuery, queryToFilters } from "../useFilters";

const KEY = "jobfit_saved_searches";

export interface SavedSearch {
  name: string;
  /** Stored as a query string rather than an object, so a set saved before a
   * filter existed still loads: unknown parameters are ignored, missing ones
   * fall back to their default. */
  query: string;
}

export function loadSaved(): SavedSearch[] {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (entry): entry is SavedSearch =>
        typeof entry === "object" && entry !== null &&
        typeof (entry as SavedSearch).name === "string" &&
        typeof (entry as SavedSearch).query === "string",
    );
  } catch {
    return [];
  }
}

function store(searches: SavedSearch[]): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(searches));
  } catch {
    // A private window with storage blocked still gets a working app; it just
    // cannot remember searches between visits.
  }
}

/** The old page's saved filter sets. Per-browser by design - these are a
 * shortcut, not data worth a table. */
export function SavedFilters({
  filters,
  apply,
}: {
  filters: Filters;
  apply: (filters: Filters) => void;
}) {
  const [searches, setSearches] = useState<SavedSearch[]>(loadSaved);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");

  function save() {
    const trimmed = name.trim();
    if (!trimmed) return;
    const query = filtersToQuery(filters);
    const next = [...searches.filter((s) => s.name !== trimmed), { name: trimmed, query }];
    next.sort((a, b) => a.name.localeCompare(b.name));
    setSearches(next);
    store(next);
    setName("");
    setNaming(false);
  }

  function remove(target: string) {
    const next = searches.filter((s) => s.name !== target);
    setSearches(next);
    store(next);
  }

  return (
    <div className="group saved">
      <h3>Saved searches</h3>
      {searches.map((search) => (
        <div key={search.name} className="saved-row">
          <button type="button" className="facet" onClick={() => apply(queryToFilters(search.query))}>
            <span title={search.query || "no filters"}>{search.name}</span>
          </button>
          <button type="button" className="remove" title="Forget this search" onClick={() => remove(search.name)}>
            ✕
          </button>
        </div>
      ))}
      {naming ? (
        <div className="saved-row">
          <input
            autoFocus
            type="text"
            placeholder="Name this search"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") save();
              if (e.key === "Escape") setNaming(false);
            }}
          />
          <button type="button" className="primary small" onClick={save}>
            save
          </button>
        </div>
      ) : (
        <button type="button" className="link small" onClick={() => setNaming(true)}>
          + save these filters
        </button>
      )}
    </div>
  );
}
