"""
Source interface + canonical Post for multi-platform interview-experience scraping.

每个数据源（1point3acres / LeetCode Discuss / Reddit / Glassdoor / …）实现 Source ABC，
返回统一的 Post 列表。后续 summarize / aggregate 完全 source-agnostic。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Iterable


@dataclass
class Post:
    """Canonical interview-experience post, source-agnostic.

    Mandatory fields (every source must populate):
        source       — short id, matches Source.slug (e.g. "1p3a", "leetcode")
        source_id    — unique within source (e.g. thread id, post slug, t3_xxx)
        company      — canonical company name as configured (NOT slug)
        title        — post title / question heading
        body         — full text body, plain or lightly cleaned markdown
        url          — canonical permalink back to source
        posted_at    — UTC datetime; if unknown, datetime.now(UTC) and set raw["posted_at_unknown"]=True

    Optional fields (sources fill if available):
        author       — display name
        reply_count  — number of comments / replies merged into body (informative)
        locked       — true if behind paywall and body is partial
        raw          — source-specific extras (preserve for debugging / future use)
    """

    source: str
    source_id: str
    company: str
    title: str
    body: str
    url: str
    posted_at: datetime
    author: str = ""
    reply_count: int = 0
    locked: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["posted_at"] = self.posted_at.astimezone(timezone.utc).isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Post":
        if isinstance(d.get("posted_at"), str):
            d = {**d, "posted_at": datetime.fromisoformat(d["posted_at"])}
        return cls(**d)

    @property
    def stable_key(self) -> str:
        """Stable cross-source identity. Used by dedup in summarize/aggregate."""
        return f"{self.source}:{self.source_id}"


class Source(ABC):
    """Abstract source plugin.

    Subclasses implement fetch() and parse_company_config().
    Registered in sources/__init__.py SOURCES dict by slug.
    """

    #: short identifier; matches Post.source and config key
    slug: str = ""

    #: human-readable name for logs
    name: str = ""

    @abstractmethod
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
        """Pull posts for one company from this source.

        Args:
            company:        full company entry from interview-question-scout.yaml
                            (has .name, .sources mapping, etc.)
            company_config: this source's per-company config (e.g. {slug: 'whatnot'}
                            for 1p3a, or {search_terms: [...]} for Reddit).
            since_days:     ignore posts older than this; sources may approximate.
            limit:          hard cap on number of posts to return.
            env:            merged env dict (cookies, API keys, etc.).
            verbose:        if True, sources should log per-post progress.

        Yields:
            Post objects. Caller decides what to do with them
            (cache to bodies.json, send to LLM, etc.).
        """
        raise NotImplementedError
