# jobfit/scrape

Plan-driven scraping. Design rationale: @docs/superpowers/specs/2026-09-28-scrape-compute-pipeline-redesign-design.md

## The one hard rule

Runtime never calls a model. `llm_client.py` is the only module importing the Anthropic SDK, nothing imports
it at module level, and `bootstrap.build_discovery_planner` loads it lazily for `update_jobs --discover` only.
`test_scrape_no_llm_at_runtime.py` asserts a production scrape imports neither. Keep it that way.

## Flow

`service.CompanyScrapeService.scrape()` → load the company's `ScrapePlan` (`plan_store.py`, one JSON file per
company under `data/scrape_plans/`, synthesised on the fly when missing) → `factory.StrategyFactory` builds a
strategy from it → the strategy fetches → `health.HealthPolicy` records yield and the plan is written back.

- `strategies.py` — behavior, one class per plan kind (`AtsApiScrape`, `HtmlListingScrape`, `InlineJsonScrape`,
  `EmbeddedAtsScrape`, `TechmapScrape`), plus `FallbackScrape` which tries the next tier when a result isn't
  healthy. `models.py` — the plan data. Same prefix, `Scrape` suffix means behavior.
- HTML listings only: `candidates.py` turns every anchor into a `Candidate`, `filters.py` decides job or not
  (deny by default — a link no filter accepts is rejected), `titles.py` splits the title out of a card's text,
  `enrich.py` fetches the job's own page for description and the authoritative title.

## Conventions

- **A card's text is not a title.** Builders wrap title + department + city + "Full-time" + an Apply button in
  one `<a>`. `titles.split_card_text` peels off only what it recognizes and stops at the first thing it
  doesn't; `titles.authoritative_title` accepts the job page's own heading only when the card contains it, so
  the rule can trim but never rename. Widening either vocabulary risks eating real title words — add a test
  from real card text on both sides (stripped and left alone).
- **Filters read `Candidate.text` (the raw anchor text), postings use `Candidate.title`.** Don't swap them:
  the filters' length and denylist checks are tuned against raw text.
- **Adding an ATS provider** means a new `AtsClient` subclass in `ats/clients.py` registered in `AtsRegistry`,
  with its URL pattern so `resolve()` can recognize a board from any job URL. Never a per-company branch.
- Plans and their listing snapshots (`cache/listing_snapshots/`) are committed: deriving one may have cost an
  API call, and hand-written plans are legal. `test_scrape_plans_replay.py` replays every verified plan
  against its snapshot and fails by company name — that file is the regression net for any heuristic change.
- **A snapshot is someone else's page, published under our name.** `candidates.strip_non_content` drops
  scripts, styles, svg *and comments* before writing one — a page had a whole disabled `<script>` with a live
  Rollbar token in a comment, which GitHub's secret scanner then flagged on us. Anything added to that strip
  list needs a test proving the extracted candidates are unchanged, and the existing snapshots re-stripped.
- **A company's scrape may not claim another company's board.** Team8's portfolio page serves jobs for
  FlowRx, Briya and C8 Health; BlueSpine's scrape wandered onto it and filed 52 of them under BlueSpine.
  `update_jobs` drops any fetched job whose URL host is the career host of a *different* company, using
  `store.companies.exclusive_career_hosts` - which lists only hosts owned by exactly one company, so it
  never fires on boards.greenhouse.io or jobs.lever.co.
- `FetchFailed` means "nothing was fetched" and must propagate, so the caller leaves stored jobs alone. An
  empty list means "page reachable, no jobs" and may close them. Don't blur the two.
