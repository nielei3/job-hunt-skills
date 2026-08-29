"""Jobright job alert email parser.

Parses Jobright instant-push and daily-digest alert emails into structured
job dicts using regex-based HTML extraction.
"""
from __future__ import annotations

import html as html_mod
import re
from email.message import EmailMessage
from typing import Any

from job_scout_lib import collapse_ws

# --- Regex patterns for parsing Jobright email HTML ---
JOB_SECTION_SPLIT_RE = re.compile(r'id="job-section"')
COMPANY_RE = re.compile(r'id="job-company-name"[^>]*>(.*?)</(?:p|span|td|div)', re.DOTALL)
MATCH_PCT_RE = re.compile(r'id="job-match-percentage"[^>]*>(.*?)</(?:p|span|td|div)', re.DOTALL)
TITLE_LINK_RE = re.compile(r'id="job-title"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
TAG_RE = re.compile(r'id="job-tag"[^>]*>(.*?)</(?:p|span|td|div)', re.DOTALL)
TIME_RE = re.compile(r'id="job-time-posted"[^>]*>(.*?)</(?:p|span|td|div)', re.DOTALL)
CATEGORIES_RE = re.compile(r'id="job-company-categories"[^>]*>(.*?)</(?:p|span|td|div)', re.DOTALL)
CANONICAL_URL_RE = re.compile(r'jobright\.ai/jobs/info/([a-f0-9]{24})')

# Tag classification patterns
SALARY_TAG_RE = re.compile(r'\$.*?/yr', re.I)
REFERRALS_TAG_RE = re.compile(r'\d+\+\s*referrals?', re.I)


def _strip_html_tags(s: str) -> str:
    """Remove HTML tags and decode entities."""
    cleaned = re.sub(r'<[^>]+>', '', s)
    return html_mod.unescape(collapse_ws(cleaned)).strip()


def _get_html_body(msg: EmailMessage) -> str:
    """Extract the HTML body from a Jobright email message.

    Uses get_payload(decode=True) for robust quoted-printable decoding.
    Handles both multipart and single-part messages.
    """
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = part.get_content_disposition()
            if disp == 'attachment':
                continue
            if ctype == 'text/html':
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode('utf-8', errors='replace')
    else:
        if msg.get_content_type() == 'text/html':
            payload = msg.get_payload(decode=True)
            if payload:
                return payload.decode('utf-8', errors='replace')
    return ''


def _canonical_url(raw_url: str) -> str:
    """Extract canonical Jobright URL (strip query params)."""
    m = CANONICAL_URL_RE.search(raw_url)
    if m:
        return f"https://jobright.ai/jobs/info/{m.group(1)}"
    return raw_url


def _classify_tags(tag_texts: list[str]) -> dict[str, str]:
    """Classify tag texts into salary, location, and referrals."""
    salary = ''
    location = ''
    referrals = ''
    for t in tag_texts:
        t = t.strip()
        if not t:
            continue
        if '$' in t and '/yr' in t:
            salary = t
        elif REFERRALS_TAG_RE.match(t):
            referrals = t
        else:
            location = t
    return {'salary': salary, 'location': location, 'referrals': referrals}


def build_jobs_from_jobright_message(msg: EmailMessage) -> list[dict[str, Any]]:
    """Parse a Jobright alert email and return job dicts."""
    html = _get_html_body(msg)
    if not html:
        return []

    # Check this is actually a jobright email with job sections
    if 'id="job-section"' not in html:
        return []

    message_id = collapse_ws(msg.get('message-id', ''))
    subject = collapse_ws(msg.get('subject', ''))
    email_date = collapse_ws(msg.get('date', ''))

    # Split HTML by job-section markers
    sections = JOB_SECTION_SPLIT_RE.split(html)
    # First element is header content before the first job-section
    if len(sections) <= 1:
        return []

    jobs: list[dict[str, Any]] = []
    seen_canonical_ids: set[str] = set()

    for section in sections[1:]:  # skip preamble before first job-section
        # Extract company name
        company_m = COMPANY_RE.search(section)
        company = _strip_html_tags(company_m.group(1)) if company_m else ''

        # Extract match percentage
        match_pct_m = MATCH_PCT_RE.search(section)
        match_pct = 0
        if match_pct_m:
            pct_text = _strip_html_tags(match_pct_m.group(1))
            pct_text = pct_text.replace('%', '').strip()
            try:
                match_pct = int(pct_text)
            except ValueError:
                pass

        # Extract title and URL
        title_m = TITLE_LINK_RE.search(section)
        if not title_m:
            continue  # Skip sections without a title link
        raw_url = html_mod.unescape(title_m.group(1)).strip()
        title = _strip_html_tags(title_m.group(2))

        if not title or not raw_url:
            continue

        # Skip "View More Opportunities" or similar non-job links
        if 'view more' in title.lower() or '/jobs/recommend' in raw_url:
            continue

        # Deduplicate by canonical URL
        canonical = _canonical_url(raw_url)
        canonical_m = CANONICAL_URL_RE.search(canonical)
        canonical_id = canonical_m.group(1) if canonical_m else canonical
        if canonical_id in seen_canonical_ids:
            continue
        seen_canonical_ids.add(canonical_id)

        # Extract tags (salary, location, referrals)
        tag_texts = [_strip_html_tags(m.group(1)) for m in TAG_RE.finditer(section)]
        classified = _classify_tags(tag_texts)

        # Extract time posted
        time_m = TIME_RE.search(section)
        time_posted = ''
        if time_m:
            time_posted = _strip_html_tags(time_m.group(1)).rstrip('· ').strip()

        # Extract company categories
        cat_m = CATEGORIES_RE.search(section)
        categories = ''
        if cat_m:
            categories = _strip_html_tags(cat_m.group(1))

        # Infer modality from location
        location = classified['location']
        modality = 'remote' if location.lower() == 'remote' else ''

        jobs.append({
            'title': title,
            'company': company,
            'location': location,
            'modality': modality,
            'snippet': '',
            'salary': classified['salary'],
            'jobright_match_pct': match_pct,
            'jobright_url': canonical,
            'links': [canonical],
            'external_candidates': [],
            'email_message_id': message_id,
            'email_date': email_date,
            'subject': subject,
            'source': 'jobright_alert_email',
            'jobright_time_posted': time_posted,
            'jobright_categories': categories,
            'jobright_referrals': classified['referrals'],
        })

    return jobs
