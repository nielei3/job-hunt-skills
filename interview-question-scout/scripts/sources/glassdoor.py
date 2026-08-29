"""
Glassdoor source — semi-manual.

Why not full automation?
    Glassdoor sits behind Cloudflare + bot detection that requires solving challenges
    on most requests. Headless or even CDP-driven access is unreliable and risks
    account flags. The 1p3a / LeetCode / Reddit fetchers handle ~95% of the user's
    needs; Glassdoor is the gap-filler for companies with little Asian-forum coverage.

Workflow:
    1. User manually browses the Glassdoor company interviews page in their normal
       Chrome session.
    2. For each interview write-up the user wants captured, they paste the page
       contents into a plain text file under:
           data/manual/glassdoor/<company-slug>/<arbitrary-name>.md
       Recommended frontmatter (optional but helpful):
           ---
           url: https://www.glassdoor.com/Interview/...
           posted_at: 2026-05-10
           title: Software Engineer Interview
           ---
           <pasted body>
    3. Run the scout. This source walks the directory and yields each file as a Post.

Per-company config (in interview-question-scout.yaml):
    sources:
      glassdoor:
        slug: whatnot          # subdirectory name under data/manual/glassdoor/
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .base import Post, Source

log = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


class GlassdoorSource(Source):
    slug = "glassdoor"
    name = "Glassdoor (manual paste)"

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
        project_root = Path(env.get("INTERVIEW_SCOUT_ROOT", "."))
        slug = company_config.get("slug")
        if not slug:
            log.warning("[glassdoor] %s: no slug", company.get("name"))
            return
        company_name = company.get("name") or slug
        base = project_root / "data" / "manual" / "glassdoor" / slug
        if not base.exists():
            if verbose:
                log.info("[glassdoor] %s: no manual dir %s — skipping", slug, base)
            return

        cutoff = datetime.now(timezone.utc).timestamp() - since_days * 86400
        yielded = 0
        for fp in sorted(base.glob("*.md")):
            if yielded >= limit:
                break
            try:
                content = fp.read_text(encoding="utf-8")
            except Exception as exc:
                log.warning("[glassdoor] read failed (%s): %s", fp, exc)
                continue
            meta, body = _split_frontmatter(content)
            title = meta.get("title") or fp.stem
            url = meta.get("url") or f"file://{fp}"
            posted_at_str = meta.get("posted_at")
            posted_at = _parse_date(posted_at_str) if posted_at_str else _file_mtime(fp)
            if posted_at.timestamp() < cutoff:
                continue
            source_id = hashlib.sha256(url.encode()).hexdigest()[:16]
            yield Post(
                source="glassdoor",
                source_id=source_id,
                company=company_name,
                title=title,
                body=body.strip()[:20000],
                url=url,
                posted_at=posted_at,
                raw={"file": str(fp)},
            )
            yielded += 1


def _split_frontmatter(content: str) -> tuple[dict[str, str], str]:
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}, content
    raw_fm, body = m.group(1), m.group(2)
    meta: dict[str, str] = {}
    for line in raw_fm.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, body


def _parse_date(s: str) -> datetime:
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def _file_mtime(fp: Path) -> datetime:
    try:
        return datetime.fromtimestamp(fp.stat().st_mtime, tz=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)
