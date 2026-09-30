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
- **List rows never fetch descriptions.** The detail panel fetches one job on demand; that split is
  why a page of results is tens of kilobytes rather than tens of megabytes.
- Toggling a job's flag is optimistic and rolls back on failure. A like that silently fails is worse
  than one that visibly does.

## Layout

- `useFilters.ts` — filter state, and the pure `filtersToQuery` / `queryToFilters` pair it is tested
  through. Port bugs hide here, which is why it is the part with unit tests.
- `api.ts` — every request, so a failure is one message rather than an undefined field in a component.
- `components/JobList.tsx` — the virtualized list. `compact` drops the location column and the
  per-row toggles when the detail panel is open; without it the titles rendered as "S…".
- `components/Filters.tsx` — facet counts beside each option, straight from `/api/facets`.
- `components/JobDetail.tsx` — one job: description, per-profile scores, the four toggles.
