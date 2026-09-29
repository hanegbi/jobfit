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
