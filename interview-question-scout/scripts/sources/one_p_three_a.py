"""
1point3acres source — thin wrapper around existing fetch_1point3acres.py.

History: this was the original (and only) source. Now repackaged as a Source plugin
so the orchestrator can run multiple sources side-by-side.
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Import the existing fetcher module from the parent scripts dir
_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

from fetch_1point3acres import (  # noqa: E402
    connect_to_cdp,
    fetch_company_list_paginated,
    fetch_thread,
)

from .base import Post, Source

log = logging.getLogger(__name__)


class OnePointThreeAcresSource(Source):
    slug = "1p3a"
    name = "1point3acres"

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
        slug = company_config.get("slug")
        if not slug:
            log.warning("[1p3a] %s: no slug configured, skipping", company.get("name"))
            return
        company_name = company.get("name") or slug

        try:
            with connect_to_cdp(cdp_url) as browser:
                listings = fetch_company_list_paginated(
                    browser, slug, months=since_days // 30 or 1, verbose=verbose
                )
                yielded = 0
                for listing in listings:
                    if yielded >= limit:
                        break
                    thread = fetch_thread(browser, listing.thread_id)
                    if not thread:
                        continue
                    posted_at_str = thread.get("posted_at") or ""
                    try:
                        posted_at = (
                            datetime.fromisoformat(posted_at_str.replace("Z", "+00:00"))
                            if posted_at_str
                            else datetime.now(timezone.utc)
                        )
                    except Exception:
                        posted_at = datetime.now(timezone.utc)

                    body = thread.get("body", "")
                    replies = thread.get("replies") or []
                    if replies:
                        body = body + "\n\n---\n\n" + "\n\n---\n\n".join(replies)

                    yield Post(
                        source=self.slug,
                        source_id=str(listing.thread_id),
                        company=company_name,
                        title=thread.get("title") or listing.title or "",
                        body=body,
                        url=f"https://www.1point3acres.com/bbs/thread-{listing.thread_id}-1-1.html",
                        posted_at=posted_at,
                        author=thread.get("author", ""),
                        reply_count=len(replies),
                        locked=bool(thread.get("locked_by_dami")),
                        raw={"listing": listing.__dict__},
                    )
                    yielded += 1
                    time.sleep(float(env.get("FETCH_INTERVAL_SECONDS", "4")))
        except Exception as exc:
            log.error("[1p3a] %s: fetch failed: %s", company_name, exc)
