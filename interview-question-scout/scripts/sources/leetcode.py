"""
LeetCode Discuss source — pulls company-tagged interview posts via Chrome CDP.

Why CDP and not raw GraphQL?
- LeetCode's `/graphql` endpoint requires CSRF token + logged-in cookies for many queries.
- Anonymous access works for some discuss queries but is heavily rate-limited and
  often returns truncated bodies.
- The user already runs a Chrome with login on port 9222 for the other scrapers,
  so we reuse it.

Strategy:
1. Navigate to `https://leetcode.com/discuss/?searchQuery=<COMPANY>+interview&category=interview-question&orderBy=newest_to_oldest`
2. Page through the result list, collecting post links + titles + dates.
3. For each post, open the URL and extract `.discuss-markdown-container` body
   + top-N reply comments.

Per-company config (in interview-question-scout.yaml):
    sources:
      leetcode:
        search_terms: ["<Company> interview", "<Company> oa"]   # required, list
        category: interview-question                            # optional, default
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import quote

from .base import Post, Source

log = logging.getLogger(__name__)

DEFAULT_CATEGORY = "interview-question"
LIST_URL = (
    "https://leetcode.com/discuss/?"
    "currentPage=1&orderBy=newest_to_oldest&query={query}&tags=&category={category}"
)


class LeetCodeDiscussSource(Source):
    slug = "leetcode"
    name = "LeetCode Discuss"

    def fetch(
        self,
        company: dict[str, Any],
        company_config: dict[str, Any],
        *,
        since_days: int = 365,
        limit: int = 50,
        env: dict[str, str] | None = None,
        verbose: bool = False,
    ) -> Iterable[Post]:
        env = env or {}
        cdp_url = env.get("OA_CHROME_CDP_URL", "http://localhost:9222")
        search_terms: list[str] = company_config.get("search_terms") or []
        if not search_terms:
            log.warning("[leetcode] %s: no search_terms configured, skipping",
                        company.get("name"))
            return
        category = company_config.get("category", DEFAULT_CATEGORY)
        company_name = company.get("name") or "unknown"

        # Lazy import so the package loads even if playwright isn't installed
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.error("[leetcode] playwright not installed; pip install playwright")
            return

        cutoff = datetime.now(timezone.utc).timestamp() - since_days * 86400
        yielded = 0

        with sync_playwright() as pw:
            try:
                browser = pw.chromium.connect_over_cdp(cdp_url)
            except Exception as exc:
                log.error("[leetcode] CDP connect failed: %s", exc)
                return
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()

            seen_urls: set[str] = set()
            for term in search_terms:
                if yielded >= limit:
                    break
                list_url = LIST_URL.format(query=quote(term), category=category)
                if verbose:
                    log.info("[leetcode] search: %s", list_url)
                try:
                    page.goto(list_url, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(5)  # SPA render
                except Exception as exc:
                    log.warning("[leetcode] list goto failed: %s", exc)
                    continue
                html = page.content()
                # Discuss list links look like `/discuss/<id>/<slug>`
                links = re.findall(r'href="(/discuss/\d+/[^"#?]+)"', html)
                links = list(dict.fromkeys(links))  # dedup keep order
                if verbose:
                    log.info("[leetcode]   found %d candidate posts", len(links))

                for href in links:
                    if yielded >= limit:
                        break
                    post_url = "https://leetcode.com" + href
                    if post_url in seen_urls:
                        continue
                    seen_urls.add(post_url)
                    post = _fetch_one_post(page, post_url, company_name, verbose)
                    if not post:
                        continue
                    if post.posted_at.timestamp() < cutoff:
                        if verbose:
                            log.info("[leetcode]   skip (too old): %s", post.title[:60])
                        continue
                    yield post
                    yielded += 1
                    time.sleep(float(env.get("FETCH_INTERVAL_SECONDS", "3")))
            page.close()
            browser.close()


def _fetch_one_post(page, url: str, company_name: str, verbose: bool) -> Post | None:
    """Open a single discuss post and extract body + metadata."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        time.sleep(3)
    except Exception as exc:
        log.warning("[leetcode] post goto failed (%s): %s", url, exc)
        return None
    html = page.content()

    # Extract post id from URL: /discuss/<id>/<slug>
    m = re.search(r"/discuss/(\d+)/", url)
    if not m:
        return None
    post_id = m.group(1)

    # Title: <h1> or page <title>
    title_m = re.search(r"<h1[^>]*>([^<]+)</h1>", html)
    title = title_m.group(1).strip() if title_m else ""
    if not title:
        t_m = re.search(r"<title>([^<]+)</title>", html)
        if t_m:
            title = t_m.group(1).split(" - LeetCode")[0].strip()

    # Body: discuss markdown container; fall back to body text
    body_m = re.search(
        r'<div[^>]*class="[^"]*discuss-markdown-container[^"]*"[^>]*>(.*?)</div>\s*<',
        html, re.DOTALL,
    )
    if body_m:
        body = _strip_html(body_m.group(1))
    else:
        # Fallback: visible body text via Playwright
        try:
            body = page.evaluate("() => document.body.innerText")
        except Exception:
            body = ""

    # Posted-at: LeetCode uses relative time in HTML ("3 days ago"); ISO time
    # often appears in <time datetime="..."> attribute or in page __NEXT_DATA__.
    posted_at = datetime.now(timezone.utc)
    dt_m = re.search(r'datetime="([^"]+)"', html)
    if dt_m:
        try:
            posted_at = datetime.fromisoformat(dt_m.group(1).replace("Z", "+00:00"))
        except Exception:
            pass

    # Reply count: look for "X comments" badge
    reply_m = re.search(r"(\d+)\s+comments?", html, re.IGNORECASE)
    reply_count = int(reply_m.group(1)) if reply_m else 0

    if verbose:
        log.info("[leetcode]   %s : %s", post_id, title[:80])

    return Post(
        source="leetcode",
        source_id=post_id,
        company=company_name,
        title=title,
        body=body[:20000],  # cap to keep LLM cost bounded
        url=url,
        posted_at=posted_at,
        reply_count=reply_count,
        raw={"original_html_len": len(html)},
    )


def _strip_html(s: str) -> str:
    """Quick-and-dirty HTML → text for LeetCode markdown blocks."""
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"</p>", "\n\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"&nbsp;", " ", s)
    s = re.sub(r"&amp;", "&", s)
    s = re.sub(r"&lt;", "<", s)
    s = re.sub(r"&gt;", ">", s)
    s = re.sub(r"&quot;", '"', s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()
