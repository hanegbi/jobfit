# jobfit/server

The control panel: a localhost-only FastAPI app with no auth, for managing personal inputs and triggering a
run. `uv run uvicorn jobfit.server.app:app --port 8787`. Design: @docs/superpowers/specs/2026-09-22-control-panel-design.md

## The whole project's test suite lives here

`jobfit/server/tests/` holds all ~52 test files for every package, not just the server — the directory name is
historical. Put new tests here regardless of what they cover.

`conftest.py` fails any test that writes to the real `companies/`, `data/jobs_v2.json`, `jobfit.html`,
`companies/_meta.json` or the aggregate cache. If a test touches those, monkeypatch the path
(`monkeypatch.setattr(update_jobs, "COMPANIES_DIR", tmp_path)`) rather than weakening the guard: it exists
because two tests once silently emptied the real page and duplicated thousands of referral jobs.

## The jobs API

The routes a front end reads, each a thin adapter over a `jobfit/store/` function:

```
GET   /api/jobs             filter/sort/paginate -> {total, page, size, jobs}
GET   /api/jobs/{id}        description, scores per profile, the user's flags
PATCH /api/jobs/{id}/state  {liked?, hidden?, sent?, reached_out?} -> the whole new state
GET   /api/facets           counts per company / city / status / department / industry / language
GET   /api/companies        every tracked company with open/total job counts
GET   /api/profiles/scored  the profile ids that have scores, for the "score against" selector
POST  /api/state/import     adopt liked/hidden/sent flags out of a browser's localStorage
```

Two rules they enforce rather than assume: **list rows never carry a `description`** (that single
field was most of the 77MB the static page shipped), and **`size` is clamped to 500** server-side,
because an unbounded page would let one request pull the dataset the API exists to avoid sending.
`/api/facets` takes the same query parameters as `/api/jobs` and shares its WHERE clause, so a count
can never disagree with the list it annotates.

## Layout

- `app.py` — routes only; uploads apply instantly, the run button is the sole thing that hits the network.
- `runner.py` — background run state and history (`data/run_history.json`); marks orphaned runs crashed at
  startup.
- `logging_stream.py` — the live log the panel streams.
- `dashboard.py` — read-only stats.
- `static/panel.html` — the whole UI, vanilla JS in one file.

## Conventions

- The app takes an instance-wide lock (`data/.server.lock`) for its entire lifetime, separate from the
  pipeline stage lock. Two server processes writing `companies/*.json` and `profiles.json` at once corrupt
  data; if startup fails on the lock, find the other process rather than deleting the file.
- Uploads (CVs, connections CSV, referral exports) land in gitignored paths under `jobfit/data/`. Never echo
  their contents into responses or logs.
- `jobfit.html` is served read-only from disk and 404s until a run has produced it. Don't generate it here —
  that belongs to `update_jobs`.
