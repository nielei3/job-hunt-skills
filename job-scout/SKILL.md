---
name: job-scout
description: Score external JD pages against the master resume and write outputs to Obsidian.
---

# job-scout

Use this skill for the daily job workflow.

## Portability contract
- Read `JOB_SCOUT_ROOT` from `~/.hermes/.env`
- Read project config from `$JOB_SCOUT_ROOT/config/job-scout.yaml`
- Read machine-specific paths like `OBSIDIAN_VAULT_PATH` from `~/.hermes/.env`
- Do **not** assume any hard-coded absolute path

## Inputs
- Resume source: resolve from `config/job-scout.yaml`
- Candidate profile: resolve from `config/job-scout.yaml`
- Enriched jobs JSON: resolve from `config/job-scout.yaml`
- Obsidian vault path: read `OBSIDIAN_VAULT_PATH` from `~/.hermes/.env`
- Obsidian output base dir: read from `config/job-scout.yaml`

## Guardrails
- Do **not** visit or scrape linkedin.com pages (the resolver scripts handle this — `linkedin_public` jobs were fetched by the pipeline, not by you).
- Only use jobs whose `external_jd_status` is `resolved`, `linkedin_public`, or `ats_api`.
- Leave unresolved jobs in the daily report as `needs_manual_review`.

## Scoring rubric

### Step 0 — Role type pre-check (before scoring)
Read `role_type_exclusions` from `profile.yaml`. If the job is fundamentally
the wrong role type, cap the score at 45 regardless of keyword overlap and
set verdict to `weak_match`. Do not let domain keyword similarity override
a role type mismatch.

Key distinctions to enforce:
- **ML Engineer / Applied Scientist / Research Engineer**: requires training,
  fine-tuning, or researching ML models (PyTorch, model architecture, RLHF,
  evals). This is NOT a match even if the job title contains "Platform" or
  "GenAI" — check the JD body for what is actually required.
- **Platform/Infrastructure Engineer**: builds the systems that *run* models
  (serving infra, API gateways, model proxy, MLOps pipelines). This IS the
  target role type.
- **Frontend Engineer**: primarily React/Vue/CSS/browser work. Cap at 40.
- **Full-Stack Engineer**: if the JD body shows frontend is >50% of the scope
  (React, TypeScript, UI components, CSS), cap at 40. If it's backend-heavy
  full-stack, score normally.
- **Engineering Manager / Director / VP**: always `weak_match`, score ≤ 30.

### Step 1 — Score 0-100 across six dimensions
- Must-have hard requirements match: 40 pts
- Core skill overlap (from `core_skills` in profile.yaml): 20 pts
- Relevant domain / industry experience: 15 pts
- Seniority / scope fit (floor: Staff or Principal): 10 pts
- Location / remote / work authorization fit: 10 pts
- Differentiators / nice-to-haves: 5 pts

### Verdict bands
- 91-100: `strong_match`
- 71-90: `medium_match`
- 0-70: `weak_match`

## Outputs
Write markdown into Obsidian under `<obsidian_base_dir>` from `config/job-scout.yaml`, with:
- `Daily Reports/`
- `Opportunities/`

### File naming
- Daily report: `YYYY-MM-DD.md`
- Opportunity note: `<Score> - <Company> - <Role>.md`

### Daily report contents
Include:
- total jobs found
- resolved JD count
- unresolved count
- table or bullets of jobs with score + verdict
- quick recommendations
- wikilinks to opportunity notes

### Opportunity note contents

Every Opportunity note **must** start with YAML frontmatter containing `match_score`
and `verdict`. This enables Dataview sorting/filtering in Obsidian.

```markdown
---
match_score: 78
verdict: strong_match
---

# <Company> — <Role>
...
```

After the frontmatter, include:
- title
- company
- location
- source URLs
- match score (also inline for readability)
- verdict
- top strengths
- key gaps
- raw JD excerpt or summary

**When updating an existing note** (e.g., re-scoring), update both the frontmatter
values and the inline score/verdict to keep them in sync.

**Score extraction pitfall**: Opportunity notes may use two different inline formats
depending on which pipeline generated them:
- Format A (bullet): `- **Match score:** 78` / `- **Verdict:** strong_match`
- Format B (table): `| **Match Score** | 78/100 |` / `| **Verdict** | \`weak_match\` |`
When reading existing notes, check both patterns. The frontmatter is the canonical source.

### Filename score-prefix convention

Opportunity filenames use a **2-digit score prefix** for filesystem sort order:
`<Score> - <Company> - <Role>.md` (e.g., `78 - Reddit - Senior Staff Engineer, Ads.md`).

`triage_and_write_obsidian.py` now auto-applies the score prefix:
- `opportunity_filename(job, score=N)` generates `<NN> - <Company> - <Role>.md`
- `_find_existing_opportunity()` searches across all filter dirs for existing notes
- The main loop routes score ≤ 70 directly to `Filtered/Low Score/`
- Existing notes are renamed/moved when their score changes

When running the agent manually (Resolved-JD mode), always pass the score to
`opportunity_filename()` or apply the score prefix when creating the file.

When **re-scoring** an existing note, rename the file if the score changed and update
any wikilinks in Daily Reports that reference the old filename.

`triage_and_write_obsidian.py` uses `_find_existing_opportunity()` for dedup
before writing. This prevents duplicate notes when the same job is re-scored
with a different score.

## Post-scoring filters (Obsidian organization)

After scoring, Opportunity notes are organized into subfolders under
`Opportunities/filtered/` based on these rules:

### Filter rules (applied in order)
1. **No JD** → `filtered/No JD/` — jobs where `external_jd_status` is not
   `resolved`, `linkedin_public`, or `ats_api` (no JD text available to score).
2. **Location mismatch** → `filtered/Location/` — onsite jobs outside your
   configured metro (`scan.location_filter.allowed_cities`). Remote/Hybrid always pass.
   Uses the same `scan.location_filter` config as the pre-fetch filter.
3. **Low Score** → `filtered/Low Score/` — score ≤ 70 (`weak_match`).

### Folder structure
```
Opportunities/
├── 82 - Stripe - Backend Engineer, Core Technology.md   # active (score > 70)
├── 77 - OpenAI - Principal Software Engineer, B2B.md
├── filtered/
│   ├── Location/    # onsite outside configured metro
│   ├── Low Score/   # score ≤ 70
│   └── No JD/       # unresolved JD
```

### When to apply
- **Automated pipeline** (`triage_and_write_obsidian.py`):
  apply filters after writing all notes.
- **Manual scoring** (Resolved-JD mode): apply filter immediately — if score ≤ 70,
  write directly to `filtered/Low Score/`; if location mismatches, write to
  `filtered/Location/`.
- **Re-scoring**: if a note's score changes to ≤ 70 or location is found to mismatch,
  move it to the appropriate filtered subfolder and update Daily Report wikilinks.

### Threshold
- Active (stays in `Opportunities/`): score > 70
- Filtered to Low Score: score ≤ 70

This threshold is intentionally different from the verdict bands — `medium_match`
starts at 71, so all `weak_match` notes (0-70) are filtered out of the main view.

## Location filter

The `scan.location_filter` section in `job-scout.yaml` filters out onsite jobs
outside the user's metro area **before** JD fetching/scoring, which drastically
reduces processing time.

```yaml
scan:
  location_filter:
    allowed_cities:
      - your-city
      - your-metro-suburb
    allow_remote: true
```

**Rules:**
- Remote/hybrid jobs always pass (if `allow_remote: true`).
- Onsite jobs must mention at least one allowed city (case-insensitive substring match).
- Empty/unknown location passes (benefit of the doubt).

**Enforced in three places:**
1. `scan_target_companies.py` — `location_passes()` filters after title match,
   before JD fetch. Log line shows: `142 total → 28 title → 12 location → 8 new`.
2. `filter_titles.py` — `classify_one()` applies the same filter to LinkedIn
   alert jobs using `scan.location_filter` config.
3. `triage_and_write_obsidian.py` — routes location-mismatched jobs to
   `Filtered/Location/` before LLM scoring (uses `location_passes_city_filter()`).

**Shared implementation** (`job_scout_lib.py`):
- `location_passes_city_filter(location, cfg)` — canonical city-level filter.
  Handles airport-code abbreviations (SFO → san francisco), vague locations ("United States", "N/A",
  "5 Locations") pass, remote/hybrid pass. Used by pipelines 3 and 4 above.
- `is_us_location(location, cfg)` — coarser US-vs-non-US filter (for geo gate
  that drops non-US jobs before scoring). Used in addition to city filter.

**Historical bug (fixed 2026-05-01)**: Before this fix, `triage_and_write_obsidian.py`
only called `is_us_location()` (US vs non-US check) but NOT the city-level filter.
Result: SF/NYC/etc. onsite jobs passed through to main `Opportunities/` instead of
being routed to `Filtered/Location/`.

## Per-company title filter overrides

Each company in `target_companies` can override the global `scan.title_include`
and `scan.title_exclude` patterns. Implemented by `_company_title_filter()` in
`scan_target_companies.py`.

```yaml
# In target_companies entry:
- name: TikTok
  ats: tiktok_careers
  title_include:          # replaces (not extends) global title_include
    - tech lead
    - engineering lead
    - architect
  title_exclude:          # replaces (not extends) global title_exclude
    - sre
    - site reliability
    - machine learning
    - quality assurance
```

**Rules:**
- If a company has `title_include`, it **fully replaces** the global list for that company.
- If a company has `title_exclude`, it **fully replaces** the global list for that company.
- If omitted, the global `scan.title_include` / `scan.title_exclude` applies.
- Include and exclude are independent — you can override one without the other.

**Use case**: TikTok returns many "Tech Lead" titles across ML, SRE, QA, etc.
The per-company `title_include` narrows to leadership roles, while `title_exclude`
filters out role types that don't match the user's profile (SRE, ML, QA).

## Adding new target companies

To add a company to `target_companies` in `job-scout.yaml`:
1. Visit the company's careers page.
2. Identify the ATS: look for `boards.greenhouse.io/SLUG`, `jobs.ashbyhq.com/SLUG`,
   `jobs.lever.co/SLUG`, or Workday/Phenom patterns.
3. Quick verification: `curl -s -o /dev/null -w "%{http_code}" "https://jobs.ashbyhq.com/SLUG"`
   (200 = confirmed).
4. Add the entry to `job-scout.yaml`:
   ```yaml
   - name: CompanyName
     ats: ashby        # or greenhouse, lever, workday, phenom, pcsx, etc.
     board_slug: slug
   ```
5. Supported ATS types: `greenhouse`, `lever`, `ashby`, `workday`, `phenom`,
   `pcsx`, `apple_jobs`, `amazon_jobs`, `google_careers`, `meta_careers`,
   `shopify_careers`, `cursor_careers`, `tiktok_careers`, `tiktok_usds`,
   `remoteok`, `weworkremotely`.

## ATS adapter maintenance & known fragility

Adapters scrape career sites that change without notice. When an adapter returns
0 results or errors, diagnose with:
```bash
python3 scripts/scan_target_companies.py -c <company_name_lowercase>
```

### Adapter-specific notes (updated 2026-04-28)

| Adapter | Technique | Fragility | Notes |
|---------|-----------|-----------|-------|
| Adapter | Technique | Fragility | Notes |
|---------|-----------|-----------|-------|
| `apple_jobs` | SSR hydration parse (`__staticRouterHydrationData`) | Medium | Old `api/role/search` → 301 as of 2026-04. New approach parses `JSON.parse("...")` from HTML. If Apple changes hydration var name, regex breaks. |
| `google_careers` | HTML regex on SSR page | Medium | Uses `<base href>` + relative links + `aria-label` for title. If Google switches to SPA or changes aria pattern, breaks. |
| `meta_careers` | **DISABLED** | N/A | SPA-only + bot detection (HTTP 400). Needs headless browser or GraphQL reverse-engineering. Rely on LinkedIn alerts. |
| `shopify_careers` | XML feed (`/careers/feed.xml`) | Low | Stable public feed with full JD HTML. No Ashby public board despite using Ashby backend. |
| `greenhouse` / `lever` / `ashby` | Official JSON APIs | Low | Most stable — public posting APIs rarely change. |
| `amazon_jobs` | JSON search API | Low | `/en/search.json` endpoint. |
| `pcsx` (Microsoft) | JSON API | Low | |
| `tiktok_usds` | Playwright CDP → ai-chrome | Low | Connects to always-on ai-chrome (`localhost:9222`) via `connect_over_cdp()`, falls back to headless launch. **CDP pitfall**: `browser.new_context()` breaks over CDP (context gets immediately closed); must use `browser.contexts[0]` instead. **Critical headers**: `website-path: usds`, `portal-channel: tiktok`, `portal-platform: pc` — without `website-path: usds` the API returns all 10k ByteDance jobs instead of ~300 USDS jobs. Server-side filters via config: `usds_job_category_id_list`, `usds_location_code_list`, `usds_recruitment_id_list`. Filter IDs: R&D=`6704215862603155720`, Location=`CT_157` (example), Experienced=`1`. |
| `tiktok_careers` | POST JSON API (`api.lifeattiktok.com`) | Low | Migrated from `careers.tiktok.com` → `lifeattiktok.com` (2025). **Critical header**: `website-path: tiktok` (without it → 400). Endpoint: `POST https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts`. Body: `{"keyword":"","limit":50,"offset":0}`. JD inline (description + requirement fields). Server-side filters via config: `tiktok_job_category_id_list`, `tiktok_location_code_list`, `tiktok_recruitment_id_list` — passed as POST body fields. **URL format**: `https://lifeattiktok.com/search/{numeric_id}` where `numeric_id` = `raw['id']` from API (e.g. `7615749350220302597`). Do NOT use `job_key` (SHA1 hex hash) — it produces invalid URLs. |
| `workday` / `phenom` | HTML scraping | Medium | Large employers (Netflix, Salesforce, etc.). Workday layout changes occasionally. |

### URL ID pitfall for TikTok notes

`_build_job()` generates a `job_key` as a 16-char hex SHA1 hash (e.g., `6ed0f1208b6c2f6b`).
The TikTok JD URL must use the **numeric posting ID** from the API `id` field
(e.g., `7615749350220302597`), NOT the `job_key`. If an Obsidian note has a URL like
`lifeattiktok.com/search/<16-char-hex>`, it's broken — the hex is the `job_key`,
not a valid posting ID. Fix by looking up the correct numeric ID from the API.

### Debugging pattern for broken adapters
1. `curl -s -o /dev/null -w "%{http_code}" <endpoint>` — check if endpoint is alive
2. `curl -s <url> | python3 -c "import sys; print(len(sys.stdin.read()))"` — check if response has content
3. For SSR sites: look for `window.__` hydration data or `<script id="__NEXT_DATA__">` JSON
4. For SPAs: check if job data is in initial HTML or requires JS rendering (if JS-only, urllib won't work)
5. For XML/RSS: check `/careers/feed.xml`, `/careers/rss`, `/jobs/feed` patterns

## Multi-source email parsing architecture

`fetch_linkedin_alerts.py` uses a **dispatcher pattern** to route emails from the
IMAP folder to source-specific parsers. No sender filtering is applied — all emails
in the `Career/jobs` IMAP folder are treated as job alerts (the folder is the trust
boundary).

### Architecture
```
IMAP (Career/jobs folder) → all messages
  ↓
scripts/email_parsers/dispatcher.py — routes by sender domain
  ├── linkedin.com → linkedin_parser.py (compound-title splitting, card parsing)
  ├── jobright.ai → jobright_parser.py (structured HTML card parsing)
  └── unknown    → [] (silently skipped; add new parsers here)
  ↓
fetch_linkedin_alerts.py → jobs_today.json (unified format)
  ↓
resolve_external_jd.py → jobs_enriched.json
  ├── ... (existing resolvers: band check, direct ATS, web search, LinkedIn)
  └── resolve_jobright_jd() — fetches JSON-LD from jobright.ai detail pages
```

### Adding a new email source
1. Create `scripts/email_parsers/<source>_parser.py`
   - Implement `build_jobs_from_<source>_message(msg: EmailMessage) -> list[dict]`
   - Each job dict must have at minimum: `title`, `company`, `source`
   - Optional: `location`, `modality`, `salary`, `<source>_url`, `links`, `external_candidates`
2. Update `scripts/email_parsers/dispatcher.py`:
   - Add sender detection in `identify_source()`
   - Add routing in `dispatch_message()`
3. If the source has detail pages with JD text, add a resolver in `resolve_external_jd.py`
   (check for `<source>_url` field, fetch page, extract JD)

### Output format (`jobs_today.json`)
```json
{
  "generated_at": "ISO-UTC",
  "source": "job_alert_email",
  "message_count": 23,
  "source_counts": {"linkedin": 17, "jobright": 6},
  "job_count": 40,
  "candidate_subjects": [...],
  "jobs": [...]
}
```

### Jobright-specific notes
- **Parser**: `scripts/email_parsers/jobright_parser.py`
- **HTML structure**: job cards delimited by `id="job-section"`, with structured
  IDs: `job-company-name`, `job-match-percentage`, `job-title` (has `<a>` link),
  `job-tag` (salary/location/referrals), `job-time-posted`, `job-company-categories`
- **Canonical URL**: `https://jobright.ai/jobs/info/{24-char-hex-id}` (strip query params for dedup)
- **JD resolver**: `resolve_jobright_jd()` fetches detail page, extracts
  `<script type="application/ld+json">` with `schema.org/JobPosting` (full description)
- **Extra fields on job dict**: `jobright_url`, `jobright_match_pct`, `salary`
- **Reference doc**: `docs/jobright-email-structure.md` (complete HTML/JSON-LD spec)
- **Email types**: instant_push (4-6 cards, utm_source=1121) and daily_digest (10 cards, utm_source=1025)
- **Dedup note**: "filler" jobs repeat across instant_push emails; canonical URL dedup handles this

## Procedure
### Full daily mode
If the user asks for the whole daily workflow, run these steps in order:

1. `python3 scripts/fetch_job_alert_emails.py` — fetches from IMAP, writes `jobs_today.json`
2. `python3 scripts/filter_titles.py` — title/seniority/location pre-filter, writes `jobs_filtered.json`
3. `python3 scripts/resolve_external_jd.py` — fetches JD text, writes `jobs_enriched.json`
4. `python3 scripts/scan_target_companies.py` — scans ATS boards, writes `jobs_target_scan.json`
5. `python3 scripts/triage_and_write_obsidian.py` — LLM-scores jobs from `jobs_enriched.json`, writes Obsidian notes + daily report
6. `python3 scripts/triage_and_write_obsidian.py --input data/inbox/jobs_target_scan.json` — LLM-scores target scan jobs (same script, different input)

Then summarize the results for the user.

**Note**: Steps 5 and 6 use the same unified scoring script. The `--input` flag
selects which jobs JSON to process. Without `--input`, it defaults to
`jobs_enriched.json` (email alerts). With `--input`, it processes any jobs JSON
file (e.g., `jobs_target_scan.json` from step 4).

### Cron / timeout pitfalls

#### `triage_and_write_obsidian.py` — scoring runtime

This script scores jobs one-by-one via LLM (~25-30s per job). With 200+
candidates (e.g., `--input jobs_target_scan.json`), it takes **2+ hours** to
complete. In cron contexts:

- **Run it in background** (`background=true` with `notify_on_complete=true`).
  Do NOT wait for it — proceed with the summary using partial results.
- The script uses `_find_existing_opportunity()` for dedup — it updates existing
  notes rather than creating duplicates. Re-running is safe but will re-score.
- For the daily cron summary, report partial results (strong matches found so far)
  and note that scoring is still in progress in background.

#### `triage_and_write_obsidian.py` — can timeout on edge cases

This script can hang (>5 min) on jobs with `external_jd_status: resolved` but empty
`external_jd_text` (0 chars). The LLM call still fires and may timeout. It
processes jobs sequentially, so a hang on the last job means all prior notes
were written successfully. Check Obsidian for notes already written.

- The script is **NOT idempotent by default** — re-running re-scores all resolved
  jobs (may change scores slightly due to LLM non-determinism). Existing notes
  are updated/renamed in place.

#### `scan_target_companies.py` — 8-10 min full run

The target company scan can exceed 10 minutes when run against all ~55 companies.
Key issues in cron/automated contexts:

1. **TikTok USDS (`tiktok_usds`)** — Now uses CDP to connect to ai-chrome
   (`localhost:9222`) instead of launching headless Chromium. Falls back to
   headless if CDP unavailable. Runs in ~7s with server-side filters.

2. **TikTok careers (`tiktok_careers`)** — Now supports server-side filters
   via config (`tiktok_job_category_id_list`, `tiktok_location_code_list`,
   `tiktok_recruitment_id_list`). With R&D + location + Experienced: 3463 → 95 jobs, ~3s.

3. **Batching strategy**: When the full scan times out, split companies into
   batches by adapter type. Run batches sequentially or in parallel, **but
   exclude `tiktok_usds` and `tiktok_careers`** from automated runs.

4. **Snapshot merging required when batching**: Each `scan_target_companies.py`
   invocation **overwrites** `jobs_target_scan.json` (last-writer-wins). When
   running in batches, the final file only has the last batch's results. To
   recover all jobs, merge from the history snapshots:
   ```python
   # Merge all snapshots from data/history/jobs_target_scan/YYYY-MM-DD/
   # Dedup by job_key, write merged result back to jobs_target_scan.json
   ```

5. **Recommended batch grouping** (by adapter speed):
   - Batch 1: Greenhouse + Lever companies (~20s total, ~23 companies)
   - Batch 2: Ashby + Cursor + Shopify + RemoteOK + WWR (~30s, ~20 companies)
   - Batch 3: Microsoft/PCSX + Apple + Amazon + Google (~90s, 5 companies)
   - Batch 4: Workday companies — Salesforce, Nvidia, Adobe, Cisco, Walmart (~120s)
   - Batch 5: TikTok + TikTok USDS (~10s with server-side filters + CDP)
   - Skip: Meta (disabled)

6. **The `-c` flag accepts partial matches** — `-c "Microsoft"` matches both
   "Microsoft" and "Microsoft AI". Be careful with ambiguous names.

### Resolved-JD mode
1. Read `JOB_SCOUT_ROOT` and `OBSIDIAN_VAULT_PATH` from `~/.hermes/.env`.
2. Read `$JOB_SCOUT_ROOT/config/job-scout.yaml`.
3. Resolve the resume source, profile source, enriched jobs JSON path, and Obsidian output base dir from the config.
4. Read the resolved resume source, profile source, and enriched jobs JSON.
5. Process only jobs where `external_jd_status` is `resolved`, `linkedin_public`, or `ats_api`.
6. Generate opportunity notes for all processed jobs.
7. Generate / update the daily report note.
9. If there are zero resolved jobs, still write a daily report saying no actionable JD pages were found.
