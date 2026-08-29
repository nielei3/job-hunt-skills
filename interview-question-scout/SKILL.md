---
name: interview-question-scout
description: Use whenever the user is preparing for a specific company's technical interview using collected 面经 / interview-experience posts. Pulls from **multiple sources** — 1point3acres, LeetCode Discuss, Reddit (r/cscareerquestions + r/leetcode), and (semi-manual) Glassdoor — and merges them into one frequency-ranked study handbook. Triggers on "帮我准备 X 面试", "汇总 X 公司面经", "X 公司高频题", "总结这家公司的题目", "复习 X 面试", "出现频次", "题库", "OpenAI/Anthropic/Google/Meta 面经" + 复习/准备 context, or when the user wants to turn raw posts into a study handbook. The deliverable is a **复习手册**: a frequency-ranked question bank with merged descriptions, observed solutions, and source links — NOT a chronological list of posts. Also use when extending the scraper to a new company/source, or when answering questions about already-collected 面经 data.
---

# interview-question-scout

Use this skill when the user is preparing for a company's technical interview based on collected 面经 (interview-experience posts) and wants a high-leverage study deliverable.

The companion script project at `career/interview-question-scout/scripts/` does **automated periodic scraping** of multiple platforms, summarizes each post via an LLM `claude-opus-4.6`, and merges into one Obsidian handbook per company. This skill is the **interactive aggregator**: it turns dozens or hundreds of scattered posts across sources into a single study handbook.

## Sources (multi-source, plugin architecture)

**Default behavior: every source runs for every company.** No per-company config required — search terms and slugs are derived from the company `name`. The user only configures overrides when defaults are wrong.

All sources implement `scripts/sources/base.py:Source` and return canonical `Post` objects. Registry lives in `scripts/sources/__init__.py:SOURCES`.

| slug | name | data path | auth | default config (from company `name`) |
|---|---|---|---|---|
| `1p3a` | 1point3acres | Chrome CDP `localhost:9222` | Logged-in cookies | `slug` = lowercased-hyphenated name (or explicit `slug:` field) |
| `leetcode` | LeetCode Discuss | Chrome CDP | Logged-in cookies | `search_terms = ["<Name> interview", "<Name> OA"]` |
| `reddit` | Reddit | Public JSON API | Anonymous | `search_terms = ["<Name> interview", "<Name> OA"]`, subreddits = `[cscareerquestions, leetcode]` |
| `glassdoor` | Glassdoor (manual paste) | Local files | Files at `data/manual/glassdoor/<slug>/*.md` | `slug` = same as 1p3a's |

**To opt out** of a source for one company, override in YAML:

```yaml
- name: ByteDance
  sources:
    reddit: { enabled: false }
    leetcode: { search_terms: ["ByteDance TikTok interview"] }   # override defaults
```

**Dispatch a multi-source pull**:

```bash
cd ~/github/agent-skills/career/interview-question-scout
python3 scripts/dispatch.py --company Whatnot --since-days 365 --limit-per-source 30 --verbose
# Or just one source:
python3 scripts/dispatch.py --company Whatnot --only-source reddit --verbose --out-json /tmp/whatnot.json
```

The dispatcher returns a combined `List[Post]`. Pipe into `summarize_posts.py` → `aggregate_handbook.py` (both already source-agnostic) to produce the final handbook.

## Core goal (read this before deciding what to produce)

The user is preparing to take an interview. They will spend hours studying. Their time is the bottleneck, not yours. The deliverable must answer:

1. **Which questions actually appear?** — frequency-ranked, deduplicated across posts.
2. **What is each question, in full?** — merged from all observers; specific enough to recognize on sight.
3. **What's the standard solution / what tripped people up?** — synthesized across posts.
4. **What round / role does it map to?** — phone screen vs onsite, MLE vs SWE vs Researcher.
5. **Where to dig deeper?** — links back to the original threads if they want more detail.

The handbook is a **review tool**, not an archive. If the user can't pick it up the night before an interview and study from it, the deliverable failed.

## Anti-patterns — do NOT produce

- A chronological list of post titles with brief excerpts. (That's the auto-scout output. The user can already get that.)
- A bullet per thread. The user thinks in **questions**, not threads. One canonical question may appear in 8 different threads — collapse them.
- "Coding (40 posts)" / "SD (25 posts)" with no detail. Counts at the post level are noise; counts at the **question** level are the leverage.
- Generic summaries that lose specifics. "Implement a system that maintains state over time" is useless. "GPU Credit II: implement `create_grant(amount, expiry)`, `subtract(amount)`, `get_balance()` with credits expiring in FIFO order" is what the user needs.
- Skipping aggregation because "the data is messy." That's exactly when aggregation is most valuable.

## When to use this skill

Activate on any of:
- User asks to "prep for X", "summarize X interview questions", "X 面经汇总", "X 高频题".
- User points at a 1point3acres company face-jing page and asks for review material.
- User asks "are there enough 面经 for X yet?" — answer this with the question-level count, not post count.
- User wants to extend interview-question-scout's company list and asks how.

Do **not** activate for:
- One-off "what was asked at company X yesterday?" — just read the latest post directly.
- Resume tailoring or behavioral prep — those are `job-scout` / unrelated.

## Data sources, in priority order

1. **Already-scouted data** in Obsidian: `Career/Company/<CompanyName>/0 interview experience.md` (interview-question-scout's automated output) — fast, no scraping needed. Read this first; it has the per-post LLM summaries already. (Previously at `Career/Interviews/<Company>.md`; moved May 2026 to co-locate with other per-company notes, filename renamed from `0 面经.md` to `0 interview experience.md`.)
2. **interview-question-scout SQLite + raw HTML** at `career/interview-question-scout/data/raw/<slug>/<tid>.html` — when you need full bodies, not summaries.
3. **Live scrape of 1point3acres** when the user is targeting a company that hasn't been added to interview-question-scout yet, or wants newer-than-last-scout data.
   - For **fresh companies**: edit `career/interview-question-scout/config/interview-question-scout.yaml` to enable the company, then run the scout once.
   - For **deeper history** than the scout's 9-newest window: run `--deep --months 12` (see `Live deep-scrape` below).

Always start with #1 — fastest, cheapest, and the data is already LLM-cleaned.

## Hard rule: ai-chrome CDP is mandatory

**All scraping of 1point3acres MUST go through ai-chrome CDP (`localhost:9222`).**
Even though the user's account currently has 0 积分 (can't bypass 188+ paywall),
CDP is still required because:
1. **Login state** avoids captcha and rate-limit walls
2. **Reply content** is fully visible (not behind paywall) — replies often contain
   interview details, question clarifications, and tips
3. **Partial OP visibility** — the first few lines of OP before the paywall marker
   often contain key question names and round info
4. **SPA pagination** — the listing page requires JS execution to paginate

The scripts (`fetch_1point3acres.py`, `run_interview_question_scout.py`) already
use `connect_over_cdp('http://localhost:9222')` by default. Do NOT bypass this
with alternative fetch methods.

## Workflow

### Step 1: Locate or refresh the data

```bash
# Read OBSIDIAN_VAULT_PATH from ~/.hermes/.env
OBSIDIAN_VAULT_PATH=$(grep '^OBSIDIAN_VAULT_PATH=' ~/.hermes/.env | cut -d= -f2-)
ls "$OBSIDIAN_VAULT_PATH/Career/Company/<CompanyName>/0 interview experience.md"
```

If the file exists and is recent, use it. Otherwise, fall back to live scrape (Step 1b).

#### Step 1b: Live deep-scrape (when scout hasn't covered enough)

**MANDATORY: Always use ai-chrome CDP (`localhost:9222`) for all scraping.**
Never use raw HTTP/urllib — the user's login session (with 积分) is only available
via the browser's cookie jar. Without CDP, posts behind the 大米 paywall are unreadable.

The orchestrator script now supports `--deep` mode which paginates through the listing:

```bash
cd ~/github/agent-skills/career/interview-question-scout
python3 scripts/run_interview_question_scout.py \
  --company <slug> --deep --months 12 --verbose
```

This will:
- Paginate listing pages (clicking `.ant-pagination-next`) until posts are older than 12 months
- Fetch each thread body via CDP (inherits user's login cookies/积分)
- Summarize via the LLM claude-opus-4.6
- Write to Obsidian

**Default lookback: 12 months.** Override with `--months N`.

Pattern (validated in Apr 2026 against jobs.1point3acres.com SPA):
- Pagination is **internal SPA state** — `?page=N` URL param does NOT work. Click `.ant-pagination-next` or `.ant-pagination-item-N` instead.
- Wait for the first `a[href*="thread-"]` to change before reading new posts (DOM-mutation signal, not URL change).
- Pace: 2–4 s between page clicks, 2.5–4 s between thread reads. Faster gets you rate-limited.
- Each thread is at `https://www.1point3acres.com/bbs/thread-<id>-1-1.html`. The fetcher reads **OP + all reply comments** (all `td#postmessage_*` elements), not just the OP. Replies often contain interview details, clarifications, and tips.
- The user's logged-in cookies carry across because both domains share `1point3acres.com`. No login wall.

**First-run auto-deep**: When the scout runs for a company that has no `data/bodies/<slug>.json` yet, it automatically enables deep mode (12-month lookback) even without `--deep`. This ensures the first handbook has comprehensive data.

### Step 2: Aggregate via the LLM

The aggregator can run standalone or be triggered automatically after each scout run.

**Standalone** (reads from scout's accumulated bodies JSON):
```bash
cd $(git rev-parse --show-toplevel)/career/interview-question-scout
python3 scripts/aggregate_handbook.py --company OpenAI
```

**Auto-triggered after scout** (via `--handbook` flag):
```bash
python3 scripts/run_interview_question_scout.py --company openai --handbook
```

The aggregator uses incremental stage1 caching — only new posts need LLM extraction. Stage2 (canonicalization) re-runs every time to update frequency counts and merge examination points from new posts.

The script:

1. **Map**: feeds posts in batches of ~8 to the LLM `claude-opus-4.6`. For each post, extract `{question_name, type, what, solution_hint, follow_ups, examination_points[], round, role}`.
2. **Reduce**: feeds all extracted questions to Opus 4.6, gets back **canonical clusters** with aliases unified, frequency, merged descriptions/solutions, and merged examination points.
3. **Render**: writes a Markdown handbook with each question as an H2 heading.

**Output paths** (all under `<vault>/Career/Company/<Name>/`, i.e. the company's own subfolder — co-located with `0 interview experience.md`):

- `<Name>-Interviews.md` — final handbook (rendered Markdown)
- `<Name>-Interviews.stage1.json` — per-post extraction cache (incremental; survives re-runs)
- `<Name>-Interviews.canonical.json` — final canonicalized question set (machine-readable)

Previously these three files lived at `Career/Company/<Name>-Interviews.*` (one level up). Moved May 2026 so everything for a given company stays in one folder. The scripts auto-create the company subdir if missing.

### Step 3: Output structure

The handbook MUST have, in this order:

```markdown
# <Company> 面试复习手册

> 数据范围 / 抓取日期 / 帖子总数 / 不同题目数

## 速览：高频题榜（≥2 次）

| # | 题目 | 类型 | 频次 | 难度 | 主要场次 |
|---|---|---|---|---|---|
| 1 | GPU Credit | Coding | 9 | 中等 | 60min 店面 |
| 2 | autograd / backprop | ML Coding | 8 | 偏难 | 75min ML coding |
| ... |

## GPU Credit _(频次 9 · Coding · 60min 店面)_

**考查点**：
- FIFO 数据结构
- Lazy evaluation vs eager cleanup
- 时间复杂度分析
- 边界条件（过期、余额不足）

**题面**：设计一个 GPU credit 系统…

**主要解法**：…

**踩坑**：…

**Follow-up**：…

**来源**：[#1174310](url) · [#1171972](url) · …

---

## autograd / backprop _(频次 8 · ML Coding · 75min ML coding)_
… (同样结构：考查点 + 题面 + 解法 + 来源)

## 罕见题（频次 = 1）
（一句话列表）

## 元信息
- 抓取脚本 / 命令
- 帖子总数、聚合到的不同题数
- 抓取失败 / 漏抓的帖子
```

**Key changes (May 2026)**:
- Each question is an **H2 heading** (not H3 inside type-group sections) — flat outline for easy Obsidian navigation.
- **考查点 (examination points)** as bullet list per question — what skills/knowledge the question tests, merged from all observers.
- Type label moved into each question's heading badge (e.g., `_(频次 9 · Coding · 60min 店面)_`).

**Front-loading the frequency table is non-negotiable.** Everything else is detail; the table is the leverage.

### Step 4: Sanity-check before reporting done

Before telling the user "done," verify:

- [ ] Frequency table exists and Top 1 makes sense (not "其它" or a fragment).
- [ ] At least the top 3 questions have specific题面 (not "implement a stateful system").
- [ ] Each top-frequency question has source links to ≥3 threads (proves frequency claim).
- [ ] Total handbook 题数 ≪ total post count (e.g., 162 posts → ~30–60 canonical questions; if 1:1, aggregation failed).
- [ ] If you aggregated `N` posts but only `M < N/3` show up in the handbook source links, you dropped data — investigate.

## Adding a new company

1. Add an entry to `career/interview-question-scout/config/interview-question-scout.yaml`:
   ```yaml
   - name: Anthropic
     slug: anthropic
     enabled: true
     obsidian_file: Career/Company/Anthropic/0 interview experience.md
   ```
   (`slug` matches the URL path: `jobs.1point3acres.com/companies/<slug>/interview`. Output goes to `Career/Company/<Name>/0 interview experience.md` — co-located with other per-company notes.)
2. Run a single scout cycle to seed: `python3 scripts/run_interview_question_scout.py --company anthropic --limit 20 --verbose`.
3. Then run the handbook aggregator with `--company anthropic`.

## Models and budget

For analysis/synthesis (this skill's core work), prefer a strong model — quality matters more than cost. The script reads `OPENAI_API_KEY` and `OPENAI_BASE_URL` (any OpenAI-compatible endpoint) from `~/.hermes/.env` → project `.env` → `os.environ`.

For lightweight extraction of a single post, `claude-sonnet-4.6` is acceptable. Never use a model your endpoint does not support.

## Known pitfalls (operational)

- **`job_scout_lib` import path**: The lib lives at `career/job-scout/scripts/job_scout_lib.py` (not `job-scout/scripts/`). If the import fails with `ModuleNotFoundError`, check that `interview_question_scout_lib.py` line 20 points to the correct path.
- **API key resolution**: The script reads `OPENAI_API_KEY` from `~/.hermes/.env` → project `.env` → `os.environ`. In cron/daemon contexts, `os.environ` won't have shell-exported vars — the `.env` files must contain the key.
- **大米 paywall (积分=0)**: The user's 1point3acres account currently has 0 积分. Posts requiring 188+ 积分 will show only partial visible content (OP preview before the paywall + all reply text). The `locked_by_dami` flag in thread data indicates this. Even with partial content, reply threads often contain useful signal (interview details, follow-up questions, tips). The script should still process locked posts — extract what's visible and mark `locked: true` in metadata.
- **`--deep` mode runtime**: Paginating 40+ listing pages takes ~3-4 minutes (4s render + 2.5s click per page). Then fetching each thread body adds ~5s per thread. For 12 months of a popular company (300+ threads), total runtime is **30+ minutes**. Always run with `background=true` + `notify_on_complete=true` for `--deep` mode.
- **CDP `_new_page` pitfall**: `fetch_company_list_paginated` keeps one page open for the entire pagination loop (avoids losing SPA state). Individual thread fetches open/close their own pages.
- **iCloud `dataless` eviction (errno 11)**: Obsidian vault files on iCloud Drive get evicted to cloud-only (`compressed,dataless` flags) when not accessed for a while and Obsidian is not running. Reading/writing these files raises `OSError: [Errno 11] Resource deadlock avoided`. Fixed in `interview_question_scout_lib.py`: `_ensure_icloud_downloaded()` calls `brctl download` and polls `stat` flags until `SF_DATALESS (0x40000000)` clears before any read/write. The `_write_with_retry()` and `read_text_with_icloud_fallback()` functions both handle this transparently.

## Guardrails

- **Don't fabricate questions.** If aggregation produces a "题面" that has no support in the source posts, drop it. Better an honest "未注明" than invented detail.
- **Source links per question are mandatory.** They let the user verify and dig deeper. No links = the user has to re-scrape.
- **Don't mix companies.** A handbook is per-company. If the user asks for "MLE 面经across companies," produce per-company handbooks and a separate cross-cut summary.
- **Respect anti-scrape.** If you're live-scraping: ≥2.5s between thread reads, ≥2s between page clicks, randomize. Use the user's logged-in Chrome via CDP (`ai-chrome` skill); never headless-launch a fresh browser.
