#!/usr/bin/env python3
"""Append interview summary blocks to an Obsidian vault file.

Handles iCloud-backed vaults (uses download-via-Finder fallback for reads).
Writes the per-company file in append-only mode, initializing the header
block on first run.
"""
from __future__ import annotations

from pathlib import Path

from interview_question_scout_lib import (
    local_now_iso,
    read_text_with_icloud_fallback,
    write_text_append,
)


HEADER_TEMPLATE = """# {company} 面经

> 自动聚合自 jobs.1point3acres.com/companies/{slug}/interview
> 由 interview-question-scout 维护。每次有新帖追加到文件末尾，不修改历史内容。

---

"""


def ensure_file_header(path: Path, company: str, slug: str) -> None:
    if path.exists():
        try:
            existing = read_text_with_icloud_fallback(path)
        except Exception:
            existing = ""
        if existing.strip():
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    from interview_question_scout_lib import _write_with_retry
    _write_with_retry(path, HEADER_TEMPLATE.format(company=company, slug=slug))


def append_block(path: Path, block: str) -> None:
    write_text_append(path, block)


def append_run_footer(path: Path, company: str, new_count: int, locked_count: int, error_count: int) -> None:
    """Small trailing line so you can scroll to find run boundaries while reading."""
    footer = (
        f"<!-- run {local_now_iso()}: +{new_count} new, {locked_count} locked, {error_count} errors -->\n"
    )
    write_text_append(path, footer)
