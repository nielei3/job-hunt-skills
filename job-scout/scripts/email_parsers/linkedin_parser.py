"""LinkedIn job alert email parser.

Moved from fetch_job_alert_emails.py — all LinkedIn-specific parsing logic.
"""
from __future__ import annotations

import re
from email.message import EmailMessage
from typing import Any

from job_scout_lib import (
    collapse_ws,
    extract_links,
    html_to_lines,
    is_http_url,
    is_linkedin_url,
    looks_like_linkedin_job_url,
)

GENERIC_LINK_TEXT = {
    'apply', 'apply now', 'view job', 'view jobs', 'see jobs', 'see all jobs',
    'linkedin', 'learn more', 'read more', 'details', 'open', 'view',
    'manage recommendations', 'remote jobs', 'view all jobs', 'gen ai jobs',
}
# --- Compound-title parser (LinkedIn new anchor-text format) ---
# Anchor format: "{Job Title} {Company} · {Location} ({Modality}) {button} [salary]"
ROLE_TOKENS = {
    'engineer', 'engineering', 'developer', 'developers',
    'manager', 'lead', 'leader', 'architect', 'director',
    'scientist', 'analyst', 'designer', 'consultant',
    'technician', 'officer', 'president', 'vp',
    'administrator', 'specialist', 'strategist',
    'researcher', 'programmer', 'head', 'chief',
    'founder', 'apprentice', 'intern', 'representative',
    'coordinator', 'advisor', 'partner', 'advocate',
    'associate', 'owner',
}
BUTTON_SUFFIX_RE = re.compile(
    r'\s*(easy apply|apply now|apply|view job|view jobs|view|save|promoted|see more)\s*$',
    re.I,
)
SALARY_SUFFIX_RE = re.compile(
    r'\s*\$[\d.,KkMm]+\s*[-–—]\s*\$[\d.,KkMm]*(?:\s*/\s*(?:yr|hr|year|hour))?[\s.]*$',
    re.I,
)
# Any $-prefixed tail (catches truncated `$123K-$...` or `$60/hr`).
SALARY_TAIL_RE = re.compile(r'\s*\$[\d.,KkMm][^·]*$')
MODALITY_RE = re.compile(r'\((on[- ]site|hybrid|remote)\)', re.I)
COMPANY_SUFFIX_TOKENS = {
    'inc', 'inc.', 'llc', 'corp', 'corp.', 'corporation',
    'ltd', 'ltd.', 'co', 'co.', 'gmbh', 'holdings',
    'venture', 'ventures', 'group', 'labs', 'technologies',
    'systems', 'solutions',
}
# Connector words / punctuation that signal title continuation
TITLE_CONTINUATION_END_CHARS = {',', '-', '–', '—', '&', '/', ':'}
TITLE_CONTINUATION_STANDALONE = {'-', '–', '—', '&', '/', 'and', 'of', 'for', 'in', 'to', 'with', 'at', 'the'}
# Hint list of known companies — used as a secondary signal when scanning for title/company boundary.
# Populated lazily from config in load_known_companies().
_KNOWN_COMPANIES_CACHE: list[str] | None = None


def _strip_word_punct(w: str) -> str:
    return re.sub(r'[^\w]+', '', w).lower()


def load_known_companies(cfg: dict[str, Any] | None = None) -> list[str]:
    global _KNOWN_COMPANIES_CACHE
    if _KNOWN_COMPANIES_CACHE is not None:
        return _KNOWN_COMPANIES_CACHE
    names: set[str] = set()
    # Static seed of common companies seen in LinkedIn alerts
    names.update({
        'TikTok', 'TikTok USDS Joint Venture', 'ByteDance',
        'Amazon', 'Apple', 'Google', 'Meta', 'Microsoft',
        'Netflix', 'Uber', 'Airbnb', 'Snap Inc.', 'Snap',
        'Salesforce', 'Stripe', 'Walmart', 'Carta', 'GitHub',
        'F5', 'Siemens', 'JPMorganChase', 'OpenAI', 'Anthropic',
        'Cursor', 'Perplexity', 'Scale AI', 'Databricks',
        'Snowflake', 'Reddit',
    })
    if cfg:
        for t in cfg.get('target_companies', []) or []:
            n = (t or {}).get('name')
            if n:
                names.add(n)
        mappings = ((cfg.get('external_ats_map') or {}).get('company_mappings') or {})
        names.update(mappings.keys())
    # Longest-first so multi-word companies match greedily
    _KNOWN_COMPANIES_CACHE = sorted(names, key=lambda s: (-len(s), s))
    return _KNOWN_COMPANIES_CACHE


def split_compound_title(raw: str, known_companies: list[str] | None = None) -> dict[str, str]:
    """Parse LinkedIn's new compound-anchor format.

    Returns a dict with keys: title, company, location, modality. Missing
    fields come back as empty strings. The raw input is preserved as-is on the
    caller side; this function is deterministic and pure.
    """
    out = {'title': '', 'company': '', 'location': '', 'modality': ''}
    if not raw:
        return out
    s = collapse_ws(raw).strip()
    if not s:
        return out

    # Peel trailing button text, salary, and modality off the tail (order matters).
    s = BUTTON_SUFFIX_RE.sub('', s).strip()
    s = SALARY_SUFFIX_RE.sub('', s).strip()
    m = MODALITY_RE.search(s)
    if m:
        out['modality'] = m.group(1).lower().replace(' ', '-')
        s = (s[:m.start()] + s[m.end():]).strip()
    # Salary can also land AFTER the modality — strip any $-tail that's left.
    s = SALARY_TAIL_RE.sub('', s).strip()
    s = re.sub(r'\s+', ' ', s).strip()

    # Without the ' · ' separator we cannot reliably split title vs company.
    if ' · ' not in s:
        out['title'] = s
        return out

    left, _, loc = s.partition(' · ')
    out['location'] = loc.strip().rstrip('.')
    left = left.strip()
    if not left:
        return out

    # Strategy 1: match a known company at the right edge of `left`.
    if known_companies is None:
        known_companies = load_known_companies()
    for name in known_companies:
        # Match case-insensitively, word-boundary-aware
        pattern = re.compile(r'(?:^|\s)(' + re.escape(name) + r')\s*$', re.I)
        mm = pattern.search(left)
        if mm:
            out['company'] = left[mm.start(1):mm.end(1)]
            out['title'] = left[:mm.start(1)].strip().rstrip(',-–— ')
            if out['title']:
                return out
            # If stripping leaves no title, fall through to heuristic.
            break

    # Strategy 2: heuristic — find last role token, extend title forward.
    words = left.split()
    role_idx = -1
    for i, w in enumerate(words):
        if _strip_word_punct(w) in ROLE_TOKENS:
            role_idx = i
    if role_idx == -1:
        out['title'] = left
        return out

    end_idx = role_idx
    i = role_idx
    while i < len(words) - 1:
        cur = words[i]
        nxt = words[i + 1]
        cur_tail = cur[-1] if cur else ''
        # Parenthetical qualifier → include through closing paren
        if nxt.startswith('('):
            for k in range(i + 1, len(words)):
                if words[k].endswith(')'):
                    end_idx = k
                    i = k
                    break
            else:
                break
            continue
        # Continuation if current word ends with a continuation char
        if cur_tail in TITLE_CONTINUATION_END_CHARS:
            end_idx = i + 1
            i += 1
            continue
        # Continuation if next word is a standalone connector
        if nxt in TITLE_CONTINUATION_STANDALONE or nxt.lower() in TITLE_CONTINUATION_STANDALONE:
            end_idx = i + 1
            i += 1
            continue
        break

    out['title'] = ' '.join(words[:end_idx + 1]).strip().rstrip(',-–— ')
    out['company'] = ' '.join(words[end_idx + 1:]).strip()
    # Cap overly greedy company capture at 5 words.
    comp_words = out['company'].split()
    if len(comp_words) > 5:
        out['company'] = ' '.join(comp_words[-4:])
    return out


def looks_like_compound_anchor(text: str) -> bool:
    """Heuristic: LinkedIn's new format puts ' · ' in the anchor text."""
    return bool(text) and ' · ' in text


def looks_like_linkedin_alert(msg: EmailMessage) -> bool:
    sender = collapse_ws(msg.get('from', '')).lower()
    subject = collapse_ws(msg.get('subject', '')).lower()
    if 'linkedin' not in sender and 'linkedin' not in subject:
        return False
    if 'job' in subject or 'jobs' in subject or 'alert' in subject:
        return True
    html, text = get_bodies(msg)
    corpus = (html + '\n' + text).lower()
    return 'linkedin' in corpus and 'job' in corpus


def clean_title(raw: str) -> str:
    title = collapse_ws(raw)
    if not title:
        return ''
    if title.lower() in GENERIC_LINK_TEXT:
        return ''
    if len(title) > 160:
        return ''
    return title


def _extract_linkedin_job_id(url: str) -> str:
    """Pull the numeric job id from a /comm/jobs/view/<id>/ URL, or '' if not present."""
    m = re.search(r'/jobs/view/(\d+)', url)
    return m.group(1) if m else ''


def _find_card_in_text(text_lines: list[str], hint: str, *, start_after: int = 0) -> tuple[str, str, str, int]:
    """Look for a ' · '-delimited line in the email body that mentions the title hint.

    For LinkedIn digest emails: when a job has anchor text "Title", the next line
    typically reads "Company · Location" or "Company · Location (Modality)".
    We scan from `start_after` forward.

    Returns (title, company, location, found_at_index). found_at_index is the
    index of the matched title line, or -1 if no card was found. Caller uses
    found_at_index to advance start_after for the next job.
    """
    if not hint:
        return '', '', '', -1
    hint_l = hint.lower().strip()
    if not hint_l:
        return '', '', '', -1
    # Pass 1: exact match (preferred).
    for i in range(start_after, len(text_lines)):
        line = text_lines[i]
        if hint_l == line.lower().strip():
            for offset in range(1, 4):
                if i + offset >= len(text_lines):
                    break
                cand = text_lines[i + offset]
                if ' · ' in cand:
                    parts = [p.strip() for p in cand.split(' · ', 1)]
                    if len(parts) == 2:
                        company, loc = parts[0], parts[1]
                        return hint, company, loc, i
            return '', '', '', i  # title found but no card line; mark position consumed
    # Pass 2: substring match (fallback for noisy preamble vs job line).
    for i in range(start_after, len(text_lines)):
        line = text_lines[i]
        if hint_l in line.lower():
            for offset in range(1, 4):
                if i + offset >= len(text_lines):
                    break
                cand = text_lines[i + offset]
                if ' · ' in cand:
                    parts = [p.strip() for p in cand.split(' · ', 1)]
                    if len(parts) == 2:
                        company, loc = parts[0], parts[1]
                        return hint, company, loc, i
            return '', '', '', i
    return '', '', '', -1


def get_bodies(msg: EmailMessage) -> tuple[str, str]:
    html = ''
    text = ''
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = part.get_content_disposition()
            if disp == 'attachment':
                continue
            try:
                payload = part.get_content()
            except Exception:
                continue
            if ctype == 'text/html' and not html:
                html = str(payload)
            elif ctype == 'text/plain' and not text:
                text = str(payload)
    else:
        payload = msg.get_content()
        if msg.get_content_type() == 'text/html':
            html = str(payload)
        else:
            text = str(payload)
    return html, text


def build_jobs_from_linkedin_message(msg: EmailMessage, *, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Parse a LinkedIn alert email and return job dicts."""
    # Quick relevance check — if it doesn't look like a job alert, skip
    if not looks_like_linkedin_alert(msg):
        return []

    html, text = get_bodies(msg)
    links = extract_links(html)
    text_lines = [line for line in (html_to_lines(html) or text.splitlines()) if collapse_ws(line)]

    message_id = collapse_ws(msg.get('message-id', ''))
    subject = collapse_ws(msg.get('subject', ''))
    email_date = collapse_ws(msg.get('date', ''))

    # Load known companies for compound-title splitting
    known = load_known_companies(cfg)

    jobs: list[dict[str, Any]] = []
    seen_job_ids: set[str] = set()
    next_search_start = 0  # advances past the last consumed title-line

    for link in links:
        href = link['href']
        if not looks_like_linkedin_job_url(href):
            continue
        job_id = _extract_linkedin_job_id(href)
        if not job_id or job_id in seen_job_ids:
            continue
        seen_job_ids.add(job_id)

        title = ''
        company = ''
        location = ''
        modality = ''
        snippet = ''

        anchor_text = clean_title(link.get('text', ''))
        if anchor_text and looks_like_compound_anchor(anchor_text):
            parsed = split_compound_title(anchor_text, known_companies=known)
            title = parsed['title'] or title
            company = parsed['company']
            location = parsed['location']
            modality = parsed['modality']
        elif anchor_text:
            title = anchor_text

        if not (title and company):
            t2, c2, loc2, found_at = _find_card_in_text(text_lines, anchor_text or title, start_after=next_search_start)
            title = title or t2
            company = company or c2
            location = location or loc2
            if found_at >= 0:
                next_search_start = found_at + 1  # next call resumes after this title-line

        if not title:
            continue  # No identifiable title -> skip; don't fall back to garbage.

        jobs.append({
            'title': title,
            'company': company,
            'location': location,
            'modality': modality,
            'snippet': snippet,
            'linkedin_url': href,
            'links': [item['href'] for item in links if is_http_url(item['href'])],
            'external_candidates': [item['href'] for item in links if is_http_url(item['href']) and not is_linkedin_url(item['href'])],
            'email_message_id': message_id,
            'email_date': email_date,
            'subject': subject,
            'source': 'linkedin_alert_email',
        })

    return jobs
