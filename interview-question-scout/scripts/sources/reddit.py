"""
Reddit source — pulls interview-related self-posts via Reddit's public JSON API.

Anonymous; no login required. Reddit rate-limits unauthenticated traffic to ~60
req/min; we sleep between requests.

Per-company config (in interview-question-scout.yaml):
    sources:
      reddit:
        search_terms: ["<Company> interview", "<Company> OA"]   # required
        subreddits: [cscareerquestions, leetcode]                # optional, default both
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import Post, Source

log = logging.getLogger(__name__)

DEFAULT_SUBREDDITS = ["cscareerquestions", "leetcode"]
UA = "interview-question-scout/1.0"


class RedditSource(Source):
    slug = "reddit"
    name = "Reddit"

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
        terms: list[str] = company_config.get("search_terms") or []
        if not terms:
            log.warning("[reddit] %s: no search_terms configured", company.get("name"))
            return
        subreddits: list[str] = company_config.get("subreddits") or DEFAULT_SUBREDDITS
        company_name = company.get("name") or "unknown"
        cutoff = datetime.now(timezone.utc).timestamp() - since_days * 86400

        seen_ids: set[str] = set()
        yielded = 0
        for sub in subreddits:
            for term in terms:
                if yielded >= limit:
                    break
                # restrict to selfposts only (self:yes) for interview-prose content
                params = {
                    "q": term,
                    "restrict_sr": "1",
                    "sort": "new",
                    "limit": str(min(25, limit - yielded)),
                    "t": "year",
                    "self": "yes",
                    "raw_json": "1",
                }
                url = f"https://www.reddit.com/r/{sub}/search.json?{urlencode(params)}"
                if verbose:
                    log.info("[reddit] %s | %s", sub, term)
                data = _http_json(url)
                if data is None:
                    continue
                children = (
                    data.get("data", {}).get("children", []) if data else []
                )
                for child in children:
                    if yielded >= limit:
                        break
                    post = _child_to_post(child, company_name)
                    if post is None:
                        continue
                    if post.source_id in seen_ids:
                        continue
                    seen_ids.add(post.source_id)
                    if post.posted_at.timestamp() < cutoff:
                        continue
                    yield post
                    yielded += 1
                time.sleep(2.0)  # respect Reddit rate limit


def _http_json(url: str) -> dict | None:
    try:
        req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        log.warning("[reddit] http failed (%s): %s", url, exc)
        return None


def _child_to_post(child: dict, company_name: str) -> Post | None:
    d = child.get("data") or {}
    if not d.get("is_self"):
        return None  # link posts have no interview prose
    body = d.get("selftext") or ""
    title = d.get("title") or ""
    if not body and not title:
        return None
    fullname = d.get("name") or d.get("id") or ""
    posted_ts = d.get("created_utc")
    posted_at = (
        datetime.fromtimestamp(posted_ts, tz=timezone.utc)
        if posted_ts
        else datetime.now(timezone.utc)
    )
    permalink = d.get("permalink") or ""
    url = "https://www.reddit.com" + permalink if permalink.startswith("/") else permalink

    return Post(
        source="reddit",
        source_id=fullname,
        company=company_name,
        title=title,
        body=body[:20000],
        url=url,
        posted_at=posted_at,
        author=d.get("author") or "",
        reply_count=int(d.get("num_comments") or 0),
        raw={
            "subreddit": d.get("subreddit"),
            "score": d.get("score"),
            "upvote_ratio": d.get("upvote_ratio"),
        },
    )
