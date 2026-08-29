#!/usr/bin/env python3
"""Read/write the global `Pending JDs.md` Obsidian note that holds awaiting-JD jobs."""
from __future__ import annotations

import re
from datetime import date as _date
from pathlib import Path
from typing import Any

PENDING_HEADER = """# Pending JDs

> Each section below is a candidate role waiting for you to paste the JD body.
> After pasting, run `python scripts/retriage.py` to score (or wait for tomorrow's cron).
"""

_PASTE_PLACEHOLDER = '<!-- paste JD body below this line, then save -->'

_SECTION_RE = re.compile(
    r'<!--\s*JOB-START\s+id=(?P<id>\S+?)(?:\s+first_seen=(?P<first_seen>\S+?))?\s*-->'
    r'(?P<body>.*?)'
    r'<!--\s*JOB-END\s+id=(?P=id)\s*-->',
    re.DOTALL,
)
_HTML_COMMENT_RE = re.compile(r'<!--.*?-->', re.DOTALL)


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _read(path: Path) -> str:
    if not path.exists():
        return ''
    return path.read_text()


def _today_iso() -> str:
    return _date.today().isoformat()


def _section_for(job: dict[str, Any], today: str) -> str:
    job_key = job.get('job_key') or ''
    if not job_key or re.search(r'\s|-->', job_key):
        raise ValueError(
            f'job_key {job_key!r} is not safe for embedding in an HTML comment marker '
            f'(must be non-empty and contain no whitespace or "-->")'
        )
    company = job.get('company') or ''
    title = job.get('title') or ''
    location = job.get('location') or ''
    url = job.get('linkedin_url') or job.get('external_jd_url') or ''
    reason = job.get('filter_reason') or ''
    return f"""<!-- JOB-START id={job_key} first_seen={today} -->
## {company} — {title}

- **Company**: {company}
- **Title**: {title}
- **Location**: {location}
- **First seen**: {today}
- **Source link**: {url}
- **Filter verdict**: {reason}

### Job Description

{_PASTE_PLACEHOLDER}

<!-- JOB-END id={job_key} -->"""


def read_pending(path: Path) -> list[dict[str, Any]]:
    """Parse all JOB-START/JOB-END blocks. Returns list of {job_key, first_seen, body}."""
    text = _read(path)
    if not text:
        return []
    out: list[dict[str, Any]] = []
    for m in _SECTION_RE.finditer(text):
        out.append({
            'job_key': m.group('id'),
            'first_seen': m.group('first_seen') or '',
            'body': m.group('body'),
        })
    return out


def sync_new(path: Path, jobs: list[dict[str, Any]], *, today: str | None = None) -> int:
    """Append a section per unresolved job whose job_key isn't already present.

    Creates the file with PENDING_HEADER if it doesn't exist. Returns count appended.
    """
    today = today or _today_iso()
    existing = {entry['job_key'] for entry in read_pending(path)}
    new_sections: list[str] = []
    appended = 0
    for job in jobs:
        if job.get('external_jd_status') and job['external_jd_status'] != 'unresolved':
            continue
        jk = job.get('job_key')
        if not jk or jk in existing:
            continue
        new_sections.append(_section_for(job, today))
        existing.add(jk)
        appended += 1

    if appended == 0:
        return 0

    _ensure_parent(path)
    current = _read(path)
    if not current.strip():
        current = PENDING_HEADER + '\n'
    if not current.endswith('\n'):
        current += '\n'
    current += '\n---\n\n' + '\n\n---\n\n'.join(new_sections) + '\n'
    path.write_text(current)
    return appended


def _strip_comments(s: str) -> str:
    return _HTML_COMMENT_RE.sub('', s)


def _extract_jd_body(section_body: str) -> str:
    """Body between '### Job Description' and end. Comments stripped, whitespace collapsed."""
    marker = '### Job Description'
    idx = section_body.find(marker)
    if idx < 0:
        return ''
    after = section_body[idx + len(marker):]
    cleaned = _strip_comments(after)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    return cleaned


def extract_ready_for_triage(path: Path, *, min_jd_text_chars: int = 200) -> list[dict[str, Any]]:
    """Return jobs whose JD section has at least min_jd_text_chars of content."""
    text = _read(path)
    if not text:
        return []
    out: list[dict[str, Any]] = []
    for m in _SECTION_RE.finditer(text):
        body = m.group('body')
        jd = _extract_jd_body(body)
        if len(jd) < min_jd_text_chars:
            continue
        # Pull metadata out of the section body markdown.
        company = _line_after(body, '- **Company**:')
        title = _line_after(body, '- **Title**:')
        location = _line_after(body, '- **Location**:')
        source = _line_after(body, '- **Source link**:')
        out.append({
            'job_key': m.group('id'),
            'company': company,
            'title': title,
            'location': location,
            'external_jd_url': source,
            'external_jd_status': 'user_supplied',
            'external_jd_source': 'pending_inbox_manual',
            'jd_text': jd,
            'jd_text_preview': jd[:1200],
            'jd_title': title,
            'jd_company': company,
            'jd_location': location,
        })
    return out


def _line_after(body: str, prefix: str) -> str:
    for line in body.splitlines():
        s = line.strip()
        if s.startswith(prefix):
            return s[len(prefix):].strip()
    return ''


def remove_scored(path: Path, job_keys: list[str]) -> int:
    """Remove sections for the given keys. Preserves anything outside marker pairs."""
    text = _read(path)
    if not text or not job_keys:
        return 0
    keys = set(job_keys)
    removed = 0

    def repl(m: re.Match) -> str:
        nonlocal removed
        if m.group('id') in keys:
            removed += 1
            return ''
        return m.group(0)

    new_text = _SECTION_RE.sub(repl, text)
    # Collapse runs of empty separators left behind. Loop until stable because
    # re.sub doesn't re-scan its replacement, so multiple adjacent dividers
    # need multiple passes.
    prev = None
    while prev != new_text:
        prev = new_text
        new_text = re.sub(r'\n---\s*\n(\s*\n---\s*\n)+', '\n---\n\n', new_text)
        new_text = re.sub(r'\n{3,}', '\n\n', new_text)
    # If no sections remain, also strip any trailing horizontal rule.
    if not _SECTION_RE.search(new_text):
        new_text = re.sub(r'\n---\s*\n*\Z', '\n', new_text)
    path.write_text(new_text.rstrip() + '\n')
    return removed
