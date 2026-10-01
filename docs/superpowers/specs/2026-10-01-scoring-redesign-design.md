# Scoring redesign: role-family gating + IDF skill weights

**Status:** proposed, awaiting approval. No code changed yet.
**Analyzed against:** `jobfit/data/jobfit.db` as of 2026-10-01, 12,823 active jobs, 10,574 with a real (>100 char) description, 29,444 jobs ever recorded.

## Operating principle: every decision is a command's output, not mine

Revised per feedback. Categorization, IDF, profile-building and scoring are Python modules, run from the CLI, same output on any machine — I do not classify a job, weigh a skill, or score anything by reading the data myself. Every "report X" below names the exact command whose printed output *is* X. The **only** manual step anywhere in this design is Dan labeling the evaluation set (§6) — that labeling is the ground truth the tuning script optimizes against, not a judgment I make.

This applies retroactively to three places the first draft was looser than that:

- **Family adjacency** (§1) is no longer "a table I propose" — it's computed by a CLI command from the corpus (cosine similarity of each family's IDF-weighted skill-presence vector), with a small, explicit, versioned override block for cases the computed value gets wrong on sparse data. The override block is authored like any other data-file edit (reviewed, committed) — it is not me re-deciding a score.
- **Formula weight tuning** (§5) is no longer "I'll propose a first pass and iterate by eye" — it's a search script that fits the weights against Dan's labeled set under the stated hard constraints and prints the winning configuration. I pick the search space and the objective; the script picks the numbers.
- **The Phase 0 before/after checkpoint** (§7) is no longer "I'll show you" — it's a comparison command that scores the same jobs under the old and new formula and prints the diff.

One thing stays as it was and should: §0 below is a *diagnostic investigation* I already did to find out why the current scorer behaves as it does (reading sampled scores, sub-scores and CV output to trace two specific code bugs to their exact lines). That already happened, it's how the two bugs got found, and it's not part of the ongoing pipeline — it doesn't recur, and nothing downstream depends on my having "judged" those jobs. Everything from §1 onward is the operational design, and that part is 100% the commands described in this revision.

## 0. Root cause (read this first — it changes the plan)

Before designing the family/IDF layer, I traced *why* the current scorer produces what it produces. The user's diagnosis ("keyword overlap drowns out rare skills") is a correct description of the symptom, but the actual mechanism is narrower and more severe: **for most job/CV pairs, 4 of the 5 sub-scores collapse to near-identical constants**, driven by two independent extraction bugs — one on the CV side, one on the JD side. The family/IDF redesign is still the right direction, but it will not produce correct scores until these two bugs are fixed, because the new design's inputs (the candidate's role-family vector, and which skills a JD actually asks for) come from the same two extraction paths.

### 0.1 CV-side bug: title/company are swapped for a common resume layout

`jobfit/ats_scorer/cv_extractor.py:_infer_title_for_role` (lines 100–128) handles two resume layouts:

- `Title | Company | Dates` on one line → splits correctly.
- `Company` on its own line, then `Title    Dates` on the next line (the second-most-common layout, and the one Dan's CV uses) → **the function returns `(prev_line, remainder)` as `(title, company)`, but for this layout `prev_line` is the company and `remainder` is the title. The assignment is backwards.**

Verified on the live profile:

```
Role(title='Hailo', family=None)                      # should be title='Software Engineer', company='Hailo'
Role(title='Cyber Security Company', family=None)      # should be title='Automation Developer', ...
Role(title='Software Quality Engineer', family=None)   # title right, but...
Role(title='B.Sc. Computer Science ...', family=None)  # an EDUCATION line, pulled in as a role
Role(title='McAfee', family=None)                      # should be title='Software Quality Engineer', ...
Role(title='Unit 8200, IDF Intelligence Corps', family=None)
```

Every one of 6 roles got `family=None`. This is not CV-specific — any CV using "Company on its own line" will hit it. It cascades hard:

- `cv_extractor._parse_roles` line 165 calls `role_families.classify(entry["title"], ...)` on a company name → never finds a family → `role.family = None` for every role.
- `matcher.recent_role_family(profile)` (cv_extractor.py:295) just reads `role.family` off the first two roles → returns `None`.
- `matcher.match()` line 196: `role_family_match = bool(job.role_family) and family == job.role_family` → **always `False`**, for every job, regardless of actual fit.
- `scorer._title_and_seniority_fit_score` (scorer.py:70-76): `role_family_match=False` → flat **`100 - 40 = 60`** for every job. Confirmed: all 6 sampled top-scoring jobs (Data Engineer, Full Stack, Backend, Data Scientist, Security Researcher, Big Data Engineer) scored **exactly 60.0** on this sub-score.
- `scorer._apply_gates` line 164: `recent_families = {r.family for r in profile.roles[:2] if r.family}` → **empty set** → the existing `role_family_mismatch_cap: 40` gate's condition (`job.role_family and recent_families and ...`) is `True and {} and ...` → **never evaluates True, so the gate that already exists in the codebase for exactly this problem never fires.** This is the single biggest lever: restoring this gate alone (by fixing the CV parse) caps all 35 of the top-100's off-family jobs at 40, which already solves most of what was reported.
- `matcher._recent_relevant_fraction` also degrades: with `role.family` always `None`, it falls through to a noisy single-word substring check (`job.domain.lower() in role_text`), landing on a muddy, not-meaningfully-discriminating `experience_relevance` score (50.0 across the sampled jobs, though this one is more coincidental than a hard constant — it depends on which generic domain words happen to appear in Dan's bullets).

**Fix scope (Step 5 prerequisite, not part of this redesign's new logic):** correct the title/company assignment for the "Company\nTitle Dates" layout in `_infer_title_for_role`. This is a small, mechanical, high-leverage bugfix — not a redesign — and it must land before Step 5 (candidate profile) can produce a real affinity vector for *any* candidate, not just Dan's. I'd fix and verify this as Step 5.0, with a regression test built from Dan's actual CV text plus 2-3 synthetic layouts.

### 0.2 JD-side bug: the requirement-section header vocabulary is too narrow

`jobfit/ats_scorer/jd_extractor.py:_HEADER_RE` (lines 42-50) only recognizes a fixed list of header phrases ("Requirements", "Qualifications", "What you'll need", "What we're looking for", "Responsibilities", "What you'll do", ...). Measured on a random sample of **600 active jobs with a real description (>200 chars)**:

| Description length | Sample | Zero must_have **and** zero nice_to_have |
|---|---|---|
| truncated at exactly 6000 chars | 300 | 76% |
| untruncated, 1000–5999 chars (a normal full JD) | 300 | **62%** |
| untruncated, 200–999 chars | 300 | 99% |

The 62% figure on normal-length, untruncated, well-formed JDs is the real finding: this isn't about short or truncated text, the header vocabulary itself is missing extremely common real-world phrasings. Concrete case (ScaleOps "Sales Engineer, Lead", 2797 chars, genuinely well-structured):

```
... What You'll Be Doing  Engage with customers ... What You'll Bring
5+ years of hands-on experience with cloud-native technologies such as
containers and Kubernetes (required). Minimum five years of experience
in customer-facing roles such as technical sales ...
```

Both headers are missed: `_RESPONSIBILITY_HEADER_WORDS` has `"what you'll do"` but not `"what you'll be doing"`; `_HEADER_RE`'s must-have list has no `"what you'll bring"` / `"what you bring"` at all — despite this being one of the most common requirements-section headers in real postings. Result: a JD with a clearly-labeled, clearly-written requirements section (`"5+ years ... Kubernetes (required)"`) yields **zero extracted must-haves**, and `job_evidence` confirms the scrape-time heuristic agrees (`"requirement_sections": 0`).

Separately, truncation at exactly 6000 characters (`jobfit/ats_fetchers.py`, 7 call sites) also matters — 22% of described jobs hit this ceiling, and the requirements section is disproportionately likely to be near the end of a JD (after the "about us" / "the role" narrative), so truncation preferentially cuts the one section the scorer needs most. Secondary effect, real, but smaller than the header-vocabulary gap.

Also found, same sample: several "descriptions" are not job postings at all — full marketing/product pages scraped as if they were a JD (e.g. a company's product-tour page, nav menu and all, stored as the description for a job titled "Pathfinder AI"). This is the same class of problem as the "About Us" / nav-junk title cleanup already done elsewhere in this session, just on the description field instead of the title field. I'm flagging it, not fixing it here — it's a scrape-quality issue, independent of the scoring redesign, and `_looks_unparseable`'s evidence gate is the right place to harden it (it already has the right shape: `jsonld_jobposting`, `apply_cta`, `requirement_sections`, `role_family_from_title` — this case had none of them and `job_evidence` was entirely absent, meaning it went through a scrape path that never recorded evidence at all).

**Fix scope (Step 6 prerequisite):** widen `_HEADER_RE`'s must-have vocabulary (add "what you'll bring", "you bring", "you have", "who you are", "about you", "must have(s)", and matching responsibility-header variants), and raise or remove the 6000-char truncation for the text the extractor sees (store full text, truncate only what's *displayed*, if storage size is the actual concern — I'll confirm with you). This is also a bugfix, not new logic, but it directly gates whether Step 6's IDF-weighted job_fit has anything to weight.

### 0.3 Net effect on the current top 100

Classifying the current top-100 scored jobs by **title alone**, using the taxonomy that already exists in the repo (`role_families.json`, unmodified) against Dan's declared target families (backend, devops, ml_infra, ml_engineering; fullstack counted separately as borderline):

| | count |
|---|---:|
| fit (backend / devops / ml_infra / ml_engineering) | 56 |
| borderline (fullstack, 9× "Senior Full Stack Engineer/Developer") | 9 |
| **not fit** | **35** |

The 35 not-fit break down as: data_engineering 9, security 8, data_science 6, data_analytics 3, sales 3, qa 2, support 1, product 1, frontend 1 (one title didn't resolve cleanly). This matches the four examples in the report (Data Scientist, Senior Product Security Researcher, Senior Big Data Engineer, Senior Full Stack Engineer) exactly.

**Which sub-score let them through:** all of them, the same way. `must_have_coverage` (40% weight) is `100` with `matched=[]` on 97 of the top 100 — not because skills matched, but because `must_have_coverage`'s own empty-input default (`scorer.py:_coverage_score`, "no requirements to cover at all" → 100) fires when JD extraction (§0.2) finds nothing. `title_and_seniority_fit` (20% weight) is flat `60` for every job regardless of actual family, because the gate that should discriminate this is dead (§0.1). `experience_relevance` (20% weight) is a noisy ~50. `nice_to_have_coverage` (10%) is the same empty-default 100. Only `evidence_depth` (10% weight) carries real, job-independent signal — which is far too little weight to produce a discriminating ranking, and explains why the top 100 is a near-flat plateau at 74/72 with an ordering that doesn't track fit at all.

### 0.4 Score distribution (10-point buckets, active jobs, `default` profile)

```
  0- 9  8,542  ############################################################   (66%, mostly correct: 8,462 are title-only/no-description jobs correctly scoring near 0; real non-tech jobs like "Payroll Accountant" also correctly land here)
 10-19    720  #####
 20-29    745  #####
 30-39    160  #
 40-49    257  ##
 50-59  1,793  #############
 60-69    509  ####
 70-79     97  #
 80-89      0
 90-99      0
```

Two findings: the max of 74 is a real ceiling, not a display/rounding artifact — `evidence_depth` tops out around 24 (its own 150%-scaled formula caps at 100, but Dan's CV only hits ~24 on it) and that's the only sub-score with headroom once the other four are pinned near their defaults, so `40 + 12 + 10 + 10 + 2.4 ≈ 74` is close to the ceiling the current formula can produce for *any* job once the two bugs are in play. The 0-9 bucket is **not** the problem — it's doing its job (rejecting title-only listings and genuinely unrelated roles). The entire fight is in the 50-74 band, where 2,399 jobs sit on a nearly-flat plateau that the current formula cannot tell apart.

### 0.5 Skill prevalence (IDF), computed on the real corpus

10,574 active jobs with a real description. Methodology: for each of the 226 taxonomy skills, % of JDs (title+description) whose text matches any of its aliases.

**Top 30 by prevalence (lowest signal value):**

| % of JDs | IDF | skill |
|---:|---:|---|
| 24.8% | 1.39 | Compliance |
| 13.1% | 2.03 | Large Language Models |
| 11.1% | 2.20 | AWS |
| 10.7% | 2.24 | Python |
| 10.6% | 2.25 | Machine Learning |
| 8.1% | 2.51 | Hiring |
| 7.8% | 2.55 | Cross-Functional Collaboration |
| 7.7% | 2.56 | Kubernetes |
| 7.7% | 2.57 | CRM Software |
| 7.0% | 2.66 | Microsoft Azure |
| 6.8% | 2.69 | Google Cloud |
| 5.9% | 2.83 | CI/CD |
| 5.3% | 2.93 | Salesforce |
| 5.2% | 2.95 | SQL |
| 5.1% | 2.99 | Cloud Security |
| 5.0% | 2.99 | Agile/Scrum |
| 5.0% | 3.00 | Business Intelligence |
| 4.8% | 3.04 | SOC Operations |
| 4.5% | 3.10 | Supply Chain Management |
| 4.4% | 3.12 | Linux Administration |
| 4.3% | 3.15 | Docker |
| 4.2% | 3.16 | JavaScript |
| 4.2% | 3.18 | Excel |
| 4.0% | 3.22 | Go-to-Market Strategy |
| 4.0% | 3.23 | Cloud Architecture |
| 4.0% | 3.23 | Zero Trust Architecture |
| 3.7% | 3.29 | Incident Response |
| 3.4% | 3.39 | Product Management |
| 3.4% | 3.39 | Forecasting |
| 3.3% | 3.40 | ETL Pipelines |

**Bottom 30 by prevalence with ≥5 mentions (highest signal value):**

| % of JDs | IDF | skill (n) |
|---:|---:|---|
| 0.05% | 7.66 | Tailwind CSS (5) |
| 0.05% | 7.66 | Nginx (5) |
| 0.06% | 7.47 | Recommender Systems (6) |
| 0.06% | 7.47 | Flutter (6) |
| 0.07% | 7.32 | C (7) |
| 0.07% | 7.32 | Cassandra (7) |
| 0.07% | 7.32 | AWS Lambda (7) |
| 0.07% | 7.32 | Jest (7) |
| 0.07% | 7.32 | Product Roadmapping (7) |
| 0.08% | 7.19 | React Native (8) |
| 0.09% | 7.07 | Pytest (9) |
| 0.09% | 7.07 | Product Requirements Documents (9) |
| 0.09% | 6.96 | Oracle Database (10) |
| 0.09% | 6.96 | Puppet (10) |
| 0.10% | 6.87 | Django (11) |
| 0.10% | 6.87 | Flask (11) |
| 0.11% | 6.78 | Objective-C (12) |
| 0.11% | 6.78 | Hadoop (12) |
| 0.11% | 6.78 | Cypress (12) |
| 0.11% | 6.78 | Financial Modeling (12) |
| 0.12% | 6.70 | Database Design (13) |
| 0.13% | 6.63 | Model Quantization (14) |
| 0.13% | 6.63 | Kanban (14) |
| 0.15% | 6.49 | Selenium (16) |
| 0.16% | 6.43 | Redux (17) |
| 0.16% | 6.43 | Istio (17) |
| 0.17% | 6.38 | Contract Negotiation (18) |
| 0.18% | 6.32 | Manual Testing (19) |
| 0.19% | 6.27 | Copywriting (20) |
| 0.20% | 6.22 | GPU Programming (21) |

**Important gap found:** of the skills the user named as *their own* rare signature ("model inference, model evaluation, accelerators, distributed inference, ONNX, Triton, benchmarking frameworks"), only **"Model Quantization"** and **"GPU Programming"** exist in the current 226-skill taxonomy. **"Model Inference," "Observability," "GenAI," and "LLM"** (as a distinct alias from "Large Language Models") are **not in the taxonomy at all** — IDF of an absent skill is undefined, not automatically high. Step 4 of the build must include expanding `skills_taxonomy.json` with this vocabulary *before* computing production IDF weights, or the signature-detection in Step 5 will have nothing to find.

Checked for the record: Python 10.7% (idf 2.24), AWS 11.1% (2.20), Kubernetes 7.7% (2.56), Docker 4.3% (3.15), Elasticsearch 0.8% (4.80), Jenkins 1.0% (4.65), MLOps 1.2% (4.39). Only 2 of 226 taxonomy skills (`R`, `Vagrant`) never appear in the corpus at all.

### 0.6 Title normalization

Normalized 12,823 active titles (lowercase, Hebrew stripped, seniority/level words stripped, common abbreviations expanded) → **8,824 unique normalized titles**. Top 10: product manager (119), software engineer (109), devops engineer (78), backend engineer (78), [nav-junk: "generative ai flexible hours" 61 — a company's own recurring career-fair/event posting, real but oddly phrased], account executive (60), fullstack engineer (57), customer success manager (50), [nav-junk: "partners" 49], data scientist (45).

Side finding, flagged but out of scope here: the normalized top-100 surfaces a second class of scrape junk beyond the nav-link titles already fixed this session — marketing CTA text stored as job titles ("Book a Demo" 41, "View Role" 39, "View Job" 34, "Solutions" 31, "More Info" 30, "Webinars" 22, "Glossary" 16, "Trust Center" 14, "Customer Stories" 13, "Get in Touch" 13, "Request a Demo" 12). These will pollute both the family classifier (title-based rules have nothing to match, correctly landing in the review bucket) and, if they ever get real description text attached, the IDF corpus. The review-bucket design in Step 2 absorbs these safely (they'll have no title-rule match and weak/no JD signal → `unknown`, not silently mis-filed) — but if you want, this is a good follow-up cleanup in the same vein as the earlier `looks_like_site_furniture` work.

### 0.7 Existing taxonomy coverage

`jobfit/ats_scorer/taxonomy.py`'s `RoleFamilies.classify()`, run **title-only** (no JD fallback — that doesn't exist yet) over all 12,823 active titles: **27% classified, 73% unclassified.** The single biggest unclassified title is literally `"Senior Software Engineer"` (43) + `"Software Engineer"` (37) = 80 jobs — the most common real tech title in the corpus has no match because every family's keyword list is specific (backend's aliases are `backend`, `back-end`, `server-side`, ... — there's no generic catch-all, by design, since "Software Engineer" alone can't tell you backend from frontend from ML from embedded). **This is exactly the scenario the user's own request anticipated** ("fallback to JD responsibilities keywords when the title is generic") — real data confirms the JD-fallback path isn't optional, it's required for the single largest title bucket in the corpus.

---

## 1. Proposed two-level taxonomy

`role_families.json` (23 families, already in this codebase) is itself a precedent for how this taxonomy is meant to be authored: it's a curated keyword-list data file, not a computed artifact, and it's edited like code — committed, reviewed, versioned. The new 25-family version follows the same process, made reproducible rather than eyeballed: a script (`uv run python -m jobfit.scripts.title_frequency`, same normalization logic as §0.6) prints the normalized-title frequency table a human edits into keyword rules, so every proposed family and split below is backed by a number that script printed, not by my reading the job list. The two splits below are what that table's real counts support:

**Security** (150 jobs, currently one family) → split into `security_engineering` and `security_research`. Evidence: 74 titles contain "security researcher", 56 contain "security engineer", 18 "application security", 11 "product security", 14 "vulnerability research" — two genuinely distinct, high-volume buckets, and the exact pair the user's bad-example #2 falls into.

**DevOps** (204 jobs, currently one family covering devops/sre/platform/infra/cloud) → split into `devops_sre` and `infrastructure_platform`. Evidence: 173 "devops", 42 "platform engineer", 27 "infrastructure engineer", 19 "site reliability"/18 "SRE", 5 "cloud engineer" — enough volume on each side, though these two will need a high partial-affinity to each other (0.7+) since the title boundary between them is genuinely fuzzy in real postings (a "DevOps Engineer" posting often *is* platform work). I'd rather split-with-high-affinity than merge-and-lose-information, since the user explicitly listed Infrastructure/Platform and DevOps/SRE as separate target families.

### Level 1 families (25, with real job counts from title-only classification)

| family | n (title-only) | example real titles |
|---|---:|---|
| `software_engineering` (generic — see JD-fallback below) | 80+ | Senior Software Engineer, Software Engineer |
| `backend` | 258 | Senior Backend Engineer, Backend Engineer, Senior Backend Developer |
| `frontend` | 46 | Senior Frontend Engineer, Frontend Engineer |
| `fullstack` | 178 | Senior Full Stack Engineer, Full Stack Developer |
| `mobile` | 8 | Android Developer, Senior Mobile Engineer (iOS/Android) |
| `data_engineering` | 67 | Senior Data Engineer, Data Engineer |
| `data_science` | 108 | Data Scientist, Senior Data Scientist, AI Research Scientist |
| `data_analytics` | 52 | Data Analyst, Business Analyst |
| `ml_engineering` (AI/ML Engineer) | 108 | AI Engineer, Senior AI Engineer, Machine Learning Engineer |
| `ml_infra` (MLOps/ML Infra) | 11† | MLOps Engineer, Senior AI Infrastructure Engineer |
| `devops_sre` | ~160† | DevOps Engineer, Senior DevOps Engineer, Site Reliability Engineer |
| `infrastructure_platform` | ~70† | Senior Platform Engineer, Infrastructure Engineer, Cloud Engineer |
| `security_engineering` | ~70† | Security Engineer, Application Security Engineer, Cloud Security Engineer |
| `security_research` | ~80† | Security Researcher, Senior Security Researcher, Vulnerability Researcher |
| `qa_automation` | 78 | QA Engineer, Automation Engineer, QA Automation Engineer |
| `embedded_firmware` | 58 | Hardware Engineer, Embedded Software Engineer |
| `product` | 283 | Product Manager, Technical Product Manager |
| `design` | 49 | Product Designer, UI/UX Designer |
| `management` | 203 | Engineering Manager, DevOps Team Lead |
| `sales` | 703 | Account Executive, Sales Engineer |
| `support` | 258 | Customer Success Manager, Technical Support Engineer |
| `marketing` | 147 | Marketing Manager, Product Marketing Manager |
| `finance` | 136 | Controller, Bookkeeper |
| `hr` | 52 | Talent Acquisition Partner |
| `legal` | 22 | Legal Counsel |
| `operations` | 224 | Project Manager, Marketing Operations Manager |

† ml_infra, devops_sre/infrastructure_platform, security_engineering/security_research counts are estimated by splitting the current merged `ml_infra`/`devops`/`security` buckets along the keyword evidence in §1. Exact counts are not something I'll report — they're the printed output of `uv run python -m jobfit.scripts.normalize_text --families --report` (§2), run once the classifier exists and again after backfill.

**Not real roles — stays `unknown`:** the nav-junk titles in §0.6 (Book a Demo, View Role, Solutions, Webinars, Glossary, ...). They have no title-rule match and (being marketing CTAs) will usually have no technical JD content either, so they land in the review bucket by construction rather than by a special-case rule.

### Level 2 (canonical title within a family)

Per your spec: e.g. `backend` → {Backend Engineer, Software Engineer (backend-leaning, JD-confirmed), Server Engineer, API Engineer}. Same authoring process as Level 1: a second lookup keyed by family, same alias-list shape `skills_taxonomy.json` already uses, populated from the same frequency-table script's output grouped by family rather than typed from memory.

### Mapping rules

1. **Title keyword rules first** (the existing `RoleFamilies.classify()` mechanism, extended with the splits above).
2. **JD fallback when the title is generic** (`"software engineer"`, `"engineer"`, `"developer"`, and similar bare titles) — classify from the JD's own responsibility-section keywords (the same text `_split_sections`'s `"responsibility"` bucket already extracts, once §0.2's header-vocabulary fix is in). This is **required**, not optional — it's the only path for the single largest unclassified title bucket (§0.7).
3. **Confidence**: `"title"` when a title rule matched directly, `"jd_fallback"` when only the JD fallback matched, `"unknown"` when neither did.
4. **`unknown` is terminal, never silently defaulted into a technical family** — exactly as you specified. An `unknown` job is excluded from family-fit gating (treated as affinity 0.5 — neither helped nor hurt) rather than guessed at, and shows up in the review-bucket report for a human to look at (or feed back into the taxonomy as new keywords).

### Adjacency (partial affinity) — computed, not authored

A `(family_a, family_b) → affinity [0,1]` table, symmetric, but not hand-typed by me: `uv run python -m jobfit.ats_scorer.cli compute-adjacency` computes it from the corpus and writes `family_adjacency.json`. Method: once every job has a stored `family` (§2), build each family's skill-presence vector (which of the 226+ taxonomy skills appear in that family's JDs, weighted by `skill_idf.json` from §4 — so a skill common to everyone, like Python, barely moves the similarity, and a skill only two adjacent families share moves it a lot), then `affinity(A, B) = cosine_similarity(vector_A, vector_B)`. Same family is defined as 1.0, not computed. This is deterministic and rerunnable — the command prints the full matrix, and running it twice on the same DB gives the same numbers.

Two things that still need a human, scoped narrowly and done as an authored data-file edit (not a live judgment call):

- **A floor**, so two families with almost no shared vocabulary don't round to 0 (the spec calls for a floor, not for "total unfamiliarity is impossible" to be my opinion) — I'd set this as one named constant in the script, e.g. `AFFINITY_FLOOR = 0.1`, applied uniformly to every computed pair, not chosen per-pair.
- **A small override block** in the same `family_adjacency.json`, for a pair the computed value gets visibly wrong because one family is sparse (today's real `ml_infra`, at 11 title-matched jobs, won't have a reliable skill-presence vector until backfill fills it out). Overrides are listed explicitly, each with a one-line reason, and are a data-file edit like any other taxonomy change — reviewed and committed, not applied silently and not re-decided per score.

I'll run this command and show you the real computed matrix once family classification exists (§2) — not a table I write up front.

---

## 2. Scrape-time classification (your hard constraint: once, not at scoring time)

### Schema

Add to the job record (and therefore to `jobfit/store/schema/` as a new migration, and to the `Job`-shaped dict every fetcher produces):

```
family            TEXT      -- e.g. "ml_infra", or NULL for unknown
canonical_title   TEXT      -- e.g. "MLOps Engineer", or NULL
family_confidence TEXT      -- "title" | "jd_fallback" | "unknown"
taxonomy_version  TEXT      -- e.g. "1.0.0", so a taxonomy edit can find stale rows
```

This repo stores jobs in SQLite (`jobfit/data/jobfit.db`), not `jobs_cache.json` — I'll add these as four columns via a numbered migration (`jobfit/store/schema/005_job_family.sql`), following the existing migration convention (`PRAGMA user_version`-gated, never edit an applied one).

### Where classification runs — not in the fetcher

Corrected per your note; I'd placed this one stage too late in the first draft. The real pipeline, verified against the code:

```
strategy.fetch(company, career_url)   # raw page -> list[JobPosting] (title/url/location/description/department/...)
     |
     v
DetailEnricher.enrich(posting)        # EXISTING stage, jobfit/scrape/enrich.py: GenericHtmlEnricher fetches
     |                                # the job's own page for a fuller description + builds Evidence
     v
classify(title, description)          # NEW, pure function, runs once per posting, here
     |                                # -> (family, canonical_title, confidence, taxonomy_version)
     v
CompanyScrapeService.scrape() returns ScrapeResult(postings=[...])
     |
     v
update_jobs.fetch_company_result / diff_and_update -> store.jobs.upsert_scraped   # persist
```

`classify()` is a standalone function in `jobfit/ats_scorer/taxonomy.py` (or a new sibling module, not inside `enrich.py`) — it takes only `(title, description)` and returns the 4-tuple, no network, no DB, no dependency on which `DetailEnricher` produced the description. `CompanyScrapeService.scrape()` calls it once per posting, right after the existing `postings = [p if p.evidence is not None else self._noop.enrich(p) for p in postings]` line, before returning — so every posting is classified exactly once regardless of which fetch/enrich path produced it. The 4 fields get added to `JobPosting` (`jobfit/scrape/models.py`) the same flat way `title`/`url`/`location` already sit on it.

**The fetcher never imports the taxonomy**: `strategy.fetch()` (`jobfit/scrape/strategies.py`) only ever returns raw postings — it has no family/taxonomy dependency, now or after this change. `classify()` is wired in at the `CompanyScrapeService` level, one layer above any individual fetch strategy.

**Backfill is the exact same function, no network**: `jobfit/scripts/normalize_text.py --families` reads `(id, title, description)` for every stored job, calls `classify()` directly (the identical function, not a reimplementation), and writes the 4 fields back. Nothing in `classify()` knows or cares whether its input came from a live fetch or a stored row.

### Scorer reads, never reclassifies

`jobfit/ats_scorer`'s job-side input becomes `(must_have, nice_to_have, family, canonical_title)` instead of re-deriving `role_family` from raw text on every score call (today's `jd_extractor.extract_job_requirements` derives `role_family` itself, per job, per profile — see `scoring.py:296`, called once per profile in `score_job_both`, so a 2-profile run classifies every job's family twice, from scratch, every single scoring run). Reading a stored column is a pure lookup — this is where "fast" comes from, as you specified.

### Backfill and re-run

```
uv run python -m jobfit.scripts.normalize_text --families          # backfill: classify every row with no family/stale taxonomy_version
uv run python -m jobfit.scripts.normalize_text --families --force  # re-run everything after a taxonomy edit, regardless of version
```

Follows the exact pattern already established this session for `--departments`, `--titles`, `--locations`, `--remote`, `--nav-junk` in `jobfit/scripts/normalize_text.py` — I'd add `--families` as one more flag in the same file rather than a new script, since it's the same kind of one-off-plus-rerunnable repair this script already exists for.

`--families --report` prints what you asked for: jobs per family (open + total), % unknown, and 20 random samples per family — read from the DB at run time, so it's identical on any machine and reflects whatever's actually stored, not a snapshot I observed once.

---

## 3. Candidate profile — `build_profile(cv) -> CandidateProfile`

Computed fresh from whatever CV text is passed in, never from a name-keyed table, per your hard constraint. Concretely:

1. **Fix §0.1 first** (the title/company swap). Without this, role-block parsing produces garbage titles for any CV using that layout, and nothing downstream — family vector, signature, evidence weighting — has real input.
2. **Classify each extracted role** into a family, using the *same* classifier as the job side (title rule → JD-fallback on the role's own bullets when the title is generic → unknown). This is already structurally close to what `_parse_roles` does today (`role_families.classify(entry["title"], " ".join(bullets))`) — it already passes bullets as a fallback source, so most of the mechanism exists; it just needs the taxonomy split applied and the input title to be correct.
3. **Recency/duration weighting**: last role counts most, roles older than 6 years count little. I'll implement this as a decay curve over `role.start`/`role.end` (already parsed), feeding a weighted family-affinity vector rather than a flat per-role vote — e.g. `weight = duration_years * recency_decay(years_ago)`, `recency_decay` linear-to-zero at 6 years, matching your stated expectation.
4. **Output**: a full affinity vector over all 25 families (0-1 each), not just the top one — needed for the adjacency-aware family_fit gate in Step 4.
5. **Signature skills**: the taxonomy already distinguishes `evidence_strength: "strong"` (found in a role's bullets — real usage) from `"weak"` (found only in a skills list — self-reported) at extraction time (`cv_extractor._extract_skills`, already built, already populated on every `CandidateProfile`). Signature = `{skill for skill in profile.skills if skill.evidence_strength == "strong" and idf[skill] >= signature_threshold}`. This reuses an existing, already-correct field rather than inventing new CV-side logic — the only new work is intersecting it with the IDF table from Step 3.
6. **Manual override file**: `jobfit/data/profile_overrides/<cv_file_hash>.json` (or alongside wherever `jobfit/cv.py` already keys a profile — I'll check its existing per-profile storage and match it rather than inventing a second location), holding `{family: boost|block}` pairs applied as the last step, strictly on top of the computed vector.
7. **API**: `build_profile(cv_text) -> CandidateProfile`, `score(profile, job) -> ScoreResult`, `rank(profile, jobs) -> list[ScoreResult]`. No module-level candidate state — matches the existing `ats_scorer` package's own stated rule ("takes strings... independent of the rest of jobfit").

**Before building the formula around it:** `uv run python -m jobfit.ats_scorer.cli profile --cv <path>` prints a CandidateProfile's full affinity vector and signature skill list. I'll run it for your CV and for one synthetic frontend-React CV and paste both outputs for you to check the mechanism generalizes — the command is the thing that computes them, not me.

---

## 4. IDF skill weights

- Computed once, from the job corpus only (never the CV), exactly as specified, by a command: `uv run python -m jobfit.ats_scorer.cli compute-idf`, writing `jobfit/ats_scorer/data/skill_idf.json` versioned with `{corpus_size, computed_at, skill: idf}`. Methodology and the real top/bottom-30 numbers in §0.5 are that computation's own output from the exploratory run already done — the production version is the same logic shipped as a committed command, not a number I transcribed.
- **Before computing the production version**, I need your go-ahead to expand `skills_taxonomy.json` with the signature vocabulary that's currently missing (§0.5): Model Inference, Model Serving, LLM Evaluation/Benchmarking, Distributed Inference, ONNX, Triton, Observability, GenAI (as its own alias distinct from "Large Language Models"). Without these as taxonomy entries, IDF has nothing to compute for them and Step 3's signature detection finds nothing.
- Recompute command: `uv run python -m jobfit.ats_scorer.cli recompute-idf` (or folded into the existing `normalize_text.py` pattern — your call), writing a fresh `skill_idf.json` with an updated `corpus_size`/`computed_at`; the scoring-engine fingerprint (`SCORING_ENGINE_FINGERPRINT`) already hashes every data file this package depends on, so a new IDF file auto-invalidates cached scores with no extra wiring.

---

## 5. New scoring formula

```
score = family_fit(candidate_vector, job.family) × job_fit(profile, job, idf)
```

- **`family_fit`**: read `candidate_vector[job.family]` (0 if `job.family` is `unknown`'s affinity-0.5 placeholder — see §1 mapping rules). Adjacent families get their value from the Step 1 affinity table rather than 0. **Gate**: `family_fit < 0.3` caps the final score at 35, exactly as specified, applied *after* the multiply (so a low-affinity job can't be rescued by a huge job_fit).
- **`job_fit`**: keeps the existing must-have/seniority/evidence sub-score structure (§0, the parts that already work: `evidence_depth`, hard gates for unmet hard requirements, seniority gap), but `must_have_coverage`/`nice_to_have_coverage` now weight each matched requirement by its IDF (`skill_idf.json`) instead of counting every match equally — a shared "Python" contributes a small fraction of what a shared "Model Quantization" or "Triton" does.
- **Signature bonus**: `+N` when the job's extracted requirements include 2+ of the candidate's signature skills (§3.5) — on top of `job_fit`, not folded into coverage, since it's a distinct "this is specifically you" signal rather than generic requirement coverage.
- **Negative evidence**: a must-have belonging to a *different* family than the job's own stated family (e.g. "A/B testing" and "statistics" as must-haves on a job classified `backend` but written with `data_science`-flavored requirements) subtracts rather than being ignored — implemented as: for each matched must-have, if `skill_family(requirement) not in {job.family, *adjacent(job.family)}`, apply a penalty proportional to its match strength.
- **Recalibration target, stated explicitly as requested**: a Senior Backend/ML-Infra role whose must-haves are covered by Dan's last two roles must land ≥85.

### Weight tuning is a search script, not me

Every number above with a free coefficient — the signature bonus `N`, the negative-evidence penalty magnitude, the `family_fit < 0.3` gate's exact threshold, the five existing sub-score weights in `WeightConfig` — is fit by a script, not proposed by me and adjusted by eye. `uv run python -m jobfit.ats_scorer.cli fit-weights --labels <path to your labeled set>`:

1. Takes a bounded search space for each free coefficient (I define the bounds and what's held fixed vs. free; the script does the search — e.g. `N ∈ [0, 20]`, sub-score weights constrained to sum to 1.0).
2. Runs a grid or random search, scoring each candidate weight configuration by precision@20 + precision@50 on your labeled set.
3. **Hard-rejects** any configuration that violates a stated constraint regardless of its precision score — the ≥85-for-a-true-fit target from above, and the two regression constraints from §6 (a Data Scientist/Security Researcher job must never exceed 40 for your profile; a backend job must never exceed 40 for the frontend CV) are implemented as assertions the search filters on, not hopes.
4. Prints the winning configuration and its precision@20/@50, writes it to `jobfit/ats_scorer/config.py`'s data, and that printed output is what I'd show you — not a number I chose.

If no configuration in the search space satisfies the hard constraints, that's the script telling us the formula's *shape* is wrong (not just its weights), and I'd come back to you with that finding rather than relaxing a constraint to make something pass.

---

## 6. Evaluation protocol

- **Dan's labeled set**: 60-80 jobs, fit/maybe/not-fit, including the 4 reported bad examples and 20+ target-title jobs. I'll pull a stratified sample (across score bands and families) for you to label rather than asking you to label a raw top-N, so the set isn't self-selected toward whatever the old scorer already ranked high.
- **3 synthetic CVs** (frontend, data science, QA automation) — I'll write these from real role-bullet patterns in the corpus (not invented from nothing), run them through the same `build_profile`, and the acceptance bar is: top 20 dominated by that CV's own family.
- **Metrics**: precision@20 and precision@50, old scorer vs. new, on Dan's labeled set; family breakdown of the new top-100 for all 4 candidates (Dan + 3 synthetic).
- **Regression tests, both directions**: a Data Scientist/Security Researcher job with heavy Python/Kubernetes overlap must never exceed 40 for Dan's profile; a backend job must never exceed 40 for the frontend CV. These get added to the existing `jobfit/server/tests/test_ats_scorer_*.py` suite.
- **Existing tests preserved as-is**: `test_ats_scorer_golden.py` (band assertions), `test_ats_scorer_gates.py`, `test_ats_scorer_monotonicity.py`, `test_ats_scorer_determinism.py` — the new formula has to keep passing all four, not replace them.

---

## 7. Migration path

1. **Phase 0** (prerequisite, small, mechanical): fix the CV title/company swap (§0.1) and widen the JD header vocabulary + reconsider the 6000-char truncation (§0.2). Each gets its own test built from the real failing cases found here. This alone restores the *existing* `role_family_mismatch_cap` gate to working order and should visibly fix most of the 35 not-fit jobs in the current top-100, before any new code exists. The checkpoint is a command, not my narration: `uv run python -m jobfit.ats_scorer.cli compare --cv <path> --jobs <ids or --all>` scores the same jobs under the pre-Phase-0 and post-Phase-0 code and prints a diff table (score deltas, which family-mismatches got capped, old vs. new top-20 overlap) — that printed table is the checkpoint, and it's what I'd paste for you, not a summary of what I looked at.
2. **Phase 1**: taxonomy data file (25 families + adjacency + level-2 canonical titles), versioned, with the JD-fallback classifier. Backfill + report (§2).
3. **Phase 2**: `skills_taxonomy.json` expansion for signature vocabulary + `skill_idf.json` computation (§4).
4. **Phase 3**: `build_profile` rewrite (family vector, signature, overrides) (§3).
5. **Phase 4**: new formula in `scorer.py`/`config.py`, weights fit against the labeled set (§5).
6. **Phase 5**: evaluation (§6), iterate weights until the 85+ target and the regression tests both hold.

Each phase is independently testable and independently committable, in the spirit of this session's other multi-phase work (store migration, app rebuild). `SCORING_ENGINE_FINGERPRINT` already auto-invalidates cached scores on any data/code change in this package, so no manual `--force-rescore` coordination is needed between phases.

---

## Open questions for you

1. **Security split** — OK to split `security` into `security_engineering`/`security_research`, and `devops` into `devops_sre`/`infrastructure_platform`? (Real-title evidence supports both; happy to keep either merged if you'd rather.)
2. **6000-char truncation** — can I raise/remove it for stored description text? (It's hitting 22% of described jobs and disproportionately cuts the requirements section.) Would increase `jobfit.db` size somewhat.
3. **Non-technical families** (sales/marketing/support/finance/hr/legal/operations) — keep them granular (as `role_families.json` already has them) or collapse to one `non_technical` bucket? Zero cost either way; just affects how fine the affinity table needs to be.
4. **Phase 0 checkpoint** — want me to do Phase 0 (the two bugfixes) first and run the `compare` command on the real top-100, before committing to building Phases 1-5? I think this is the right order (cheap, fast, tells us how much of the problem the family/IDF layer actually needs to solve versus how much was extraction bugs) but it's your call.

Waiting for your go-ahead (or adjustments) before writing any code.
