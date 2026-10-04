# jobfit_agent

A LangGraph agent on top of jobfit. It takes jobs jobfit has already scraped and scored, then for each
one: judges the fit against your CV, researches the company (size, funding and exit outlook, reviews,
salary, interview process), plans CV edits and has a critic check them, and writes one self-contained
`report.html` — a tab per company, a card per job.

The scraper stays model-free. `jobfit` never imports this package, and a test enforces that.

## Run

```
ollama pull qwen3:4b                                   # once

PYTHONPATH=. uv run --project jobfit_agent python -m jobfit_agent.cli --url <job url> --open
PYTHONPATH=. uv run --project jobfit_agent python -m jobfit_agent.cli --top 5
```

Reports land in `jobfit_agent/out/<timestamp>/` as `report.html` plus the `report.json` it renders from.

| Flag | What it does |
|---|---|
| `--url <url>` | one job, by its posting url, instead of the top-scored ones |
| `--top N` | how many top-scored open jobs (default 5) |
| `--profile ID` | CV profile from jobfit's `profiles.json` (default `default`) |
| `--skip-research` | fit and CV plan only, no web research — much faster |
| `--refresh` | ignore the 14-day company research cache |
| `--ask` | pause for approval before the report (off by default: read first, decide after) |
| `--resume THREAD` | continue a stopped run |
| `--open` | open the report when it is done |

## Models

`agent/config.py` maps each node to `provider:model`. `ollama:` is local and free, `anthropic:` is paid
and needs `ANTHROPIC_API_KEY`. Everything defaults to `qwen3:4b` because this machine has no GPU.

`REASONING = False` turns off qwen3's thinking step: measured at 20s versus 76s for one fit call. Turn it
on when the judgement matters more than the wait.

## What it will not do

- Invent facts. A research topic that cannot cite a page it actually fetched reports "no data", and the
  exit outlook is a qualitative view with its evidence, never a made-up probability.
- Invent experience. Every CV edit quotes a line of your CV, or is flagged `verify first`.
- Write to `jobfit.db`. The store connection is opened `query_only`.
- Trust the web. Fetched page text is fenced as data in the prompt, and the report builds its DOM from
  text nodes, so a hostile page cannot run anything.

## Test

```
PYTHONPATH=. uv run --project jobfit_agent python -m pytest jobfit_agent/tests -q
```

54 tests, about 2 seconds. They use a fake model, so they need no Ollama, no network and no real database.
`python -m jobfit_agent.tests.sample <dir>` renders a sample report for eyeballing the page.
