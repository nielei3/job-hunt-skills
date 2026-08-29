#!/usr/bin/env python3
"""LeetCode "Last 6 Months" company-tagged question scraper + Java answer writer.

Workflow:
  1. Connect to the user's logged-in Chrome via CDP (port 9222) — LC Premium required.
  2. Navigate to https://leetcode.com/company/<slug>/?favoriteSlug=<slug>-six-months
  3. Scrape the question table (already sorted by frequency desc).
  4. For each question (resumable, idempotent):
       - Open /problems/<title-slug>/description/
       - Pull title, #, difficulty, statement, examples, constraints, tags
       - Call the LLM claude-opus-4.6 to write a full-depth Java answer
       - Write to <vault>/Career/Company/<Company>/LC-<idx>-<#>-<kebab>.md

Filename: LC-<idx>-<lc_number>-<title-kebab>.md  (idx zero-padded to 3 digits)

CLI:
    python3 scripts/lc_company_six_months.py --company Uber
    python3 scripts/lc_company_six_months.py --company "The Trade Desk" --limit 20
    python3 scripts/lc_company_six_months.py --company Uber --start-idx 25 --overwrite
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from datetime import date
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))

from interview_question_scout_lib import (  # noqa: E402
    cdp_probe,
    load_config,
    load_env,
    obsidian_file_path,
    _write_with_retry,
)

log = logging.getLogger("lc_company_six_months")


# ---------------- company slug resolution ----------------


def _kebab(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "-", text.strip())
    return text.lower()


def resolve_company(name: str, config: dict) -> tuple[str, str, str]:
    """Return (display_name, lc_slug, obsidian_dir_relative_to_vault).

    Look up the company in interview-question-scout.yaml. If found and the entry
    has `obsidian_file: Career/Company/X/0 interview experience.md`, use X as
    the Obsidian dir name. Otherwise default to `Career/Company/<name>`.

    The LC slug comes from `lc_slug` in the YAML if present; otherwise derived
    from the company name via kebab-case (Uber -> uber, The Trade Desk -> the-trade-desk).
    """
    name_lower = name.strip().lower()
    for entry in config.get("companies", []):
        if entry.get("name", "").strip().lower() == name_lower:
            display = entry["name"]
            lc_slug = entry.get("lc_slug") or _kebab(display)
            obsidian_rel = entry.get("obsidian_file", "")
            if obsidian_rel:
                # e.g. "Career/Company/Uber/0 interview experience.md" -> "Career/Company/Uber"
                obsidian_dir = str(Path(obsidian_rel).parent)
            else:
                obsidian_dir = f"Career/Company/{display}"
            return display, lc_slug, obsidian_dir
    # Not in config — fall back to kebab + Career/Company/<name>
    return name, _kebab(name), f"Career/Company/{name}"


# ---------------- LC scraping ----------------


SIX_MONTHS_URL = "https://leetcode.com/company/{slug}/?favoriteSlug={slug}-six-months"
PROBLEM_URL = "https://leetcode.com/problems/{title_slug}/description/"


DIFF_MAP = {"Easy": "Easy", "Med.": "Medium", "Medium": "Medium", "Hard": "Hard"}


def scrape_question_list(page, lc_slug: str, verbose: bool) -> list[dict]:
    """Return list of {idx, lc_number, title, title_slug, difficulty, acceptance, url}.

    LC's six-months list is already sorted by frequency descending — we use the
    1-based position as the frequency proxy (`idx`/`rank`). LC stopped rendering
    a numeric frequency in this view; only a bar visualization remains, which is
    not stably scrapeable. The user only cares about the ordering, not the raw
    number, so rank is sufficient.

    Pre-condition: page is on the company six-months page and LC Premium is
    unlocked.
    """
    url = SIX_MONTHS_URL.format(slug=lc_slug)
    log.info("navigating to %s", url)
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(7000)

    body_text = page.inner_text("body")[:3000]
    if "Premium" not in body_text:
        log.error("Page does not mention 'Premium' — likely not on LC. Check URL/slug.")
        return []
    if "Subscribe" in body_text and "Unlock" in body_text and "Premium" in body_text and "questions" not in body_text:
        log.error("LC Premium paywall detected. Log in to LeetCode Premium in this Chrome and retry.")
        return []

    # Only the company-list rows carry envType=company in their href; the daily-
    # question nav anchor is the only other /problems/ link and is filtered out.
    rows_data = page.evaluate(
        """() => {
            const out = [];
            const seen = new Set();
            const anchors = Array.from(document.querySelectorAll('a[href*="/problems/"]'));
            for (const a of anchors) {
                const href = a.getAttribute('href') || '';
                if (!href.includes('envType=company')) continue;
                const m = href.match(/\\/problems\\/([^\\/?#]+)/);
                if (!m) continue;
                const slug = m[1];
                if (seen.has(slug)) continue;
                seen.add(slug);
                out.push({
                    slug,
                    href,
                    anchor_text: a.innerText || '',
                });
            }
            return out;
        }"""
    )

    questions: list[dict] = []
    for idx, item in enumerate(rows_data, start=1):
        slug = item["slug"]
        # anchor_text is a newline-joined cell list, e.g.:
        #   "79. Word Search\n47.4%\n\nMed."
        parts = [p.strip() for p in (item["anchor_text"] or "").split("\n") if p.strip()]
        # parts[0] = "79. Word Search"
        # parts[-1] = "Med." / "Easy" / "Hard"
        # parts[1] = "47.4%" (acceptance)
        lc_num = 0
        title = slug.replace("-", " ").title()
        if parts:
            m = re.match(r"^\s*(\d+)\.\s*(.+?)\s*$", parts[0])
            if m:
                lc_num = int(m.group(1))
                title = m.group(2)
            else:
                title = parts[0]
        difficulty = "Unknown"
        for p in parts:
            if p in DIFF_MAP:
                difficulty = DIFF_MAP[p]
                break
        acceptance = None
        for p in parts:
            am = re.match(r"^(\d+\.?\d*)\s*%$", p)
            if am:
                acceptance = float(am.group(1))
                break
        questions.append(
            {
                "idx": idx,
                "rank": idx,
                "lc_number": lc_num,
                "title": title,
                "title_slug": slug,
                "difficulty": difficulty,
                "acceptance": acceptance,
                "url": f"https://leetcode.com/problems/{slug}/",
            }
        )
        if verbose:
            log.info(
                "  [%3d] LC#%-4s | %-6s | acc=%4s | %s",
                idx,
                lc_num or "?",
                difficulty,
                f"{acceptance}%" if acceptance is not None else " ?",
                title,
            )
    return questions


def scrape_question_detail(page, title_slug: str, verbose: bool = False) -> dict:
    """Open /problems/<slug>/description/ and pull statement, examples, constraints, tags."""
    url = PROBLEM_URL.format(title_slug=title_slug)
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(5500)
    detail = page.evaluate(
        """() => {
            // LC renders the problem statement inside a div with data-track-load="description_content"
            const desc = document.querySelector('[data-track-load="description_content"]');
            const fallback = document.querySelector('div.elfjS') || document.querySelector('.content__u3I1, .css-pre, .question-content__JfgR');
            const root = desc || fallback;
            // Title block
            const titleEl = document.querySelector('a[href^="/problems/"]') || document.querySelector('h1');
            const heading = (titleEl && titleEl.innerText) ? titleEl.innerText.trim() : document.title;
            // Difficulty (rendered with class containing one of easy/medium/hard)
            const diffEl = document.querySelector('div[class*="text-difficulty-"]');
            const difficulty = diffEl ? diffEl.innerText.trim() : '';
            // Tags
            const tags = Array.from(document.querySelectorAll('a[href^="/tag/"]')).map(a => a.innerText.trim()).filter(Boolean);
            return {
                heading,
                difficulty,
                tags,
                statement_html: root ? root.innerHTML : '',
                statement_text: root ? root.innerText : '',
                url: location.href,
            };
        }"""
    )
    if verbose:
        log.info("  detail: %s | %d chars", detail.get("heading", "?")[:80], len(detail.get("statement_text") or ""))
    return detail


# ---------------- LLM answer generation ----------------


JAVA_ANSWER_PROMPT = """You are writing an interview-prep answer file for a LeetCode problem. The candidate uses Java exclusively. Output PURE MARKDOWN — no preamble, no apologetic framing, no trailing commentary.

# Problem context
- Title: {title}
- LeetCode #: {lc_num}
- Difficulty: {difficulty}
- URL: {url}
- Tags: {tags}

# Problem statement (copied verbatim from LeetCode)
{statement}

# Output format (follow this structure exactly)

Start with this frontmatter block (YAML):

```
---
company: {company}
platform: LeetCode
list: Six Months (sorted by frequency)
rank: {rank}
problem_number: {lc_num}
question: {title}
difficulty: {difficulty}
tags: {tags}
source_url: {url}
language: Java
archived_at: {today}
---
```

Then this body, in order:

# {title}

**Source:** [LeetCode {lc_num}]({url}) · Difficulty: {difficulty} · Tags: {tags}

## Description

A clear English restatement of the problem (1–2 paragraphs). Keep the LeetCode constraints + examples verbatim under sub-headings:

### Examples

(Preserve LeetCode's example I/O. Use fenced code blocks.)

### Constraints

(Bullet list copied from LeetCode.)

## Approach

用**中文**写 2–4 句,讲清所选算法以及*为什么* —— 模式识别、数据结构选择、关键洞察。写成候选人在面试里真的会说出口的那种一段式思路框架。(英文技术术语保留原文,如 binary search / monotonic stack / two pointers。)

## Solution (Java)

```java
// production-quality Java. Hard rules:
//   - NO `var` keyword anywhere — always use explicit types.
//   - NO `Map.merge(...)` — use get/put or getOrDefault.
//   - Include `import java.util.*;` (and other imports) at the top inside the fenced block.
//   - Class name: `Solution` with the standard LeetCode method signature for this problem.
//   - Solve the problem at optimal time + space for this difficulty tier.
//   - Code compiles and passes the LeetCode tests.
//   - 在关键代码块上加**中文注释**帮助理解题意与解法:每个主要步骤
//     (状态初始化、核心循环、状态转移、关键判断、返回值)前一行加一句
//     中文说明它在做什么、为什么这么做。只注释 non-obvious 的行 ——
//     不要每行都注释,也不要注释 `i++` 这种自解释代码。技术术语保留英文。

import java.util.*;

class Solution {{
    // ...
}}
```

**Complexity:** Time O(...), Space O(...) — one short sentence justifying each.

## Alternative Solutions

0–2 brief alternatives (e.g. recursive vs iterative, BFS vs DFS, hash vs sort). For each: one-paragraph framing + a short Java snippet. SKIP this section entirely (do not write the heading) if the optimal approach is the only sensible one.

## Edge Cases

Bullet list of edge cases the solution must handle (empty input, single element, all duplicates, overflow, negative numbers, etc.). 3–8 bullets.

## Follow-ups

0–3 plausible follow-up extensions the interviewer might ask, with one-line answers. SKIP the section if there are no natural follow-ups.

# Hard rules
- Output ONLY the markdown described above. No preamble, no "here is the answer", no trailing summary.
- No `var` keyword in Java code.
- No `Map.merge(...)` calls.
- All code blocks fenced with language tag (```java, ```python if you reference one, etc.).
- Keep length reasonable — typically 100–250 lines including code.
"""


def generate_java_answer(client, model: str, question_meta: dict, detail: dict, company: str) -> str:
    today = date.today().isoformat()
    tags = ", ".join(detail.get("tags") or []) or "—"
    statement = (detail.get("statement_text") or "").strip()
    if not statement:
        statement = f"(LeetCode statement unavailable — please check {detail.get('url')})"
    prompt = JAVA_ANSWER_PROMPT.format(
        title=question_meta["title"],
        lc_num=question_meta["lc_number"] or "?",
        difficulty=question_meta["difficulty"],
        url=question_meta["url"],
        tags=tags,
        statement=statement[:9000],
        company=company,
        rank=question_meta["idx"],
        today=today,
    )
    resp = client.messages.create(
        model=model,
        max_tokens=6000,
        messages=[{"role": "user", "content": prompt}],
        timeout=180,
    )
    parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    return "\n".join(parts).strip()


# ---------------- filename + write ----------------


def make_filename(idx: int, lc_number: int, title: str) -> str:
    kebab = _kebab(title)[:80] or "untitled"
    lc_num_str = str(lc_number) if lc_number else "0"
    return f"LC-{idx:03d}-{lc_num_str}-{kebab}.md"


# ---------------- main ----------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LeetCode six-months company question scraper + Java answer writer")
    p.add_argument("--company", required=True, help="Company display name (must match config OR a free-form name)")
    p.add_argument("--lc-slug", help="Override LC slug (default: from config or kebab of --company)")
    p.add_argument("--cdp-url", default="http://localhost:9222")
    p.add_argument("--limit", type=int, default=0, help="Max questions to process (0 = no cap)")
    p.add_argument("--start-idx", type=int, default=1, help="Resume from rank N (1-indexed)")
    p.add_argument("--overwrite", action="store_true", help="Regenerate even if output file exists")
    p.add_argument("--dry-run", action="store_true", help="Scrape list only; do not fetch details or generate answers")
    p.add_argument("--throttle-seconds", type=float, default=4.0, help="Sleep between question fetches")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s | %(message)s",
    )

    env = load_env()
    config = load_config()
    display, lc_slug, obsidian_dir = resolve_company(args.company, config)
    if args.lc_slug:
        lc_slug = args.lc_slug
    log.info("company=%s | lc_slug=%s | obsidian_dir=%s", display, lc_slug, obsidian_dir)

    # CDP probe
    probe = cdp_probe(args.cdp_url)
    if not probe:
        log.error("CDP not reachable at %s — run ai-chrome ensure_chrome.sh first.", args.cdp_url)
        sys.exit(2)
    log.info("CDP ok: %s", probe.get("Browser") or probe)

    # Output dir
    out_dir = obsidian_file_path(env, obsidian_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("output dir: %s", out_dir)

    # Lazy-import playwright + openai
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("playwright not installed — pip install playwright (venv at .venv)")
        sys.exit(3)
    try:
        import anthropic
    except ImportError:
        log.error("anthropic SDK not installed — pip install anthropic")
        sys.exit(3)

    client = None
    model = env.get("LLM_MODEL") or env.get("LC_ANSWER_MODEL") or "claude-opus-4.6"
    if not args.dry_run:
        api_key = env.get("ANTHROPIC_TOKEN") or env.get("ANTHROPIC_API_KEY") or env.get("OPENAI_API_KEY")
        base_url = env.get("ANTHROPIC_BASE_URL") or env.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        if not api_key:
            log.error("Missing ANTHROPIC_TOKEN (or OPENAI_API_KEY) in env (~/.hermes/.env). Cannot call LLM.")
            sys.exit(4)
        client = anthropic.Anthropic(api_key=api_key, base_url=base_url)
        log.info("LLM client: model=%s base_url=%s", model, base_url)

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(args.cdp_url)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
            questions = scrape_question_list(page, lc_slug, args.verbose)
            if not questions:
                log.error("No questions found. Possible causes: (1) wrong slug; (2) LC Premium not logged in; (3) page structure changed.")
                sys.exit(5)
            log.info("scraped %d questions from LC six-months list", len(questions))

            if args.dry_run:
                for q in questions:
                    acc = f"{q['acceptance']}%" if q.get('acceptance') is not None else "  ?"
                    print(f"  [{q['idx']:3d}] LC#{q['lc_number']:<5d} | {q['difficulty']:<7s} | acc={acc} | {q['title']}")
                return

            cutoff = len(questions) if args.limit <= 0 else min(len(questions), args.start_idx - 1 + args.limit)
            for q in questions:
                if q["idx"] < args.start_idx:
                    continue
                if q["idx"] > cutoff:
                    break
                fname = make_filename(q["idx"], q["lc_number"], q["title"])
                outpath = out_dir / fname
                if outpath.exists() and not args.overwrite:
                    log.info("[%3d/%d] SKIP (exists): %s", q["idx"], len(questions), fname)
                    continue
                log.info("[%3d/%d] fetch detail: %s (%s)", q["idx"], len(questions), q["title"], q["title_slug"])
                try:
                    detail = scrape_question_detail(page, q["title_slug"], args.verbose)
                except Exception as exc:
                    log.error("  detail fetch failed: %s", exc)
                    continue
                if not (detail.get("statement_text") or "").strip():
                    log.warning("  empty statement — skipping (likely paywall or render error)")
                    time.sleep(args.throttle_seconds)
                    continue
                # Patch in difficulty from detail if list-row was Unknown
                if q["difficulty"] == "Unknown" and detail.get("difficulty"):
                    q["difficulty"] = detail["difficulty"]
                log.info("  generating Java answer via %s …", model)
                try:
                    md = generate_java_answer(client, model, q, detail, display)
                except Exception as exc:
                    log.error("  LLM call failed: %s", exc)
                    time.sleep(args.throttle_seconds)
                    continue
                _write_with_retry(outpath, md)
                log.info("  wrote %s (%d chars)", outpath.name, len(md))
                time.sleep(args.throttle_seconds)
        finally:
            page.close()


if __name__ == "__main__":
    main()
