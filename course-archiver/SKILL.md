---
name: course-archiver
description: Use whenever the user wants to capture an online course or interview question bank into local Markdown files — especially paid content behind a login. Triggers on phrases like "把这门课保存下来", "archive this course", "复刻课程到 MD", "把极客时间/得到/Coursera/Udemy 这门课下载下来", "save this course offline", "mirror the course content", "snapshot all chapters", "archive hack2hire questions", "save interview questions", or any request to back up, copy, or extract course chapters / interview problems from a logged-in learning platform. Drives the user's live Chrome via the ai-chrome skill to walk through the TOC, pull each section's text + headings + code blocks + images, and write a clean directory tree of MD files into the user's Obsidian Career/Course vault. Also covers interview question platforms like hack2hire.com — see `references/hack2hire.md` for platform-specific extraction details.
---

# course-archiver

Capture a whole online course — TOC, every section's text, code blocks, and images — into a tidy directory of Markdown files inside the user's Obsidian vault. Built on top of the `ai-chrome` skill for live, logged-in browser access.

## When to use

The user has paid for or enrolled in a course on some platform (Coursera, Udemy, 极客时间, 得到, edX, Pluralsight, internal training, etc.), is logged in via their normal Chrome, and wants a local, searchable, Obsidian-friendly copy of the content. The course's text and code is what they want — not the videos.

Don't use this for:

- Public docs you can `WebFetch` anonymously — it's faster and cleaner.
- Video extraction. Modern course platforms gate videos with DRM/HLS-with-rotating-keys, which is out of scope and often a TOS violation. We capture transcripts/notes only.
- Interactive coding exercises whose content lives only in iframe sandboxes (we'll save what's visible on the parent page and flag the rest).

## What you're producing

A directory tree like this, anchored at the user's Obsidian vault. **Default to the chaptered layout** whenever the course's TOC has more than one logical section group (HelloInterview's 5 sidebar groups, Coursera weeks, multi-module Udemy courses, etc.). Only fall through to a flat layout for courses with a genuinely linear single-list curriculum (most ByteByteGo Type A linear courses, single-week tutorials). When in doubt, prefer chaptered — it's easier to flatten later than to re-split.

When the user later browses in Obsidian, a flat 60-file root is hostile; 5 nested folders is navigable. Your job is to read the source platform's actual structure, not to optimize for your own write throughput. `make_index.py` handles both layouts on the read side.

```
~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Career/Course/
└── <platform>/
    └── <course-slug>/
        ├── README.md                       # course overview + TOC with links
        ├── _toc.json                       # canonical chapter+section list
        ├── _progress.jsonl                 # one row per archived section
        │
        │  # Flat layout (no chapters):
        ├── 00-<section-slug>.md
        ├── 01-<section-slug>.md
        ├── ...
        │
        │  # OR — chaptered layout:
        ├── 01-<chapter-slug>/
        │   ├── 01-<section-slug>.md
        │   ├── 02-<section-slug>.md
        │   └── ...
        ├── 02-<chapter-slug>/
        │
        └── images/                         # all downloaded images, referenced
            ├── 01-<section-slug>-fig1.png  #   relatively from the section MDs
            └── ...
```

Each section MD has Obsidian-friendly frontmatter and content:

```markdown
---
course: <course title>
platform: <platform name>
chapter: <chapter title>
section: <section title>
source_url: https://...
archived_at: 2026-04-26
---

# <section title>

…body, with `inline code`, code blocks (language-tagged), images
referenced as `![alt](../images/01-section-fig1.png)`, etc.
```

The `README.md` at the root has the full TOC with `[[...]]` Obsidian wikilinks (or relative links if the user prefers — ask if unsure).

## Prerequisites

- **Chrome running with CDP on localhost:9222** (the standard Hermes `browser.cdp_url` config). Hermes browser tools (`browser_navigate`, `browser_console`, `browser_cdp`) all use this connection. No separate `ensure_chrome.sh` needed — Hermes manages the CDP connection.
- **The user must already be logged into the course platform** in that Chrome. If you read the TOC page and see a login wall instead of chapter list, stop and tell the user: "Looks like you're not logged in — please log in and tell me to continue."
- **No Playwright needed for extraction.** The primary extraction path uses Hermes `browser_navigate` + `browser_console` which talk directly to the real Chrome via CDP. Playwright is NOT recommended — it suffers from CDP connection hangs when Chrome has many tabs open, and headless Playwright gets blocked by bot detection on many platforms.

## The workflow

There are four phases. Don't skip phases — each one is the foundation for the next.

### Phase 1 — Discover the course and its TOC

1. Run `ai-chrome`'s `ensure_chrome.sh` and `list_tabs.py`. Identify which tab is on the course's TOC / curriculum / syllabus page.
2. Run `ai-chrome`'s `read_page.py --url-substring <best-match>` to pull the page's `inner_text`, screenshot, and a 20k-char `html_preview`. If the structure is non-trivial (most SPAs are), pass `--full-html` to get the full DOM.
3. From that DOM, extract:
   - **Course title** (page `<h1>`, `<title>`, or biggest visible heading).
   - **Platform name** (from the URL host — `coursera.org` → "Coursera", `time.geekbang.org` → "极客时间", etc. Prefer human-readable names).
   - **Chapter and section list**, each with: chapter title, section title, section URL. Most platforms render this as nested `<ul>` / `<li>` with `<a href>` per section. Some lazy-load; if so, ask the user to scroll the TOC fully open and re-run `read_page.py`.
4. Build a Python list of dicts `[{chapter, chapter_idx, section, section_idx, url}, ...]`. Save this to `<output_root>/<platform>/<course-slug>/_toc.json` so a re-run can pick up where it left off.

   **CRITICAL `_toc.json` schema**: `extract_section.py` reads `meta["course"]` and `meta["host"]` from this file. The top-level JSON **must** include these exact keys:
   ```json
   {
     "course": "Course Title Here",
     "platform": "PlatformName",
     "course_slug": "...",
     "host": "bytebytego.com",
     "toc_url": "https://...",
     "sections": [...]
   }
   ```
   Using `course_title` instead of `course` will cause a `KeyError: 'course'` at extraction time and silently fail every section.
5. **Show the user the parsed TOC and ask them to confirm** before downloading anything. Print a compact view: chapter count, total sections, first three section titles, last three. Ask: "Looks right? Or do I need to scroll the TOC and try again?" Don't proceed without explicit confirmation — getting the TOC wrong wastes a lot of subsequent work.

The platform → name mapping and slug-to-pretty-name choices are heuristic. Common ones:

| URL host substring  | Platform name |
|---------------------|---------------|
| `coursera.org`      | Coursera      |
| `udemy.com`         | Udemy         |
| `time.geekbang.org` | 极客时间       |
| `dedao.cn` / `igetget` | 得到       |
| `edx.org`           | edX           |
| `pluralsight.com`   | Pluralsight   |
| `xiaoe-tech.com`    | 小鹅通        |

If the host isn't on the list, just title-case the second-level domain — fine for a folder name.

### Phase 2 — Walk the course, section by section

Loop over the TOC. For each section, do these two steps using **Hermes browser tools** (the default and recommended approach):

**Step 1 — Navigate** to the section URL:

```python
browser_navigate(url=section_url)
```

This navigates the real Chrome tab. Wait for the page to load — `browser_navigate` returns a snapshot automatically.

**Step 2 — Extract content** via `browser_console`:

```python
browser_console(expression=EXTRACT_JS)
```

Where `EXTRACT_JS` is a synchronous IIFE that:
1. Finds `document.querySelector('article')` (or the main content container)
2. Walks child elements (`h1-h6`, `p`, `pre`, `li`, `img`, `blockquote`, `table`)
3. Returns a structured JSON array of `{t: type, ...data}` elements

Then convert the JSON to markdown and save via `write_file`.

**Why Hermes browser tools, not Playwright or curl:**
- **Hermes browser tools use the real Chrome** via CDP — same cookies, same session, same anti-bot fingerprint as the user's normal browsing. No bot detection issues.
- **Playwright headless** gets blocked by bot detection on most platforms, and `connect_over_cdp` hangs when Chrome has many open tabs.
- **curl** only works for SSR pages — most modern course platforms are SPAs where content loads client-side.
- **browser_console** returns JSON results directly with no size issues (tested up to 120KB).

**Extraction JS template** (adapt selectors per platform):
```javascript
(function() {
  const article = document.querySelector('article');
  if (!article) return JSON.stringify({error: 'no article'});
  const elements = [];
  for (const el of article.querySelectorAll('h1,h2,h3,h4,h5,h6,p,pre,li,img,blockquote,table')) {
    const tag = el.tagName.toLowerCase();
    if (['h1','h2','h3','h4','h5','h6'].includes(tag))
      elements.push({t:'h', l:parseInt(tag[1]), x:el.textContent.trim()});
    else if (tag === 'p') { const t = el.textContent.trim(); if (t) elements.push({t:'p', x:t}); }
    else if (tag === 'pre') elements.push({t:'code', x:el.textContent.trim()});
    else if (tag === 'li') elements.push({t:'li', x:el.textContent.trim()});
    else if (tag === 'img') { const s=el.getAttribute('src')||''; const a=el.getAttribute('alt')||''; if(s&&s.length>10) elements.push({t:'img',s:s,a:a}); }
    else if (tag === 'blockquote') elements.push({t:'bq', x:el.textContent.trim()});
    else if (tag === 'table') { const rows=[]; el.querySelectorAll('tr').forEach(tr=>{const cells=[];tr.querySelectorAll('td,th').forEach(td=>cells.push(td.textContent.trim()));if(cells.length)rows.push(cells);}); if(rows.length) elements.push({t:'tbl',rows:rows}); }
  }
  return JSON.stringify({url: window.location.pathname, len: article.innerText.length, elements: elements});
})()
```

**Fallback — legacy `extract_section.py`**: The bundled script still works if you prefer, but it depends on ai-chrome's `read_page.py` + Playwright which can hang. Use it only if the Hermes browser approach fails for a specific platform.

**Producing chaptered output with `extract_section.py`**: pass `--chapter-dir <slug>` (e.g. `01-in-a-hurry`) and `--in-chapter-idx <N>` (1-based, per chapter). The script will write to `<course-root>/<chapter-dir>/<NN>-<slug>.md`, emit `../images/<file>` refs (so the central `images/` dir at course root still works), add `chapter:` to the frontmatter, and use atomic write (`.tmp` + `os.replace`) which prevents iCloud Drive from creating ` 2.md` conflict copies on retry. Without `--chapter-dir`, output is flat at `<course-root>` (legacy behavior). The websocket batch script in this skill should use the same chapter-dir convention when writing the MDs — see the iCloud collision note in Failure modes.

The defaults are tuned for **Next.js course platforms** (ByteByteGo and similar). For a different platform, you may need to override:

- `--body-start-regex` — anchors the *end* of nav/sidebar text in `inner_text`. Default: `\nThe Learning Continues\nMy Courses\n` (ByteByteGo's TOC sentinel). For other platforms, find a unique line that always sits between the sidebar and the article, and pass it in.
- `--body-end-substring` — anchors the start of the page footer. Default: `Mark as Complete`. Pick whatever footer button text the platform uses.
- `--figure-regex` — pattern for the lone-line marker that indicates "image goes here". Default: `^Figure\s+(\d+)$`.

When you're on an unfamiliar platform, run `read_page.py` once and **eyeball `inner_text`** to find the right boundary markers before processing all sections — it's a five-minute investment that saves 30+ runs of bad output.

#### What `extract_section.py` actually does (so you know what to override)

- **Headings**: maps `<h2>`/`<h3>`/`<h4>` text → level, then promotes any matching body line to that heading level. The section title becomes `#`.
- **Code blocks**: pulls `<pre>` contents, decodes HTML entities, guesses the language from cheap heuristics (JSON, Python, JS, Java, C++), and emits fenced blocks. If guessing fails, the fence has no language — better than guessing wrong.
- **Images**: matches every `<img src>`. If `src` is a Next.js image proxy (`/_next/image?url=…`), it URL-decodes the inner path so we download the canonical asset, not a re-encoded version. Filters out non-content paths (only keeps URLs containing `/images/courses/`, `/images/lessons/`, or `course` in the path). Filenames are `<NN-section-slug>-fig<K>.<ext>`. Alt text is trimmed to roughly the first sentence (≤60 chars + tolerance) so MD lines stay readable.
- **Tables**: detects runs of consecutive lines with ≥2 tab characters per line and short cell content, treats the first row as a header, and renders `| col1 | col2 |` Markdown tables. ByteByteGo and most Next.js platforms don't render `<table>` for simple data and use tab-separated text — this is what catches them.
- **Inline embeds (videos, iframes, quizzes)**: not captured by default. They render in `inner_text` as either a stray URL or nothing. If a section is heavy on embeds, flag it in the skip log so the user knows to revisit manually.

#### When you should NOT use `extract_section.py`

- The platform actually renders quiz answers / interactive content into the DOM that the user wants captured. Then have Claude do the extraction by hand from the screenshot + `inner_text`. Be explicit about this in the report.
- The section is paid-for video only with no transcript text. Skip with a `> *Video-only section — no transcript available.*` placeholder in the MD.

Slugify rules (built into the script): lowercase, ASCII-fold, replace whitespace + non-alphanumerics with `-`, collapse runs, cap at 60 chars. Chinese characters are preserved as-is — Obsidian and macOS handle them fine.

### Phase 2.5 — Cleanup and normalization

After extraction, normalize the output directory:
1. **Remove duplicates** — if earlier failed runs left duplicate files (e.g. `01-introduction.md` AND `01-p0-c2-introduction.md`), keep the larger/newer version and rename to the clean form.
2. **Remove helper scripts** — delete `_archive_batch.py`, `_extract_js.py`, `_section_list.py` and similar temporary files from the output directory.
3. **Create `_toc.json`** — must have BOTH `course` and `course_title` keys for compatibility:
   ```json
   {
     "course": "Course Title",
     "course_title": "Course Title",
     "chapters": [{"num": "00", "file": "00-filename.md", "title": "Chapter Title"}, ...]
   }
   ```
4. **Verify file count** — ensure the number of `.md` files matches the expected chapter count from the TOC.

### Phase 3 — Build the course README

After the loop, generate `<course>/README.md` using `scripts/make_index.py`:

```bash
python3 scripts/make_index.py <course-root>
```

This walks the directory, reads each section MD's frontmatter, and emits:

```markdown
---
course: <title>
platform: <platform>
source_url: <toc-url>
archived_at: 2026-04-26
sections_total: 47
sections_archived: 47
---

# <Course title>

> Archived from [<platform>](<toc-url>) on 2026-04-26.

## Table of contents

### 1. <Chapter 1 title>
- [[01-chapter-slug/01-section-slug|1.1 Section title]]
- [[01-chapter-slug/02-section-slug|1.2 Section title]]
…

### 2. <Chapter 2 title>
…
```

It uses `[[...]]` Obsidian wikilinks by default. Pass `--relative-links` to emit `[title](path.md)` instead.

### Phase 4 — Report

Tell the user concretely:
- How many sections succeeded vs. were skipped (and *why* — read `_progress.jsonl`).
- Where the directory is on disk.
- Any sections that need a manual revisit (e.g. quizzes whose answers needed clicking).

If it's a long course (>20 sections), batch progress updates as you go so the user knows it's not stuck. The default flow is "go straight through, report at the end" — but a single sentence at every ~5 sections ("done with Chapter 3, 12/47") is usually appreciated.

## Output path resolution

Default: `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Career/Course/<platform>/<course-slug>/`.

Override knobs (in priority order):
1. The user explicitly says a path in the request (e.g. "save it to ~/notes/this-course"). Use that.
2. A `COURSE_ARCHIVER_ROOT` env var, if set, overrides the default Obsidian root. Useful when iCloud is unavailable.
3. Otherwise the default.

If the resolved root doesn't exist, create it (`mkdir -p`). If the leaf course directory already exists and isn't empty, ask the user: "There's already content at `<path>` — overwrite, merge (skip already-archived sections), or abort?" Default to "merge" if they don't answer, since restarting a long run is painful.

## Failure modes

- **TOC page returns empty `inner_text`** — page renders into shadow DOM. Fall back to reading the screenshot (`Read` tool on `/tmp/ai_chrome_snap.png`); extract the TOC visually.
- **Login wall on a section URL** — session expired mid-archive. Stop, tell the user, ask them to log back in and run the skill again with the same target. The `_progress.jsonl` lets you skip already-archived sections.
- **Section URL navigates to a generic course landing page** (not the section) — the platform doesn't expose section-level URLs. Fall back to manual mode: ask the user to open the section themselves; then read + extract; advance to the next when they say "next". Document this in the skip log so the run is reproducible.
- **Image download fails** — log the URL, leave the MD reference pointing to a placeholder file or the original URL, keep going. Don't abort the whole run for one image.
- **Rate limiting from the platform** — sleep + retry once, then skip with a `429` note. Don't hammer.
- **Playwright `connect_over_cdp` hangs for 180s** — This is a known issue when Chrome has many tabs open. **Don't use Playwright at all** — use Hermes browser tools (`browser_navigate` + `browser_console`) instead. They use the same CDP connection but are managed by Hermes and don't suffer from this hang.
  - If even Hermes browser tools time out, it's likely Chrome itself is overloaded. Close some tabs (especially ad-heavy ones like 1point3acres) and retry.
- **Re-running a section leaves orphan images on disk** — image filenames are derived from a slug that may differ between runs (heading text changes, slug truncation, etc.), so a re-extract can produce new filenames while the old ones linger. After any re-run, sweep orphans: `comm -23 <(ls images/) <(grep -ho 'images/[^)]\+' *.md | sed 's|images/||' | sort -u)`.
- **iCloud Drive ` 2.md` conflict copies on retry** — when the output dir is in iCloud (the default Obsidian Career/Course path is), writing to a path that already exists from an earlier partial run can produce `<file> 2.md` (and stale-content `<file>.md`) sync conflict copies. **Always use atomic write**: write to `<dest>.tmp` first, then `os.replace(<tmp>, <dest>)`. The patched `extract_section.py` does this. The websocket batch script in this SKILL.md MUST do the same — never write directly to the iCloud path with `open(path, 'w')` in a retry loop. If you find yourself with conflict copies anyway, the heuristic that worked once: in the backup, `<file> 2.md` was the partial-but-image-rich pass; `<file>.md` was the full-text but URL-only pass. Recovery means merging text from `.md` with image refs from ` 2.md` — non-trivial, prevent it instead.
- **Some figure-marker lines are prose, not markers** — "Figure 5 shows X" is a marker line; "Returning to Figure 5, we now …" looks similar but is a body reference. Mitigation: `extract_section.py` only treats the *first* occurrence of `Figure N` per page as a marker; subsequent occurrences stay as plain text. If a page genuinely shows the same figure twice, the second one falls through to a prose line — acceptable.

### Pacing — don't get throttled or banned

Course platforms will silently rate-limit (or in rare cases ban) IPs that hit too many sections back-to-back. The user's logged-in session is *valuable*; treat it that way:

- **Sleep 8–15 seconds between sections** by default. Randomize the delay so the access pattern doesn't look like a script (e.g. `random.uniform(8, 15)`). Image downloads inside a single section also run serially through `fetch_resource.py`, which gives natural ~50–200ms gaps per request.
- For long courses (>20 sections), bump the inter-section sleep to 12–20 seconds.
- If you see a `429`, back off exponentially (30s, 90s, 240s) before retrying. After three failures, stop the whole run and tell the user — keep the partial archive intact.
- Don't parallelize section fetches. The whole skill is sequential by design.

### Batch archival pattern — Hermes browser sequential loop

For courses with many sections, the recommended pattern is a **sequential loop using `browser_navigate` + `browser_console`** directly from the agent:

```
for each section in _toc.json:
  1. browser_navigate(url=section_url)      # navigates real Chrome
  2. browser_console(expression=EXTRACT_JS)  # extracts structured JSON
  3. Convert JSON → markdown in execute_code or write_file
  4. Sleep naturally (tool call overhead provides ~2-5s gap)
```

**Key advantages over the old Playwright-based approach:**
- No CDP connection hangs — Hermes manages the CDP lifecycle
- No bot detection issues — uses the real Chrome with user's fingerprint
- No Playwright dependency at all
- Works even with 30+ Chrome tabs open (Playwright would hang)

**For very long courses (20+ sections):** Budget 2-3 tool calls per section (navigate + extract + save). With 18 sections that's ~54 calls — fits within a single conversation turn. If context gets too large, write a Python helper that converts JSON elements to markdown, and batch-save via `execute_code`.

**Fastest approach — websocket batch script via `execute_code`:**
When `browser_navigate` times out (common with many Chrome tabs open), write a Python script that connects directly to Chrome's CDP websocket and processes all chapters in a loop:

```python
import asyncio, json, websockets, os

# 1. Find the tab: curl -s http://localhost:9222/json | python3 -c "import sys,json; [print(t['id'],t['url']) for t in json.load(sys.stdin) if 'bytebytego' in t['url']]"
WS_URL = "ws://localhost:9222/devtools/page/<TAB_ID>"

async def main():
    async with websockets.connect(WS_URL, max_size=10_000_000) as ws:
        await ws.send(json.dumps({"id": 0, "method": "Page.enable"}))
        await ws.recv()
        
        for num, slug, fname in CHAPTERS:
            # Navigate
            await ws.send(json.dumps({"id": n, "method": "Page.navigate", "params": {"url": url}}))
            await ws.recv()
            await asyncio.sleep(4)  # wait for SPA to render
            
            # Drain pending events
            try:
                while True: await asyncio.wait_for(ws.recv(), timeout=0.5)
            except: pass
            
            # Extract
            await ws.send(json.dumps({"id": n+1, "method": "Runtime.evaluate", 
                "params": {"expression": EXTRACT_JS, "returnByValue": True}}))
            # Get response (skip events)
            for _ in range(20):
                resp = json.loads(await ws.recv())
                if resp.get("id") == n+1: break
            
            # Parse and save
            items = json.loads(resp["result"]["result"]["value"])
            md = json_to_markdown(items)
            os.makedirs("/tmp/chapters", exist_ok=True)
            with open(f"/tmp/chapters/{num}-{fname}.md", 'w') as f:
                f.write(md)

asyncio.run(main())
```

Key details for the websocket batch approach:
- Get tab ID from `curl -s http://localhost:9222/json` — find the course tab by URL substring
- `pip install websockets` if needed
- Write to `/tmp/` first, then copy to iCloud path (iCloud paths can fail with bare `open()` from scripts)
- `max_size=10_000_000` — some pages return huge JSON (70KB+)
- `asyncio.sleep(4)` between navigate and extract — SPAs need time to hydrate
- Drain events after sleep to clear the websocket buffer before reading eval response
- Use `await asyncio.sleep(2)` between chapters as pacing
- This approach processes 11 chapters in ~70 seconds total vs browser_navigate which can timeout

**When to use which approach:**
- `browser_navigate` + `browser_console`: Works for ≤5 pages, or when browser_navigate isn't timing out
- `browser_cdp` (Page.navigate + Runtime.evaluate): Works for medium batches, reliable single-page extraction
- Websocket batch script: Best for 10+ pages — fastest, most reliable, no timeout issues

**DO NOT use these approaches** (they all failed in practice):
- `curl` — most course platforms are SPAs; no article content in SSR HTML
- Playwright headless (`p.chromium.launch(headless=True)`) — gets blocked by bot detection after 4-5 pages
- Playwright `connect_over_cdp` — hangs for 180s when Chrome has many tabs
- `delegate_task` with `toolsets: ["browser"]` — uses cloud browser, not local Chrome

### HelloInterview — MUI SPA with sidebar + content grid

**Mandatory chaptered output for HelloInterview** — the sidebar has 5 distinct groups, each a meaningful chapter. Use these exact directory names so the order matches the website nav and the README TOC stays sensible:

```
hellointerview/system-design/
├── 01-in-a-hurry/             # 7 sections
├── 02-core-concepts/          # 9 sections
├── 03-question-breakdowns/    # ~28 sections (grows over time)
├── 04-patterns/               # 7 sections
├── 05-deep-dives/             # 12 sections
├── images/                    # all figures, referenced as ../images/<file>
├── _toc.json
└── README.md
```

When you call `extract_section.py`, pass `--chapter-dir 01-in-a-hurry` (etc.) and `--in-chapter-idx <N>` so each chapter restarts at 01. **Do not** dump everything flat at the course root — that's exactly the failure mode the user reported when the skill first shipped. If you're using the websocket batch script instead, replicate this layout in the script's write path and emit `../images/<file>` refs.

HelloInterview (`hellointerview.com`) is a MUI-based React SPA. Key structural details:

**DOM hierarchy to find content:**
```
main.flex-1 > .flex.flex-col > .px-4 (first one)
  > .grid.grid-cols-7
    > .col-span-7.w-full.max-w-4xl > .relative > div:first-child  ← ARTICLE CONTENT
```

The selector `document.querySelector('.col-span-7.w-full.max-w-4xl > .relative > div:first-child')` gets the article. There is NO `<article>` or `.prose` element.

**What to skip in extraction:**
- Elements with `className` containing: `chrome`, `MuiDrawer`, `mt-16` (comments section), `underbody`, `flex-row flex-wrap w-full justify-between` (nav buttons)
- `<audio>` elements (podcast player)
- Short divs containing "Schedule a mock interview" or "Try This Problem Yourself" (CTAs)
- SVGs are all UI icons (24x24, video player controls) — NOT content diagrams. Only extract `<img>` tags.

**Comments section is in the DOM** — previous extraction incorrectly captured user comments (avatars, discussion text, vote counts). The comments live in a `.mt-16` div after the article. Skipping this div typically removes 30-50% of apparent content but results in HIGHER quality output.

**Sidebar navigation links** have `href` attributes and follow pattern:
- `/learn/system-design/{section}/{slug}` (sections: `in-a-hurry`, `core-concepts`, `problem-breakdowns`, `patterns`, `deep-dives`)
- No login required — all content is public
- All sidebar links are visible without expanding/clicking (unlike ByteByteGo's collapsible menus)

**"Question Breakdowns" listing page** (`/in-a-hurry/problem-breakdowns`) is a grid/card layout, not a content page. Generate a simple index file for it rather than trying to extract article content.

**Extraction JS for HelloInterview** — full working version at `/tmp/hellointerview_extract.py`. Key: use `typeof child.className === 'string'` check because SVG elements have `SVGAnimatedString` className which doesn't have `.includes()`.

**Two extraction pitfalls to avoid (learned the hard way on the first run):**

1. **Emoji callouts get dropped.** HelloInterview wraps "What is X?" intros and inline tips inside `<details>` or custom callout components. The default `querySelectorAll('h1,...,blockquote,table')` walker either misses the wrapper entirely or only captures the bare link text inside a `<strong>` element, leaving orphan single words in the output (e.g. just `Stripe` where the original page had `**📸 What is [Stripe](https://www.stripe.com/)?**` followed by a paragraph). Before extracting, expand all collapsed `<details>` and capture text from all `<div>` descendants that contain emoji prefixes:

   ```javascript
   // Run before the main element walker:
   document.querySelectorAll('details:not([open])').forEach(d => d.open = true);
   // In the walker, treat any element whose textContent starts with an emoji
   // followed by structured text as a callout block; emit the full innerHTML
   // converted to MD, not just textContent of children.
   const EMOJI_PREFIX = /^(\u{1F4F8}|\u{1F6CD}|\u{1F4A1}|\u{2699}|\u{1F3AC}|\u{1F4DD}|\u{1F50D})️?/u;
   ```

2. **Video-player UI controls leak into output.** The HelloInterview video player renders `Chapters`, `Settings`, `AirPlay`, `Closed-Captions On`, `Google Cast`, `Enter PiP`, `Enter Fullscreen`, and `Video Content` as regular text nodes that the walker picks up as standalone single-line paragraphs. Filter these out either at extraction time (skip elements inside the video player container — usually a `<div>` with `aria-label="Video Player"` or class `*VideoPlayer*`) or strip them in post-processing. The current scripts do not do this; the cleanup script `/tmp/cleanup_noise_lines.py` from the recovery run is a good reference for the noise terms list.

**Websocket batch script works great** — 63 pages in ~5 minutes. Use 4s sleep between pages (the site is fast). No rate limiting observed.

### ByteByteGo SPA navigation — two course layout types

ByteByteGo is a Next.js SPA with **two distinct sidebar layouts**:

#### Type A — Linear curriculum (e.g. `tech-resume`, `system-design-101`)

- Sidebar uses `[role=menuitem]` items in a flat list
- URL pattern: `/courses/{course}/{section-slug}` (2-level path)
- Slugs DON'T always match titles — e.g. "Design a Movie Ticket Booking System" → `movie-ticket-booking-system`, Conclusion → `p4-c0-conclusion` (not `p3-c5-conclusion`)
- **Must discover URLs by clicking sidebar items**, never guess from titles

Discovery approach:
1. Navigate to any section of the course first.
2. Use `browser_cdp` `Runtime.evaluate` to click through all `[role=menuitem]` sidebar items sequentially, capturing the resulting `window.location.href` after each click.
3. Build `_toc.json` from these discovered URLs.

Example JS snippet:
```javascript
new Promise(resolve => {
  const results = [];
  const items = document.querySelectorAll('[role=menuitem]');
  let i = 0;
  function next() {
    if (i >= items.length) { resolve(results); return; }
    items[i].click();
    setTimeout(() => {
      results.push({menu_idx: i, url: window.location.href});
      i++; next();
    }, 800);
  }
  next();
})
```

#### Type B — Coding patterns / problem sets (e.g. `coding-patterns`)

- Sidebar uses **Ant Design `ant-menu`** with collapsible `ant-menu-submenu` sections
- URL pattern: `/courses/{course}/{section-slug}/{lesson-slug}` (3-level path)
- Section slugs = kebab-case of section name (e.g. "Two Pointers" → `two-pointers`)
- Lesson slugs = `slugify(title)` (lowercase, non-alphanumeric→dash). Special chars: "0/1 Knapsack" → `01-knapsack`
- Menu items have NO `<a href>` — they use click handlers for client-side routing
- **120 lessons across 19 sections** — use the websocket batch script (see below)

Discovery approach for Type B:
1. Expand all collapsed submenus first:
```javascript
document.querySelectorAll('li.ant-menu-submenu:not(.ant-menu-submenu-open) .ant-menu-submenu-title')
  .forEach(el => el.click());
```
2. Wait 2s, then read all `li.ant-menu-item` text + parent section titles
3. Click ONE item, observe resulting URL to confirm slug pattern
4. Generate all URLs programmatically from section/lesson titles (slugify is predictable for this layout)

**Section-based output directory structure for Type B:**
```
coding-patterns/
├── 01-two-pointers/
│   ├── 01-pair-with-target-sum.md
│   ├── 02-find-non-duplicate-instances.md
│   └── ...
├── 02-hash-maps-and-sets/
│   └── ...
└── _toc.json
```

#### Content extraction (both types)

Use `browser_cdp` `Runtime.evaluate` or `browser_console` with the structured extraction JS (see Phase 2). The extraction JS works identically for both layouts — it targets `article` or the main content container.

**Key selector note**: ByteByteGo wraps main content in `article`. If `article` is absent on some pages, fall back to `document.querySelector('main')` or `.prose`.

## Public sites (no login required)

Even for public sites, **always default to Hermes browser tools** (`browser_navigate` + `browser_console`). The reasons:

1. **Most modern course sites are SPAs** — content loads client-side via JS, so `curl` gets an empty shell
2. **The real Chrome has no bot detection issues** — it's a normal browser with normal fingerprints
3. **It's the same workflow as paywalled sites** — no need to switch approaches

### The standard approach (works for ALL sites)

Same as Phase 2 above:
1. `browser_navigate(url=section_url)` — navigates real Chrome
2. `browser_console(expression=EXTRACT_JS)` — extracts structured JSON from DOM
3. Convert JSON → markdown → `write_file`

### When curl MIGHT work (rare, optional optimization)

Only consider curl if ALL of these are true:
- The site uses SSR (server-side rendering) — test with `curl -sL <url> | grep '<article'`
- The `<article>` tag contains full content in the initial HTML
- You're archiving 50+ pages and want maximum speed

Even then, many SSR sites have some pages that are client-rendered (e.g. Next.js `(no-mdx)` routes). Always verify a few pages before committing to curl.

### Image handling for public sites

Images on Next.js sites often use the `/_next/image?url=...` proxy. URL-decode the inner `url` parameter to get the canonical asset URL before downloading with curl.

## What this skill intentionally doesn't do

- **Mostly URL-driven navigation**, but some SPAs (e.g. ByteByteGo) require clicking sidebar menu items via `browser_cdp` `Runtime.evaluate` to discover correct URLs and trigger client-side routing. See "ByteByteGo SPA navigation" above. If a course truly requires "next lesson" button clicks with no per-section URLs at all, the skill flags it and asks the user to drive.
- **Doesn't archive videos.** See *When to use* above.
- **Doesn't try to be platform-aware.** Heuristic, DOM-and-LLM-driven extraction. Works on most platforms; might miss edge cases on weird ones. Better to ship "good across many" than "perfect on one."
- **Doesn't run unattended forever.** It's a one-shot. If you want recurring archival, schedule the prompt yourself with the `schedule` skill.

## Customization quickstart

Common things the user might ask for during a run:

| Ask                                | What to do                                                   |
|------------------------------------|--------------------------------------------------------------|
| "Skip code-only sections"          | Filter the TOC list before Phase 2 by section title regex.   |
| "Just chapters 3 and 5"            | Trim the TOC list before Phase 2; pass through Phase 3 too.  |
| "Use relative links, not wikilinks" | Pass `--relative-links` to `make_index.py`.                  |
| "Output somewhere else"            | Use the explicit path or `COURSE_ARCHIVER_ROOT` env var.     |
| "Don't download images, just link" | Skip `fetch_resource.py`; reference the original URLs in MD. |

## Platform-specific references

For platforms with complex DOM structures or non-standard extraction workflows,
see the `references/` directory:

| Platform | Reference file | Notes |
|----------|---------------|-------|
| hack2hire.com | `references/hack2hire.md` | Ant Design + CodeMirror SPA, coding questions + system design, scroll-and-collect extraction |

When adding a new platform, create a `references/<platform>.md` with DOM selectors,
extraction JS snippets, pagination details, and known pitfalls.
