# Phase 3 (React app): the front end replaces the page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A React application that does everything `jobfit.html` does, reading the API instead of embedding 29,444 jobs.

**Architecture:** Vite + React + TypeScript in `frontend/`, built to `jobfit/server/static/app/` and served by FastAPI at `/app`. TanStack Query owns server state; filter state lives in the URL so a filtered view is a link. The result list is virtualized, because "show all matching jobs" has to stay honest at 29,000 rows.

**Tech Stack:** Node 24, Vite 7, React 19, TypeScript, TanStack Query v5, TanStack Virtual v3. No UI framework — the existing page's CSS is a good starting point and carries no dependency.

**Spec:** `docs/superpowers/specs/2026-09-30-jobfit-app-sqlite-api-react-design.md` §6

## Global Constraints

- Python tests: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests -q`. Frontend tests: `npm test` in `frontend/`.
- Every SQL statement lives in `jobfit/store/`. The API is the front end's only door to data — no new Python endpoints that bypass the store.
- `node_modules/` and `frontend/dist/` are gitignored; the built app under `jobfit/server/static/app/` **is** committed, so the server works from a fresh clone without Node.
- The old page keeps working until phase 4. This phase adds; it does not delete.
- A UI claim is not verified until it has been used in a browser. Type checks and unit tests do not establish that a filter filters.

---

### Task 1: `has_connection` — the filter the API is missing

The old page filters on "someone I know works here", and 7,146 jobs have a connection. The spec lists `has_connection=` but phase 2 skipped it, because connections live in a CSV rather than the database.

**Files:**
- Create: `jobfit/store/schema/003_company_connections.sql`
- Modify: `jobfit/store/companies.py`, `jobfit/store/search.py`, `jobfit/server/app.py`, `jobfit/scripts/update_jobs.py`
- Test: `jobfit/server/tests/test_store_companies.py`, `test_store_search.py`, `test_api_jobs.py`

**Interfaces:**
- Produces: `companies.refresh_connection_counts(conn, contacts_by_key) -> int`, `search_jobs(..., has_connection=None)`, `GET /api/jobs?has_connection=true`

- [ ] **Step 1: Write the failing tests**

```python
# jobfit/server/tests/test_store_companies.py
def test_connection_counts_are_refreshed_from_the_contacts_index():
    conn = _conn()
    companies.upsert_company(conn, "acme", "Acme Ltd.")
    companies.upsert_company(conn, "beta", "Beta")
    # keys are connections.normalize_company output
    assert companies.refresh_connection_counts(conn, {"acme": ["Jane", "Bob"]}) == 1
    assert companies.get_company(conn, "acme")["connection_count"] == 2
    assert companies.get_company(conn, "beta")["connection_count"] == 0


def test_refreshing_again_replaces_rather_than_adds():
    conn = _conn()
    companies.upsert_company(conn, "acme", "Acme Ltd.")
    companies.refresh_connection_counts(conn, {"acme": ["Jane", "Bob"]})
    companies.refresh_connection_counts(conn, {"acme": ["Jane"]})
    assert companies.get_company(conn, "acme")["connection_count"] == 1


def test_a_removed_connections_file_clears_every_count():
    conn = _conn()
    companies.upsert_company(conn, "acme", "Acme Ltd.")
    companies.refresh_connection_counts(conn, {"acme": ["Jane"]})
    companies.refresh_connection_counts(conn, {})
    assert companies.get_company(conn, "acme")["connection_count"] == 0
```

```python
# jobfit/server/tests/test_store_search.py
def test_filtering_by_whether_anyone_i_know_works_there():
    from jobfit.store import companies as store_companies

    conn = _conn()
    store_companies.refresh_connection_counts(conn, {"acme": ["Jane"]})
    assert {j["id"] for j in search.search_jobs(conn, has_connection=True)["jobs"]} == {"j1", "j2"}
    assert {j["id"] for j in search.search_jobs(conn, has_connection=False)["jobs"]} == {"j3"}


def test_rows_report_their_connection_count():
    from jobfit.store import companies as store_companies

    conn = _conn()
    store_companies.refresh_connection_counts(conn, {"acme": ["Jane", "Bob"]})
    rows = {j["id"]: j for j in search.search_jobs(conn)["jobs"]}
    assert rows["j1"]["connection_count"] == 2 and rows["j3"]["connection_count"] == 0
```

```python
# jobfit/server/tests/test_api_jobs.py
def test_jobs_can_be_filtered_by_connection(client, seeded):
    from jobfit.store import companies as store_companies

    store_companies.refresh_connection_counts(seeded, {"acme": ["Jane"]})
    body = client.get("/api/jobs?has_connection=true").json()
    assert {j["id"] for j in body["jobs"]} == {"j1", "j2"}
```

- [ ] **Step 2: Run them and watch them fail**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests/test_store_companies.py jobfit/server/tests/test_store_search.py -q`
Expected: FAIL — no such column `connection_count`, no `refresh_connection_counts`.

- [ ] **Step 3: Migration 003**

```sql
-- How many of the user's LinkedIn contacts work at this company. Derived
-- from the uploaded CSV, refreshed whenever it changes - a column so that
-- "jobs where I know someone" is a query rather than a post-filter.
ALTER TABLE companies ADD COLUMN connection_count INTEGER NOT NULL DEFAULT 0;
CREATE INDEX idx_companies_connections ON companies(connection_count) WHERE connection_count > 0;
```

- [ ] **Step 4: Implement**

In `companies.py`, add `"connection_count"` to `_FIELDS` and:

```python
def refresh_connection_counts(conn: sqlite3.Connection, contacts_by_key: dict[str, list]) -> int:
    """Set every company's contact count from the connections index. Clears
    counts not present in it, so deleting the CSV really does mean "I know
    nobody" rather than leaving stale numbers behind. Returns how many
    companies ended up with at least one."""
    from jobfit import connections as connections_module

    conn.execute("UPDATE companies SET connection_count = 0 WHERE connection_count != 0")
    touched = 0
    for row in conn.execute("SELECT id, display_name FROM companies").fetchall():
        key = connections_module.normalize_company(row["display_name"])
        count = len(contacts_by_key.get(key) or [])
        if count:
            conn.execute("UPDATE companies SET connection_count = ? WHERE id = ?", (count, row["id"]))
            touched += 1
    return touched
```

In `search.py`: add `c.connection_count` to `_LIST_COLUMNS`, and in `build_filter`:

```python
    if has_connection is not None:
        where.append("c.connection_count > 0" if has_connection else "c.connection_count = 0")
```

In `app.py`, add `has_connection: bool | None = None` to `api_jobs` and `api_facets` and pass it through.

In `update_jobs.recompute_stage`, refresh the counts before scoring, so an uploaded CSV takes effect on the next run:

```python
        counted = store_companies.refresh_connection_counts(conn, connections.load_connections_index())
        logger.info("recompute: %d company(ies) have a connection", counted)
```

- [ ] **Step 5: Run the tests, then refresh the real database**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests -q --ignore=jobfit/server/tests/test_scrape_plans_replay.py`
Then: `PYTHONPATH=. uv run python -c "from jobfit import connections; from jobfit.store import companies, db; print(companies.refresh_connection_counts(db.shared(), connections.load_connections_index()))"`
Expected: several hundred companies, and `/api/jobs?has_connection=true` returning roughly 7,000 jobs.

- [ ] **Step 6: Commit**

```bash
git add jobfit/store jobfit/server jobfit/scripts/update_jobs.py
git commit -m "feat(api): filter by whether anyone you know works there"
```

---

### Task 2: The Vite app, served by FastAPI

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/api.ts`, `frontend/src/types.ts`
- Modify: `jobfit/server/app.py`, `.gitignore`
- Test: `jobfit/server/tests/test_app_serves_the_spa.py`

**Interfaces:**
- Produces: `npm run build` → `jobfit/server/static/app/`; `GET /app` serving it; `api.ts` exporting `fetchJobs`, `fetchJob`, `setJobState`, `fetchFacets`, `fetchCompanies`

- [ ] **Step 1: Write the failing test**

```python
# jobfit/server/tests/test_app_serves_the_spa.py
"""The built front end is committed and served by FastAPI, so the app works
from a fresh clone with no Node installed."""

from jobfit.server import app as app_module


def test_the_app_route_serves_the_built_index(client):
    res = client.get("/app")
    assert res.status_code == 200
    assert "<div id=\"root\">" in res.text


def test_the_built_assets_are_served(client):
    """Vite emits hashed asset names; whatever index.html references must
    resolve, or the page loads blank with a 404 in the console."""
    import re

    index = client.get("/app").text
    for asset in re.findall(r'(?:src|href)="(/app/assets/[^"]+)"', index):
        assert client.get(asset).status_code == 200, asset


def test_a_missing_asset_is_a_404_not_the_index(client):
    assert client.get("/app/assets/nope.js").status_code == 404
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests/test_app_serves_the_spa.py -q`
Expected: FAIL — 404, no `/app` route.

- [ ] **Step 3: Scaffold**

```bash
cd frontend && npm install --no-fund --no-audit
```

`frontend/package.json` pins the versions rather than taking whatever is newest:

```json
{
  "name": "jobfit-frontend",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "test": "vitest run",
    "typecheck": "tsc --noEmit"
  },
  "dependencies": {
    "@tanstack/react-query": "^5.62.0",
    "@tanstack/react-virtual": "^3.11.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  },
  "devDependencies": {
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.3.4",
    "typescript": "^5.7.0",
    "vite": "^7.0.0",
    "vitest": "^2.1.0"
  }
}
```

`frontend/vite.config.ts` — `base` matters: assets are served under `/app/`, and getting it wrong is the classic blank page.

```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  base: "/app/",
  build: { outDir: "../jobfit/server/static/app", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8787" } },
});
```

- [ ] **Step 4: Serve it from FastAPI**

In `app.py`:

```python
APP_DIR = STATIC_DIR / "app"

if APP_DIR.exists():
    app.mount("/app/assets", StaticFiles(directory=APP_DIR / "assets"), name="app-assets")


@app.get("/app")
def spa_index() -> FileResponse:
    index = APP_DIR / "index.html"
    if not index.exists():
        raise HTTPException(404, "the front end has not been built - run `npm run build` in frontend/")
    return FileResponse(index)
```

Import `StaticFiles` from `fastapi.staticfiles`. Add `frontend/node_modules/` and `frontend/dist/` to `.gitignore`; the built `jobfit/server/static/app/` is committed on purpose.

- [ ] **Step 5: Build and run the tests**

Run: `cd frontend && npm run build` then `PYTHONPATH=. uv run python -m pytest jobfit/server/tests/test_app_serves_the_spa.py -q`
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add frontend jobfit/server .gitignore
git commit -m "feat(app): vite + react scaffold, built and served at /app"
```

---

### Task 3: The list — search, filters, sort, virtualized rows

**Files:**
- Create: `frontend/src/useJobs.ts`, `frontend/src/useFilters.ts`, `frontend/src/components/JobList.tsx`, `frontend/src/components/Filters.tsx`, `frontend/src/components/SearchBar.tsx`, `frontend/src/styles.css`
- Test: `frontend/src/useFilters.test.ts`

**Interfaces:**
- Consumes: `api.fetchJobs`, `api.fetchFacets`
- Produces: `useFilters()` returning `[filters, setFilter]` synced to `window.location.search`; `<JobList>`, `<Filters>`, `<SearchBar>`

- [ ] **Step 1: Write the failing test** — the filter/URL logic is where port bugs hide, so it is the part with unit tests

```ts
// frontend/src/useFilters.test.ts
import { describe, expect, it } from "vitest";
import { filtersToQuery, queryToFilters } from "./useFilters";

describe("filter state in the URL", () => {
  it("round-trips every filter", () => {
    const filters = { q: "mlops", city: "Tel Aviv", minScore: 60, liked: true, sort: "date" as const };
    expect(queryToFilters(filtersToQuery(filters))).toMatchObject(filters);
  });

  it("omits empty values so the URL stays readable", () => {
    expect(filtersToQuery({ q: "", city: null })).toBe("");
  });

  it("keeps a query string with special characters intact", () => {
    const query = filtersToQuery({ q: "C++ & node.js" });
    expect(queryToFilters(query).q).toBe("C++ & node.js");
  });

  it("ignores unknown parameters rather than crashing", () => {
    expect(queryToFilters("?nonsense=1&q=devops").q).toBe("devops");
  });

  it("treats a missing page as page 1", () => {
    expect(queryToFilters("?q=x").page).toBe(1);
  });
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd frontend && npm test`
Expected: FAIL — `useFilters.ts` does not exist.

- [ ] **Step 3: Implement the filter/URL module, then the components**

`useFilters.ts` exports the two pure functions above plus the hook that keeps them in `window.history`. Every filter the API accepts appears here once; the components read from the hook rather than holding their own state.

`JobList.tsx` renders rows through `@tanstack/react-virtual` so 29,000 matches cost the same as 50. Each row shows title, company, city, best score, the CV that produced it, and the four toggles. `Filters.tsx` renders the facet counts beside each option, since the API returns them for the current filter.

- [ ] **Step 4: Run the tests and the type check**

Run: `cd frontend && npm test && npm run typecheck`
Expected: pass.

- [ ] **Step 5: Use it in a browser**

Start FastAPI (`uv run uvicorn jobfit.server.app:app --port 8787`) and Vite (`cd frontend && npm run dev`), then drive the real UI: search `mlops`, set a minimum score, filter to Tel Aviv, sort by date, page forward. Confirm the counts beside each filter match what the list shows, and that the URL changes so the view can be linked.

- [ ] **Step 6: Commit**

```bash
git add frontend
git commit -m "feat(app): job list with search, filters, sort and URL-synced state"
```

---

### Task 4: The detail view and the four toggles

**Files:**
- Create: `frontend/src/components/JobDetail.tsx`, `frontend/src/useJobState.ts`
- Test: `frontend/src/useJobState.test.ts`

**Interfaces:**
- Consumes: `api.fetchJob`, `api.setJobState`
- Produces: `<JobDetail jobId>`; `useJobState(jobId)` returning `[state, toggle]` with an optimistic update

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/useJobState.test.ts
import { describe, expect, it, vi } from "vitest";
import { nextState } from "./useJobState";

describe("toggling a job's state", () => {
  it("flips only the named flag", () => {
    const current = { liked: false, hidden: false, sent: true, reached_out: false };
    expect(nextState(current, "liked")).toEqual({ ...current, liked: true });
  });

  it("flips back off", () => {
    expect(nextState({ liked: true, hidden: false, sent: false, reached_out: false }, "liked").liked).toBe(false);
  });
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd frontend && npm test`
Expected: FAIL — no `useJobState.ts`.

- [ ] **Step 3: Implement**

`useJobState` toggles optimistically through TanStack Query's `onMutate`, and rolls back on error — a like that silently fails is worse than one that visibly does. `JobDetail` fetches the job on demand, because its description is the reason list rows don't carry one.

- [ ] **Step 4: Run tests, then use it in a browser**

Run: `cd frontend && npm test && npm run typecheck`
Then in the browser: open a job, read its description, like it, reload the page and confirm it is still liked, then filter by liked and see it there.

- [ ] **Step 5: Commit**

```bash
git add frontend
git commit -m "feat(app): job detail and the liked/hidden/sent/reached toggles"
```

---

### Task 5: Build, verify against the real dataset, document

**Files:**
- Modify: `jobfit/server/CLAUDE.md`, `README.md`, `CLAUDE.md`
- Create: `frontend/CLAUDE.md`

- [ ] **Step 1: Build and run the whole Python suite**

Run: `cd frontend && npm run build && cd .. && PYTHONPATH=. uv run python -m pytest jobfit/server/tests -q`
Expected: all pass, the built app committed under `jobfit/server/static/app/`.

- [ ] **Step 2: Drive the real thing in a browser**

Against the real database (29,444 jobs): search, filter to Tel Aviv, sort by score, scroll far enough to prove virtualization, open a detail, toggle each of the four flags, reload, and confirm the state survived. Watch the network panel: a list request should be tens of kilobytes, and scrolling must not refetch the whole list.

- [ ] **Step 3: Write `frontend/CLAUDE.md`**

Under 60 lines: how to run dev and build, that the build output is committed so the server works without Node, that `base: "/app/"` is load-bearing, that filter state lives in the URL and belongs in `useFilters.ts` only, and that the list must stay virtualized.

- [ ] **Step 4: Update the root docs**

Root `CLAUDE.md`: the app is at `/app`, built from `frontend/`. `README.md`: how to open it. `jobfit/server/CLAUDE.md`: the `/app` mount.

- [ ] **Step 5: Commit**

```bash
git add frontend jobfit README.md CLAUDE.md
git commit -m "docs: the front end - how to run it, and what not to break"
```

---

## Done when

- `/app` serves a working UI from a fresh clone with no Node installed.
- Searching, filtering, sorting and paging 29,444 jobs feels instant, and a filtered view is a URL you can share.
- Liking a job survives a reload, because it is in the database rather than the browser.
- The old `jobfit.html` still works — phase 4 deletes it, not this phase.
