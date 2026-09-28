"""Plan-driven company scraping.

Discovery (explicit `update_jobs --discover`) reads a company's career page
once - deterministic probes first, a small LLM only when the probes could
not decide - and stores a ScrapePlan. Runtime (every ordinary update) loads
that plan and executes it in pure Python: no model call anywhere on this
path. See docs/superpowers/specs/2026-09-28-scrape-compute-pipeline-redesign-design.md,
sections 2.4, 3 and 4.
"""
