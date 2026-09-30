# frontend

The React application at `/app`. It reads the API; it never touches the database or embeds job data.

```
npm install
npm run dev        # Vite on :5173, proxying /api to FastAPI on :8787
npm run build      # -> ../jobfit/server/static/app/  (committed)
npm test           # vitest
npm run typecheck
```

Run FastAPI alongside `npm run dev`: `uv run uvicorn jobfit.server.app:app --port 8787`.

## Rules

- **The build output is committed** (`jobfit/server/static/app/`), so the server works from a fresh
  clone with no Node installed. Rebuild and commit it whenever you change anything here, or the app
  silently serves the previous version.
- **`base: "/app/"` in `vite.config.ts` is load-bearing.** Assets are served under that prefix; change
  it and the page loads blank with 404s for every asset. A test asserts every asset `index.html`
  references actually resolves.
- **Filter state lives in the URL**, and every filter is named once in `useFilters.ts` — the URL, the
  request and the UI all read that one map. A filter added anywhere else is half-wired by
  construction.
- **The list stays virtualized.** "Show all matching jobs" has to cost the same at 29,000 rows as at
  50. Rendering them all would reintroduce exactly the problem the API exists to solve.
- **List rows carry a bounded snippet, never the description.** `search.SNIPPET_CHARS` of it, so a
  card can show a few lines; the whole text is only ever the detail read. That cap is what keeps a
  page of results tens of kilobytes rather than tens of megabytes.
- **A job opens on its own site.** Clicking the title follows the posting's URL; there is no in-app
  detail view, because the posting is the thing you actually want to read.
- Toggling a job's flag is optimistic and rolls back on failure. A like that silently fails is worse
  than one that visibly does.

## Layout

- `useFilters.ts` — filter state, and the pure `filtersToQuery` / `queryToFilters` pair it is tested
  through. Port bugs hide here, which is why it is the part with unit tests.
- `api.ts` — every request, so a failure is one message rather than an undefined field in a component.
- `components/JobList.tsx` — the virtualized list of cards. Cards are measured after mount, because
  one with no description is shorter than one with three lines of it. `toItems` interleaves company
  headings in the order the *sort* produced, not alphabetically — grouping by company while sorted by
  score still leads with the company holding the best job. The contacts popover raises its whole
  virtual item: each card is an absolutely-positioned sibling, so a later one paints over an earlier
  one's popover.
- `components/SearchBar.tsx` — the query and the two things that modify it (scope, excluded words).
  Those are not filters and deliberately do not live in the sidebar: they change what *this* query
  means and are only reached for while typing one. Typing is debounced; `/` focuses, Esc clears.
- `components/ActiveFilters.tsx` — every active filter as a chip you can take off. With 25 controls
  down a scrolling sidebar, an unexplained "0 jobs match" is nearly always a filter set three screens
  ago; this is the answer to "why am I seeing this?".
- `components/Highlight.tsx` — marks the matched terms, split the same way `search.py` splits them,
  so what lights up is what actually matched rather than a substring of the raw query.
- `components/Filters.tsx` — facet counts beside each option, straight from `/api/facets`. Every
  yes/no filter is tri-state (`any` / `yes` / `no`), because "jobs I have NOT hidden" has to be
  askable; a plain checkbox can only say "hidden".
- `components/SavedFilters.tsx` — the old page's saved searches, stored as query strings in
  `localStorage`. A string, not an object, so a set saved before a filter existed still loads.
