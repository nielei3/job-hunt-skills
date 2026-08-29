#!/usr/bin/env python3
"""Proactively scan target company ATS pages for matching jobs.

Adapters supported:
  - greenhouse        (JD inline; e.g. Anthropic, Airbnb, Reddit, Affirm)
  - lever             (JD inline)
  - pcsx              (Microsoft / Microsoft AI; JD per-page fetch)
  - apple_jobs        (jobs.apple.com)
  - amazon_jobs       (amazon.jobs)
  - google_careers    (careers.google.com — UNTESTED, best-effort)
  - meta_careers      (metacareers.com   — UNTESTED, best-effort)

Each adapter is (list_fn, jd_fetch_fn). list_fn returns canonical job dicts; if
JD text is not inline, it sets jd_text='' and the main loop calls jd_fetch_fn
lazily for each title-matched, deduped job (capped per company).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Callable
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from job_scout_lib import (
    collapse_ws,
    default_cafile,
    html_to_lines,
    load_env,
    load_project_config,
    nested_get,
    project_root,
    read_json,
    seen_jobs_json_path,
    write_json_snapshot_and_history,
)

DEFAULT_TIMEOUT = 20
DEFAULT_MAX_JD_FETCHES_PER_COMPANY = 60


def fetch_url(url: str, timeout: int = DEFAULT_TIMEOUT, *, headers: dict[str, str] | None = None) -> str:
    hdrs = {'User-Agent': 'Mozilla/5.0 (compatible; job-scout/1.0)'}
    if headers:
        hdrs.update(headers)
    req = Request(url, headers=hdrs)
    cafile = default_cafile()
    ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
    with urlopen(req, timeout=timeout, context=ctx) as resp:
        charset = resp.headers.get_content_charset() or 'utf-8'
        return resp.read().decode(charset, errors='ignore')


def fetch_json(url: str, timeout: int = DEFAULT_TIMEOUT, *, headers: dict[str, str] | None = None) -> Any:
    return json.loads(fetch_url(url, timeout=timeout, headers=headers))


def post_json(url: str, body: dict, timeout: int = DEFAULT_TIMEOUT, *, headers: dict[str, str] | None = None) -> Any:
    hdrs = {
        'User-Agent': 'Mozilla/5.0 (compatible; job-scout/1.0)',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    if headers:
        hdrs.update(headers)
    payload = json.dumps(body).encode('utf-8')
    req = Request(url, data=payload, method='POST', headers=hdrs)
    cafile = default_cafile()
    ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
    with urlopen(req, timeout=timeout, context=ctx) as resp:
        charset = resp.headers.get_content_charset() or 'utf-8'
        return json.loads(resp.read().decode(charset, errors='ignore'))


def jd_from_html(html: str) -> str:
    lines = [ln for ln in html_to_lines(html) if collapse_ws(ln)]
    return '\n'.join(lines)


def _build_job(title: str, company: str, location: str, url: str,
               jd_text: str, ats: str, ats_id: str) -> dict:
    job_key = hashlib.sha1((url or f'{company}|{title}|{ats_id}').encode('utf-8')).hexdigest()[:16]
    return {
        'job_key': job_key,
        'title': title,
        'company': company,
        'location': location,
        'linkedin_url': '',
        'links': [url] if url else [],
        'external_candidates': [url] if url else [],
        'snippet': '',
        'source': 'target_scan',
        'ats': ats,
        'ats_job_id': ats_id,
        'discovered_at': datetime.now(timezone.utc).isoformat(),
        'jd_text': jd_text,
        'jd_text_preview': jd_text[:1200],
        'jd_title': title,
        'jd_company': company,
        'jd_location': location,
        'external_jd_status': 'ats_api',
        'external_jd_url': url,
        'external_jd_source': ats,
        'jd_parser': f'{ats}_api',
        'search_queries': [],
        'search_candidates': [],
        'fetch_errors': [],
    }


# ============================================================================
# Greenhouse (JD inline)
# ============================================================================

def fetch_greenhouse_jobs(board_slug: str, timeout: int = DEFAULT_TIMEOUT) -> list[dict]:
    url = f'https://boards-api.greenhouse.io/v1/boards/{board_slug}/jobs?content=true'
    data = fetch_json(url, timeout=timeout)
    return data.get('jobs', [])


def normalize_greenhouse_job(raw: dict, company: str) -> dict:
    title = collapse_ws(raw.get('title', ''))
    location = collapse_ws((raw.get('location') or {}).get('name', ''))
    url = raw.get('absolute_url', '')
    jd_text = jd_from_html(raw.get('content', '') or '')
    return _build_job(title, company, location, url, jd_text, 'greenhouse', str(raw.get('id', '')))


def list_greenhouse(spec: dict, timeout: int) -> list[dict]:
    slug = spec.get('board_slug', '')
    if not slug:
        return []
    return [normalize_greenhouse_job(j, spec.get('name', '')) for j in fetch_greenhouse_jobs(slug, timeout=timeout)]


# ============================================================================
# Lever (JD inline)
# ============================================================================

def fetch_lever_jobs(board_slug: str, timeout: int = DEFAULT_TIMEOUT) -> list[dict]:
    url = f'https://api.lever.co/v0/postings/{board_slug}?mode=json'
    return fetch_json(url, timeout=timeout)


def normalize_lever_job(raw: dict, company: str) -> dict:
    title = collapse_ws(raw.get('text', ''))
    location = collapse_ws((raw.get('categories') or {}).get('location', ''))
    url = raw.get('hostedUrl', '')
    jd_html = raw.get('description', '') or raw.get('descriptionPlain', '')
    jd_text = jd_from_html(jd_html)
    return _build_job(title, company, location, url, jd_text, 'lever', raw.get('id', ''))


def list_lever(spec: dict, timeout: int) -> list[dict]:
    slug = spec.get('board_slug', '')
    if not slug:
        return []
    return [normalize_lever_job(j, spec.get('name', '')) for j in fetch_lever_jobs(slug, timeout=timeout)]


# ============================================================================
# Microsoft PCSX (Microsoft / Microsoft AI). JD fetched per page.
# ============================================================================
# Search API (proven by resolve_external_jd.resolve_via_microsoft_pcsx):
#   GET https://apply.careers.microsoft.com/api/pcsx/search
#       ?domain=microsoft.com&query=&location=&start=N
# Response shape: {"data": {"positions": [{positionUrl, title|name, jobId,
#                                          standardizedLocations|locations, ...}, ...]}}

PCSX_PAGE_SIZE = 20
PCSX_MAX_PAGES = 100  # cap total pulled at PCSX_PAGE_SIZE * PCSX_MAX_PAGES = 2000


def list_pcsx(spec: dict, timeout: int) -> list[dict]:
    company = spec.get('name', 'Microsoft')
    domain = (spec.get('domain') or 'microsoft.com').strip()
    team_filter = (spec.get('team_filter') or '').strip().lower()
    out: list[dict] = []
    for page in range(PCSX_MAX_PAGES):
        start = page * PCSX_PAGE_SIZE
        url = (
            'https://apply.careers.microsoft.com/api/pcsx/search'
            f'?domain={quote_plus(domain)}&query=&location=&start={start}'
        )
        try:
            payload = fetch_json(url, timeout=timeout)
        except Exception as exc:
            print(f'    pcsx page {page} error: {exc}')
            break
        positions = (payload.get('data') or {}).get('positions') or []
        if not positions:
            break
        for raw in positions:
            title = collapse_ws(raw.get('title') or raw.get('name') or '')
            if team_filter:
                hay = ' '.join([
                    title.lower(),
                    str(raw.get('businessGroup') or '').lower(),
                    str(raw.get('orgFunction') or '').lower(),
                    str(raw.get('discipline') or '').lower(),
                ])
                if team_filter not in hay:
                    continue
            locs = raw.get('standardizedLocations') or raw.get('locations') or []
            location = ' | '.join(locs) if isinstance(locs, list) else str(locs or '')
            position_url = str(raw.get('positionUrl') or '').strip()
            full_url = ('https://apply.careers.microsoft.com' + position_url) if position_url else ''
            ats_id = str(raw.get('jobId') or raw.get('id') or '')
            out.append(_build_job(title, company, location, full_url, '', 'pcsx', ats_id))
        if len(positions) < PCSX_PAGE_SIZE:
            break
    return out


def jd_pcsx(url: str, timeout: int) -> str:
    if not url:
        return ''
    return jd_from_html(fetch_url(url, timeout=timeout))


# ============================================================================
# Apple jobs (jobs.apple.com). JD fetched per page.
# ============================================================================
# Old API (api/role/search) → 301 to /pagenotfound as of 2026-04.
# New approach: parse SSR hydration data from the search HTML page.
# The page embeds window.__staticRouterHydrationData = JSON.parse("...")
# which contains {loaderData: {search: {searchResults: [...], totalRecords: N}}}
# Detail page: https://jobs.apple.com/en-us/details/{positionId}/{transformedPostingTitle}

APPLE_MAX_PAGES = 50
_APPLE_HYDRATION_RE = re.compile(
    r'window\.__staticRouterHydrationData\s*=\s*JSON\.parse\("(.*?)"\)\s*;?\s*</script>',
    re.DOTALL,
)


def _parse_apple_hydration(html: str) -> tuple[list[dict], int]:
    """Extract searchResults and totalRecords from Apple SSR hydration data."""
    m = _APPLE_HYDRATION_RE.search(html)
    if not m:
        return [], 0
    inner_str = json.loads('"' + m.group(1) + '"')
    data = json.loads(inner_str)
    loader = data.get('loaderData', {})
    search_data = loader.get('search', {})
    if not isinstance(search_data, dict):
        return [], 0
    results = search_data.get('searchResults') or []
    total = search_data.get('totalRecords', 0)
    return results, total


def list_apple(spec: dict, timeout: int) -> list[dict]:
    company = spec.get('name', 'Apple')
    out: list[dict] = []
    seen_ids: set[str] = set()
    for page in range(1, APPLE_MAX_PAGES + 1):
        url = f'https://jobs.apple.com/en-us/search?location=united-states-USA&page={page}'
        try:
            html = fetch_url(url, timeout=timeout)
        except Exception as exc:
            print(f'    apple page {page} error: {exc}')
            break
        results, total = _parse_apple_hydration(html)
        if not results:
            break
        for r in results:
            title = collapse_ws(r.get('postingTitle') or r.get('title') or '')
            locs = r.get('locations') or []
            loc_names: list[str] = []
            if isinstance(locs, list):
                for loc in locs:
                    if isinstance(loc, dict):
                        n = loc.get('name') or loc.get('city') or ''
                        if n:
                            loc_names.append(str(n))
                    elif isinstance(loc, str):
                        loc_names.append(loc)
            location = ' | '.join(loc_names)
            posting_id = str(r.get('positionId') or r.get('id') or '')
            if posting_id in seen_ids:
                continue
            seen_ids.add(posting_id)
            slug = r.get('transformedPostingTitle') or ''
            detail_url = f'https://jobs.apple.com/en-us/details/{posting_id}/{slug}'.rstrip('/') if posting_id else ''
            out.append(_build_job(title, company, location, detail_url, '', 'apple_jobs', posting_id))
        if len(out) >= total:
            break
    return out


def jd_apple(url: str, timeout: int) -> str:
    if not url:
        return ''
    return jd_from_html(fetch_url(url, timeout=timeout))


# ============================================================================
# Amazon jobs (amazon.jobs).
# ============================================================================
# Search API:
#   GET https://www.amazon.jobs/en/search.json?offset=N&result_limit=100&sort=relevant
#       &country[]=USA&category[]=software-development
# Response: {"jobs": [{id, title, location, normalized_location, job_path,
#                      description, description_short}], "hits": int}

AMAZON_PAGE_SIZE = 100
AMAZON_MAX_PAGES = 50


def list_amazon(spec: dict, timeout: int) -> list[dict]:
    company = spec.get('name', 'Amazon')
    categories = spec.get('amazon_categories') or ['software-development']
    cat_qs = '&'.join(f'category[]={quote_plus(c)}' for c in categories)
    out: list[dict] = []
    for page in range(AMAZON_MAX_PAGES):
        offset = page * AMAZON_PAGE_SIZE
        url = (
            'https://www.amazon.jobs/en/search.json'
            f'?offset={offset}&result_limit={AMAZON_PAGE_SIZE}&sort=relevant'
            f'&country[]=USA&{cat_qs}'
        )
        try:
            payload = fetch_json(url, timeout=timeout)
        except Exception as exc:
            print(f'    amazon offset {offset} error: {exc}')
            break
        jobs = payload.get('jobs') or []
        if not jobs:
            break
        for j in jobs:
            title = collapse_ws(j.get('title', ''))
            location = collapse_ws(j.get('normalized_location') or j.get('location', ''))
            posting_path = j.get('job_path', '')
            full_url = ('https://www.amazon.jobs' + posting_path) if posting_path else ''
            jd_text = jd_from_html(j.get('description') or '') or jd_from_html(j.get('description_short') or '')
            out.append(_build_job(title, company, location, full_url, jd_text, 'amazon_jobs', str(j.get('id', ''))))
        if len(jobs) < AMAZON_PAGE_SIZE:
            break
    return out


def jd_amazon(url: str, timeout: int) -> str:
    if not url:
        return ''
    return jd_from_html(fetch_url(url, timeout=timeout))


# ============================================================================
# Google careers (careers.google.com).
# ============================================================================
# UNTESTED. Google does not expose a stable public job-search API. We fetch the
# SSR search results page and parse anchor patterns of the form:
#     /about/careers/applications/jobs/results/<digits>-<slug>
# The detail page is then fetched for JD text. If the layout changes, this
# adapter's listing may return zero results — error is logged and the run
# continues. Verify with `python3 scripts/scan_target_companies.py -c google`.

GOOGLE_MAX_PAGES = 30
# Google careers SSR uses a <base href="...applications/"> tag, so job links
# are *relative* (e.g. href="jobs/results/<id>-<slug>?..."). The job title
# lives in an aria-label="Learn more about <title>" attribute, not in the
# anchor text. We accept both the old absolute-path form and the new relative
# form so the regex survives if Google changes back.
GOOGLE_LINK_RE = re.compile(
    r'href=["\'](?P<href>(?:/about/careers/applications/)?jobs/results/(?P<id>\d+)-[^"\'?]*)[^"\']*["\']'
    r'[^>]*?aria-label="Learn more about (?P<title>[^"]+)"',
    re.I,
)
GOOGLE_BASE_URL = 'https://www.google.com/about/careers/applications/'


def list_google(spec: dict, timeout: int) -> list[dict]:
    company = spec.get('name', 'Google')
    out: list[dict] = []
    seen_ids: set[str] = set()
    for page in range(1, GOOGLE_MAX_PAGES + 1):
        url = (
            'https://www.google.com/about/careers/applications/jobs/results/'
            f'?location=United%20States&page={page}'
        )
        try:
            html = fetch_url(url, timeout=timeout)
        except Exception as exc:
            print(f'    google page {page} error: {exc}')
            break
        matches = list(GOOGLE_LINK_RE.finditer(html))
        if not matches:
            break
        new_count = 0
        for m in matches:
            job_id = m.group('id')
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)
            new_count += 1
            href = m.group('href')
            title = collapse_ws(unescape(m.group('title')))
            # Resolve relative or absolute href to a full URL
            if href.startswith('/'):
                full_url = 'https://www.google.com' + href
            else:
                full_url = GOOGLE_BASE_URL + href
            out.append(_build_job(title, company, '', full_url, '', 'google_careers', job_id))
        if new_count == 0:
            break
    return out


def jd_google(url: str, timeout: int) -> str:
    if not url:
        return ''
    return jd_from_html(fetch_url(url, timeout=timeout))


# ============================================================================
# Meta careers (metacareers.com).
# ============================================================================
# BROKEN as of 2026-04: metacareers.com is a React SPA that blocks bot
# User-Agents (HTTP 400), redirects /jobs → /jobsearch, and renders jobs
# client-side only. The old regex scraper cannot work. Options:
#   (a) Headless browser (Playwright) — heavy dependency
#   (b) Reverse-engineer their GraphQL API
#   (c) Rely on LinkedIn alerts for Meta jobs (current fallback)
# For now, list_meta returns empty and logs a warning.


def list_meta(spec: dict, timeout: int) -> list[dict]:
    print('    ⚠ meta_careers adapter disabled — metacareers.com is SPA-only, '
          'blocks urllib. Rely on LinkedIn alerts for Meta jobs.')
    return []


def jd_meta(url: str, timeout: int) -> str:
    if not url:
        return ''
    return jd_from_html(fetch_url(url, timeout=timeout))


# ============================================================================
# RemoteOK aggregator (cross-company; per-job company name).
# ============================================================================
# Public JSON API: https://remoteok.com/api
# Response: a JSON array. The first element is a legal notice (no id/company);
# subsequent elements are jobs with: id, slug, position, company, location,
# description (HTML), apply_url, url, tags, ...
# JD is inline (description). Each job carries its own company; spec['name']
# is just the source label ("RemoteOK") for logging.

REMOTEOK_API_URL = 'https://remoteok.com/api'


def list_remoteok(spec: dict, timeout: int) -> list[dict]:
    try:
        payload = fetch_json(REMOTEOK_API_URL, timeout=timeout)
    except Exception as exc:
        print(f'    remoteok error: {exc}')
        return []
    if not isinstance(payload, list):
        return []
    out: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        # Skip legal-notice entry — it has no id or company.
        if not item.get('id') or not item.get('company'):
            continue
        title = collapse_ws(item.get('position') or '')
        company = collapse_ws(item.get('company') or '')
        location = collapse_ws(item.get('location') or 'Remote')
        apply_url = (item.get('apply_url') or '').strip()
        canonical_url = (item.get('url') or '').strip()
        external_url = apply_url or canonical_url
        jd_text = jd_from_html(item.get('description') or '')
        ats_id = str(item.get('id') or item.get('slug') or '')
        out.append(_build_job(title, company, location, external_url, jd_text, 'remoteok', ats_id))
    return out


# ============================================================================
# We Work Remotely aggregator (RSS).
# ============================================================================
# Public RSS feeds. Each <item> has <title>, <link>, <description> (HTML).
# Title is one of:
#   "Job Title at Company Name"
#   "Company Name: Job Title"
# All listings are remote. JD is inline (description).

WWR_DEFAULT_FEEDS = [
    'https://weworkremotely.com/categories/remote-programming-jobs.rss',
    'https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss',
    'https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss',
    'https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss',
]

_WWR_TITLE_AT_RE = re.compile(r'^(?P<title>.+?)\s+at\s+(?P<company>.+)$', re.I)
_WWR_TITLE_COLON_RE = re.compile(r'^(?P<company>[^:]+):\s+(?P<title>.+)$')


def _parse_wwr_title(raw: str) -> tuple[str, str]:
    """Return (company, title) from a WWR feed title. Falls back to ('', raw)."""
    s = collapse_ws(raw)
    m = _WWR_TITLE_AT_RE.match(s)
    if m:
        return collapse_ws(m.group('company')), collapse_ws(m.group('title'))
    m = _WWR_TITLE_COLON_RE.match(s)
    if m:
        return collapse_ws(m.group('company')), collapse_ws(m.group('title'))
    return '', s


def list_weworkremotely(spec: dict, timeout: int) -> list[dict]:
    feeds = spec.get('wwr_feeds') or WWR_DEFAULT_FEEDS
    out: list[dict] = []
    seen_links: set[str] = set()
    for feed_url in feeds:
        try:
            xml_text = fetch_url(feed_url, timeout=timeout)
        except Exception as exc:
            print(f'    wwr feed {feed_url}: {exc}')
            continue
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            print(f'    wwr parse error ({feed_url}): {exc}')
            continue
        for item in root.iterfind('.//item'):
            link = (item.findtext('link') or '').strip()
            if not link or link in seen_links:
                continue
            seen_links.add(link)
            title_full = item.findtext('title') or ''
            description = item.findtext('description') or ''
            company, title = _parse_wwr_title(title_full)
            jd_text = jd_from_html(description)
            ats_id = link.rstrip('/').rsplit('/', 1)[-1]
            out.append(_build_job(title, company, 'Remote', link, jd_text, 'weworkremotely', ats_id))
    return out


# ============================================================================
# Shopify careers (shopify.com/careers/feed.xml).
# ============================================================================
# Shopify uses Ashby as backend ATS but does NOT expose a public Ashby board.
# Instead, they publish an XML job feed at /careers/feed.xml with full JD HTML.
# Each <job> has: partnerJobId, title, description (HTML), applyUrl, location,
# workplaceTypes, experienceLevel, listDate.

SHOPIFY_FEED_URL = 'https://www.shopify.com/careers/feed.xml'


def list_shopify_careers(spec: dict, timeout: int) -> list[dict]:
    company = spec.get('name', 'Shopify')
    try:
        xml_text = fetch_url(SHOPIFY_FEED_URL, timeout=timeout)
    except Exception as exc:
        print(f'    shopify_careers error: {exc}')
        return []
    root = ET.fromstring(xml_text)
    out: list[dict] = []
    for job_el in root.findall('.//job'):
        title = (job_el.findtext('title') or '').strip()
        if not title:
            continue
        ats_id = (job_el.findtext('partnerJobId') or '').strip()
        location = (job_el.findtext('location') or '').strip()
        apply_url = (job_el.findtext('applyUrl') or '').strip()
        desc_html = job_el.findtext('description') or ''
        jd_text = jd_from_html(desc_html)
        out.append(_build_job(title, company, location, apply_url, jd_text, 'shopify_careers', ats_id))
    return out


# ============================================================================
# Ashby (api.ashbyhq.com posting-api).
# ============================================================================
# Public JSON API: https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true
# Response: {"jobs": [{id, title, location, locationName, departmentName, employmentType,
#                      descriptionHtml, jobUrl, applyUrl, ...}]}
# JD is inline in descriptionHtml. Covers OpenAI, Perplexity, Decagon, Character.AI,
# OpenRouter, Whatnot, etc.


def list_ashby(spec: dict, timeout: int) -> list[dict]:
    company = spec.get('name', '')
    slug = (spec.get('board_slug') or '').strip()
    if not slug:
        return []
    url = f'https://api.ashbyhq.com/posting-api/job-board/{quote_plus(slug)}?includeCompensation=true'
    try:
        payload = fetch_json(url, timeout=timeout)
    except Exception as exc:
        print(f'    ashby error ({slug}): {exc}')
        return []
    out: list[dict] = []
    for raw in payload.get('jobs') or []:
        if not isinstance(raw, dict):
            continue
        title = collapse_ws(raw.get('title') or '')
        loc_field = raw.get('location') or raw.get('locationName') or ''
        if isinstance(loc_field, dict):
            location = collapse_ws(loc_field.get('city') or loc_field.get('country') or '')
        else:
            location = collapse_ws(str(loc_field))
        # Sometimes Ashby returns secondaryLocations list — fold them in for visibility.
        secondaries = raw.get('secondaryLocations') or []
        if isinstance(secondaries, list) and secondaries:
            extras = []
            for s in secondaries:
                if isinstance(s, dict):
                    extras.append(s.get('locationName') or s.get('city') or '')
                elif isinstance(s, str):
                    extras.append(s)
            if extras:
                location = (location + ' | ' + ' | '.join(filter(None, extras))).strip(' |')
        external_url = (raw.get('jobUrl') or raw.get('applyUrl') or '').strip()
        jd_text = jd_from_html(raw.get('descriptionHtml') or '')
        ats_id = str(raw.get('id') or '')
        out.append(_build_job(title, company, location, external_url, jd_text, 'ashby', ats_id))
    return out


# ============================================================================
# Cursor careers (cursor.com/careers — custom HTML).
# ============================================================================
# The cursor.com careers page lists openings inline. JD body needs a separate
# fetch from each opening's URL. Ported from resolve_external_jd.cursor_careers_openings.

_CURSOR_LINK_RE = re.compile(
    r'<a[^>]*\s+href=["\'](?P<href>/careers/[^"\']+)["\'][^>]*>(?P<body>.*?)</a>',
    re.I | re.S,
)
_CURSOR_TITLE_RE = re.compile(r'<p[^>]*>\s*(?P<text>[^<]+?)\s*</p>', re.I | re.S)
_CURSOR_SPAN_RE = re.compile(r'<span[^>]*>\s*(?P<text>[^<]+?)\s*</span>', re.I | re.S)


def list_cursor_careers(spec: dict, timeout: int) -> list[dict]:
    company = spec.get('name', 'Cursor')
    try:
        html = fetch_url('https://cursor.com/careers', timeout=timeout)
    except Exception as exc:
        print(f'    cursor error: {exc}')
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for m in _CURSOR_LINK_RE.finditer(html):
        href = collapse_ws(m.group('href'))
        if not href or href == '/careers' or href in seen:
            continue
        seen.add(href)
        body = m.group('body')
        title_m = _CURSOR_TITLE_RE.search(body)
        title = collapse_ws(unescape(title_m.group('text'))) if title_m else ''
        spans = [collapse_ws(unescape(t)) for t in _CURSOR_SPAN_RE.findall(body)]
        spans = [s for s in spans if s and s not in {'·', 'Apply →', 'Apply'}]
        location = (spans[-1] if spans else '').replace(';', ' | ')
        if not title:
            continue
        full_url = 'https://cursor.com' + href
        ats_id = href.rstrip('/').rsplit('/', 1)[-1]
        out.append(_build_job(title, company, location, full_url, '', 'cursor_careers', ats_id))
    return out


def jd_cursor(url: str, timeout: int) -> str:
    if not url:
        return ''
    return jd_from_html(fetch_url(url, timeout=timeout))


# ============================================================================
# Workday (myworkdayjobs.com — many enterprise/big-tech tenants).
# ============================================================================
# Each Workday tenant has a URL pattern:
#   https://{tenant}.{pod}.myworkdayjobs.com/{site}
# where pod is wd1, wd5, wd12, etc. (varies per tenant).
#
# Listing API:
#   POST https://{tenant}.{pod}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
#   Body: {"appliedFacets": {}, "limit": 20, "offset": N, "searchText": ""}
#   Response: {"jobPostings": [{title, locationsText, externalPath, bulletFields, ...}],
#              "total": int}
#
# JD detail API (per posting):
#   GET https://{tenant}.{pod}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/job/{externalPath}
#   Response: {"jobPostingInfo": {jobDescription: "<html>", ...}, ...}
#
# Per-tenant config required (no auto-discovery — pods/sites differ):
#   - workday_tenant: salesforce
#   - workday_pod:    wd12
#   - workday_site:   External_Career_Site
# UNTESTED for many tenants — verify per company with -c <name>.

WORKDAY_PAGE_SIZE = 20
WORKDAY_MAX_PAGES = 50


def _workday_base(spec: dict) -> str | None:
    tenant = (spec.get('workday_tenant') or '').strip()
    pod = (spec.get('workday_pod') or '').strip()
    site = (spec.get('workday_site') or '').strip()
    if not (tenant and pod and site):
        return None
    return f'https://{tenant}.{pod}.myworkdayjobs.com/wday/cxs/{tenant}/{site}'


def list_workday(spec: dict, timeout: int) -> list[dict]:
    company = spec.get('name', '')
    base = _workday_base(spec)
    if not base:
        print(f'    workday config incomplete for {company}: need workday_tenant/pod/site')
        return []
    list_url = base + '/jobs'
    out: list[dict] = []
    for page in range(WORKDAY_MAX_PAGES):
        body = {
            'appliedFacets': {},
            'limit': WORKDAY_PAGE_SIZE,
            'offset': page * WORKDAY_PAGE_SIZE,
            'searchText': '',
        }
        try:
            payload = post_json(list_url, body, timeout=timeout)
        except Exception as exc:
            print(f'    workday page {page} error ({company}): {exc}')
            break
        postings = payload.get('jobPostings') or []
        if not postings:
            break
        for raw in postings:
            title = collapse_ws(raw.get('title') or '')
            location = collapse_ws(raw.get('locationsText') or '')
            external_path = (raw.get('externalPath') or '').strip()
            req_id = str(raw.get('bulletFields', [None])[0] or raw.get('jobReqId') or '')
            tenant_url = base.replace('/wday/cxs/', '/').replace(f'/{spec.get("workday_tenant","")}/{spec.get("workday_site","")}', '')
            full_url = (
                f'https://{spec.get("workday_tenant","")}.{spec.get("workday_pod","")}.myworkdayjobs.com'
                f'/{spec.get("workday_site","")}{external_path}'
            )
            out.append(_build_job(title, company, location, full_url, '', 'workday', req_id))
        if len(postings) < WORKDAY_PAGE_SIZE:
            break
    return out


def jd_workday(url: str, timeout: int, *, spec_cache: dict[str, dict] | None = None) -> str:
    """Fetch a Workday JD by translating the public URL back to its CXS endpoint."""
    if not url:
        return ''
    # Public form:  https://{tenant}.{pod}.myworkdayjobs.com/{site}/job/...
    # CXS form:     https://{tenant}.{pod}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/job/...
    m = re.match(
        r'^https://(?P<tenant>[^.]+)\.(?P<pod>wd\d+)\.myworkdayjobs\.com/(?P<site>[^/]+)(?P<path>/job/.+)$',
        url,
    )
    if not m:
        # Fallback: just try the public URL and html-extract.
        try:
            return jd_from_html(fetch_url(url, timeout=timeout))
        except Exception:
            return ''
    cxs = (
        f'https://{m["tenant"]}.{m["pod"]}.myworkdayjobs.com/wday/cxs/{m["tenant"]}/{m["site"]}{m["path"]}'
    )
    try:
        payload = fetch_json(cxs, timeout=timeout)
    except Exception:
        try:
            return jd_from_html(fetch_url(url, timeout=timeout))
        except Exception:
            return ''
    info = payload.get('jobPostingInfo') or {}
    desc = info.get('jobDescription') or ''
    return jd_from_html(desc)


# ============================================================================
# TikTok Careers — lifeattiktok.com  (was careers.tiktok.com before 2025 migration)
# ============================================================================
# Listing API (POST, JSON body):
#   POST https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts
#   Required header: website-path: tiktok
#   Body: {"keyword":"","limit":50,"offset":0}
#   Response: {code:0, data:{count:N, job_post_list:[...]}}
#   Each post has: id, title, description, requirement, city_info{en_name, parent{en_name}}
#   JD detail URL: https://lifeattiktok.com/search/{id}

TIKTOK_PAGE_SIZE = 50
TIKTOK_MAX_PAGES = 50

_TIKTOK_API_URL = 'https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts'
_TIKTOK_EXTRA_HEADERS = {
    'website-path': 'tiktok',
    'Accept-Language': 'en-US',
    'Referer': 'https://lifeattiktok.com/',
}


def _tiktok_extract_location(raw: dict) -> str:
    """Extract location string from a TikTok job post.

    New API shape: city_info is a single dict with en_name and nested parent
    (state > country). Old shape had city_list as a list. Handle both.
    """
    # New API: single city_info dict
    city_info = raw.get('city_info')
    if isinstance(city_info, dict):
        city = city_info.get('en_name') or city_info.get('i18n_name') or city_info.get('name') or ''
        state_info = city_info.get('parent') or {}
        state = state_info.get('en_name') or ''
        if city and state:
            return f'{city}, {state}'
        return city or state

    # Legacy: city_list as array
    locs = raw.get('city_list') or raw.get('locations') or raw.get('location_list') or []
    loc_names: list[str] = []
    for loc in (locs if isinstance(locs, list) else []):
        if isinstance(loc, dict):
            n = loc.get('name') or loc.get('en_name') or loc.get('location_name') or loc.get('city_name') or ''
            if n:
                loc_names.append(str(n))
        elif isinstance(loc, str):
            loc_names.append(loc)
    return ' | '.join(loc_names) or collapse_ws(str(raw.get('location') or ''))


def list_tiktok(spec: dict, timeout: int) -> list[dict]:
    company = spec.get('name', 'TikTok')
    out: list[dict] = []

    # Server-side filters from config (same IDs as USDS portal)
    cat_ids = spec.get('tiktok_job_category_id_list', [])
    loc_codes = spec.get('tiktok_location_code_list', [])
    recruit_ids = spec.get('tiktok_recruitment_id_list', [])

    for page in range(TIKTOK_MAX_PAGES):
        offset = page * TIKTOK_PAGE_SIZE
        body: dict = {'keyword': '', 'limit': TIKTOK_PAGE_SIZE, 'offset': offset}
        if cat_ids:
            body['job_category_id_list'] = cat_ids
        if loc_codes:
            body['location_code_list'] = loc_codes
        if recruit_ids:
            body['recruitment_id_list'] = recruit_ids
        try:
            payload = post_json(_TIKTOK_API_URL, body, timeout=timeout,
                                headers=_TIKTOK_EXTRA_HEADERS)
        except Exception as exc:
            print(f'    tiktok page {page} error: {exc}')
            break
        data = payload.get('data') or {}
        posts = data.get('job_post_list') or []
        if not posts:
            break
        for raw in posts:
            if not isinstance(raw, dict):
                continue
            title = collapse_ws(raw.get('title') or '')
            location = _tiktok_extract_location(raw)
            posting_id = str(raw.get('id') or '')
            full_url = f'https://lifeattiktok.com/search/{posting_id}' if posting_id else ''
            # JD is inline: description (responsibilities) + requirement (qualifications)
            desc = raw.get('description') or ''
            req = raw.get('requirement') or ''
            jd_text = jd_from_html(desc)
            if req:
                req_text = jd_from_html(req)
                if req_text:
                    jd_text = f'{jd_text}\n\n{req_text}' if jd_text else req_text
            out.append(_build_job(title, company, location, full_url, jd_text, 'tiktok_careers', posting_id))
        if len(posts) < TIKTOK_PAGE_SIZE:
            break
    return out


def jd_tiktok(url: str, timeout: int) -> str:
    """Fetch JD for a single TikTok job. Tries the listing API with ID filter,
    then falls back to scraping the HTML detail page."""
    if not url:
        return ''
    m = re.search(r'/search/(\d+)', url) or re.search(r'/position/(\d+)', url)
    if m:
        body = {'keyword': '', 'limit': 1, 'offset': 0}
        # The list API doesn't filter by ID, so we scrape the detail page instead
        pass
    # Scrape the HTML page (lifeattiktok.com is SSR, so content is in initial HTML)
    try:
        html = fetch_url(url, timeout=timeout, headers=_TIKTOK_EXTRA_HEADERS)
        return jd_from_html(html)
    except Exception:
        return ''


# ============================================================================
# TikTok USDS (careers.tiktokusds.com)
# ============================================================================
# Listing API (POST, requires browser _signature — use Playwright):
#   POST https://careers.tiktokusds.com/api/v1/search/job/posts
#        ?keyword=&limit=50&offset=0&portal_type=10&...&_signature=...
#   Body: {"keyword":"","limit":50,"offset":0,"portal_type":10,...}
#   Response: same shape as tiktok_careers
#   JD detail URL: https://careers.tiktokusds.com/usds/position/{id}/detail

_USDS_PLAYWRIGHT_AVAILABLE: bool | None = None  # lazy check

# CDP endpoint for ai-chrome (always-on Chrome with --remote-debugging-port=9222)
_CDP_URL = 'http://localhost:9222'


def _check_playwright() -> bool:
    global _USDS_PLAYWRIGHT_AVAILABLE
    if _USDS_PLAYWRIGHT_AVAILABLE is not None:
        return _USDS_PLAYWRIGHT_AVAILABLE
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        _USDS_PLAYWRIGHT_AVAILABLE = True
    except ImportError:
        _USDS_PLAYWRIGHT_AVAILABLE = False
        print('    [tiktok_usds] playwright not installed — skipping')
    return _USDS_PLAYWRIGHT_AVAILABLE


@contextmanager
def _playwright_page():
    """Context manager: open a page in ai-chrome (CDP), fallback to headless launch.

    Yields a Page.  On CDP the page is opened inside the browser's default
    BrowserContext (new_context() is broken over CDP — the context gets
    immediately closed by Chromium).  The page is closed on exit.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # Try connecting to the always-on ai-chrome first
        try:
            browser = p.chromium.connect_over_cdp(_CDP_URL, timeout=5000)
            print(f'    [tiktok_usds] connected to ai-chrome via CDP ({_CDP_URL})')
            ctx = browser.contexts[0]  # reuse default context — new_context() breaks over CDP
            page = ctx.new_page()
            try:
                yield page
            finally:
                page.close()
        except Exception as exc:
            if 'connect_over_cdp' in str(exc) or 'Target' in str(exc):
                # CDP not available — fallback to headless
                print('    [tiktok_usds] CDP unavailable, launching headless Chromium')
            else:
                print(f'    [tiktok_usds] CDP error ({exc}), falling back to headless')
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                try:
                    yield page
                finally:
                    page.close()
            finally:
                browser.close()


def list_tiktok_usds(spec: dict, timeout: int) -> list[dict]:
    """Fetch all USDS jobs via Playwright (prefers CDP to ai-chrome)."""
    if not _check_playwright():
        return []

    company = spec.get('name', 'TikTok USDS')
    out: list[dict] = []

    # Server-side filters from config (e.g. R&D category, location)
    cat_ids = spec.get('usds_job_category_id_list', [])
    loc_codes = spec.get('usds_location_code_list', [])
    recruit_ids = spec.get('usds_recruitment_id_list', [])

    with _playwright_page() as page:
        # Load the USDS careers page to initialize cookies/signature
        page.goto('https://careers.tiktokusds.com/usds/position', timeout=30000)
        page.wait_for_timeout(3000)

        offset = 0
        limit = TIKTOK_PAGE_SIZE
        total = None

        while True:
            js_code = """async (params) => {
                const resp = await fetch(
                    '/api/v1/search/job/posts?keyword=&limit=' + params.limit
                    + '&offset=' + params.offset
                    + '&portal_type=10&job_category_id_list=' + params.cat_ids.join(',')
                    + '&tag_id_list=&location_code_list=' + params.loc_codes.join(',')
                    + '&subject_id_list=&recruitment_id_list=' + params.recruit_ids.join(',')
                    + '&job_function_id_list=&storefront_id_list=&portal_entrance=1',
                    {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'website-path': 'usds',
                            'portal-channel': 'tiktok',
                            'portal-platform': 'pc'
                        },
                        body: JSON.stringify({
                            keyword: '',
                            limit: params.limit,
                            offset: params.offset,
                            portal_type: 10,
                            job_category_id_list: params.cat_ids,
                            tag_id_list: [],
                            location_code_list: params.loc_codes,
                            subject_id_list: [],
                            recruitment_id_list: params.recruit_ids,
                            job_function_id_list: [],
                            storefront_id_list: [],
                            portal_entrance: 1
                        })
                    }
                );
                return await resp.json();
            }"""

            try:
                result = page.evaluate(js_code, {
                    'limit': limit, 'offset': offset,
                    'cat_ids': cat_ids, 'loc_codes': loc_codes,
                    'recruit_ids': recruit_ids,
                })
            except Exception as exc:
                print(f'    tiktok_usds page evaluate error at offset {offset}: {exc}')
                break

            data = result.get('data') or {}
            posts = data.get('job_post_list') or []
            count = data.get('count') or 0

            if total is None:
                total = count
                print(f'    tiktok_usds: {total} total jobs found')

            if not posts:
                break

            for raw in posts:
                if not isinstance(raw, dict):
                    continue
                title = collapse_ws(raw.get('title') or '')
                location = _tiktok_extract_location(raw)
                posting_id = str(raw.get('id') or '')
                full_url = (
                    f'https://careers.tiktokusds.com/usds/position/{posting_id}/detail'
                    if posting_id else ''
                )
                desc = raw.get('description') or ''
                req = raw.get('requirement') or ''
                jd_text = jd_from_html(desc)
                if req:
                    req_text = jd_from_html(req)
                    if req_text:
                        jd_text = f'{jd_text}\n\n{req_text}' if jd_text else req_text
                out.append(_build_job(title, company, location, full_url,
                                     jd_text, 'tiktok_usds', posting_id))

            offset += limit
            if offset >= total or len(posts) < limit:
                break

    return out


def jd_tiktok_usds(url: str, timeout: int) -> str:
    """Fetch JD for a single USDS job via Playwright (prefers CDP to ai-chrome)."""
    if not url or not _check_playwright():
        return ''

    try:
        with _playwright_page() as page:
            page.goto(url, timeout=30000)
            page.wait_for_timeout(3000)
            # Extract JD text from the rendered page
            text = page.evaluate("""() => {
                const main = document.querySelector('main') || document.body;
                return main.innerText || '';
            }""")
            return text.strip() if text else ''
    except Exception:
        return ''


# ============================================================================
# Phenom (used by Netflix and many other enterprise career sites).
# ============================================================================
# Listing API:
#   GET https://{phenom_host}/api/apply/v2/jobs?domain={phenom_domain}&num=100&start=N
#   Response: {"positions": [{id, name, posting_name, location, locations,
#                              department, business_unit, ats_job_id,
#                              canonicalPositionUrl, ...}]}
#   Listing positions DO NOT include job_description — that's a per-job fetch.
#
# Per-job (full JD):
#   GET https://{phenom_host}/api/apply/v2/jobs/{id}?domain={phenom_domain}
#   Response: same record but WITH job_description field (HTML).
#
# Per-tenant config:
#   phenom_host:   explore.jobs.netflix.net
#   phenom_domain: netflix.com

PHENOM_PAGE_SIZE = 10  # Phenom hard-caps responses at 10 regardless of `num`
PHENOM_MAX_PAGES = 200  # cap at PAGE_SIZE*MAX_PAGES = 2000 jobs


def list_phenom(spec: dict, timeout: int) -> list[dict]:
    company = spec.get('name', '')
    host = (spec.get('phenom_host') or '').strip().rstrip('/')
    domain = (spec.get('phenom_domain') or '').strip()
    if not host or not domain:
        print(f'    phenom config incomplete for {company}: need phenom_host/phenom_domain')
        return []
    out: list[dict] = []
    for page in range(PHENOM_MAX_PAGES):
        start = page * PHENOM_PAGE_SIZE
        url = f'https://{host}/api/apply/v2/jobs?domain={quote_plus(domain)}&num={PHENOM_PAGE_SIZE}&start={start}'
        try:
            payload = fetch_json(url, timeout=timeout)
        except Exception as exc:
            print(f'    phenom page {page} error ({company}): {exc}')
            break
        positions = payload.get('positions') or []
        if not positions:
            break
        for raw in positions:
            if not isinstance(raw, dict):
                continue
            title = collapse_ws(raw.get('name') or raw.get('posting_name') or '')
            locs = raw.get('locations') or ([raw.get('location')] if raw.get('location') else [])
            location = ' | '.join(collapse_ws(str(loc)) for loc in locs if loc)
            ext_url = (raw.get('canonicalPositionUrl') or '').strip()
            # Strip the ?microsite=... query if present, for a stable URL
            if '?' in ext_url:
                ext_url = ext_url.split('?', 1)[0]
            ats_id = str(raw.get('id') or raw.get('ats_job_id') or '')
            out.append(_build_job(title, company, location, ext_url, '', 'phenom', ats_id))
        if len(positions) < PHENOM_PAGE_SIZE:
            break
    return out


def jd_phenom(url: str, timeout: int) -> str:
    """Fetch a Phenom JD by translating the canonical careers URL to its API form."""
    if not url:
        return ''
    # canonical:  https://{host}/careers/job/{id}
    # api:        https://{host}/api/apply/v2/jobs/{id}?domain={domain}
    m = re.match(r'^https://(?P<host>[^/]+)/careers/job/(?P<id>\d+)', url)
    if not m:
        # Fallback: try the URL as HTML (won't have JD, but safe)
        try:
            return jd_from_html(fetch_url(url, timeout=timeout))
        except Exception:
            return ''
    host = m.group('host')
    job_id = m.group('id')
    # Phenom domain is normally the registrable host part. We don't know it
    # from the URL alone; pass an empty domain — Phenom returns the record
    # regardless when fetched by id. If that fails, fall back to HTML.
    api_url = f'https://{host}/api/apply/v2/jobs/{job_id}?domain='
    try:
        payload = fetch_json(api_url, timeout=timeout)
    except Exception:
        try:
            return jd_from_html(fetch_url(url, timeout=timeout))
        except Exception:
            return ''
    desc = payload.get('job_description') or ''
    return jd_from_html(desc)


# ============================================================================
# Adapter registry
# ============================================================================
# (list_fn, jd_fetch_fn). jd_fetch_fn=None means JD is inline in the listing.

ATS_ADAPTERS: dict[str, tuple[Callable[[dict, int], list[dict]], Callable[[str, int], str] | None]] = {
    'greenhouse':     (list_greenhouse, None),
    'lever':          (list_lever, None),
    'ashby':          (list_ashby, None),
    'shopify_careers': (list_shopify_careers, None),
    'cursor_careers': (list_cursor_careers, jd_cursor),
    'pcsx':           (list_pcsx, jd_pcsx),
    'apple_jobs':     (list_apple, jd_apple),
    'amazon_jobs':    (list_amazon, jd_amazon),
    'google_careers': (list_google, jd_google),
    'meta_careers':   (list_meta, jd_meta),
    'workday':        (list_workday, jd_workday),
    'phenom':         (list_phenom, jd_phenom),
    'tiktok_careers': (list_tiktok, jd_tiktok),
    'tiktok_usds':    (list_tiktok_usds, jd_tiktok_usds),
    'remoteok':       (list_remoteok, None),
    'weworkremotely': (list_weworkremotely, None),
}


# ============================================================================
# Title filter, dedup, output (unchanged shape)
# ============================================================================

def build_title_filter(cfg: dict) -> tuple[list[re.Pattern], list[re.Pattern]]:
    scan = nested_get(cfg, 'scan') or {}
    include_terms = scan.get('title_include') or []
    exclude_terms = scan.get('title_exclude') or []
    include_pats = [re.compile(re.escape(t), re.I) for t in include_terms]
    exclude_pats = [re.compile(re.escape(t), re.I) for t in exclude_terms]
    return include_pats, exclude_pats


def title_matches(title: str, include_pats: list[re.Pattern], exclude_pats: list[re.Pattern]) -> bool:
    if not include_pats:
        return True
    if any(p.search(title) for p in exclude_pats):
        return False
    return any(p.search(title) for p in include_pats)


def _company_title_filter(
    company_cfg: dict,
    global_inc: list[re.Pattern],
    global_exc: list[re.Pattern],
) -> tuple[list[re.Pattern], list[re.Pattern]]:
    """Return (include_pats, exclude_pats) for a company.

    If the company spec has its own title_include / title_exclude lists,
    those *replace* the global patterns for that company.
    """
    co_inc = company_cfg.get('title_include')
    co_exc = company_cfg.get('title_exclude')
    inc = [re.compile(re.escape(t), re.I) for t in co_inc] if co_inc else global_inc
    exc = [re.compile(re.escape(t), re.I) for t in co_exc] if co_exc else global_exc
    return inc, exc


# ---- Location filter -------------------------------------------------------
_REMOTE_KEYWORDS = re.compile(r'\bremote\b', re.I)
_HYBRID_KEYWORDS = re.compile(r'\bhybrid\b', re.I)


def build_location_filter(cfg: dict) -> tuple[list[re.Pattern], bool]:
    """Return (allowed_city_patterns, allow_remote) from config.

    If no location_filter is configured, returns ([], True) which means
    everything passes (backward compatible).
    """
    scan = nested_get(cfg, 'scan') or {}
    loc_cfg = scan.get('location_filter') or {}
    cities = loc_cfg.get('allowed_cities') or []
    allow_remote = loc_cfg.get('allow_remote', True)
    city_pats = [re.compile(re.escape(c), re.I) for c in cities]
    return city_pats, allow_remote


def location_passes(location: str, city_pats: list[re.Pattern], allow_remote: bool) -> bool:
    """Return True if the job location passes the location filter.

    Rules:
    - If no city_pats configured, everything passes (no filter).
    - Remote/hybrid jobs pass if allow_remote is True.
    - Onsite jobs must mention at least one allowed city.
    - Empty location string passes (benefit of the doubt).
    """
    if not city_pats:
        return True  # no filter configured
    if not location:
        return True  # unknown location — let it through
    if allow_remote and (_REMOTE_KEYWORDS.search(location) or _HYBRID_KEYWORDS.search(location)):
        return True
    return any(p.search(location) for p in city_pats)


def load_seen_keys(env: dict, cfg: dict) -> set[str]:
    seen_path = seen_jobs_json_path(env, cfg)
    db = read_json(seen_path, {'jobs': []})
    return {job['job_key'] for job in db.get('jobs', []) if job.get('job_key')}


def upsert_seen_jobs(env: dict, cfg: dict, jobs: list[dict]) -> None:
    seen_path = seen_jobs_json_path(env, cfg)
    db = read_json(seen_path, {'jobs': []})
    existing = {item['job_key']: item for item in db.get('jobs', []) if item.get('job_key')}
    now = datetime.now(timezone.utc).isoformat()
    for job in jobs:
        key = job['job_key']
        if key in existing:
            existing[key]['last_seen_at'] = now
        else:
            existing[key] = {
                'job_key': key,
                'first_seen_at': now,
                'last_seen_at': now,
                'title': job.get('title', ''),
                'company': job.get('company', ''),
                'location': job.get('location', ''),
                'linkedin_url': '',
                'external_jd_url': job.get('external_jd_url', ''),
            }
    db['jobs'] = sorted(existing.values(), key=lambda item: item.get('last_seen_at', ''))
    write_json_snapshot_and_history(env, seen_path, db, history_group='seen_jobs')


def jobs_target_scan_json_path(env: dict, cfg: dict) -> Path:
    root = project_root(env)
    spec = nested_get(cfg, 'jobs_target_scan_json') or {}
    rel_path = spec.get('path') or 'data/inbox/jobs_target_scan.json'
    return root / rel_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Scan target company ATS pages for matching jobs.')
    parser.add_argument(
        '--company', '-c',
        metavar='NAME',
        help='Only scan this company (case-insensitive, partial match). Can be repeated.',
        action='append',
        default=[],
    )
    parser.add_argument('--include-seen', action='store_true', help='Include already-seen jobs (skip dedup).')
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    company_filter = [f.lower() for f in args.company]

    env = load_env()
    cfg = load_project_config(env)
    timeout = int(env.get('JOB_HTTP_TIMEOUT_SECONDS', str(DEFAULT_TIMEOUT)))
    max_jd = int(env.get('JOB_SCAN_MAX_JD_FETCHES_PER_COMPANY', str(DEFAULT_MAX_JD_FETCHES_PER_COMPANY)))
    output_path = jobs_target_scan_json_path(env, cfg)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    include_pats, exclude_pats = build_title_filter(cfg)
    city_pats, allow_remote = build_location_filter(cfg)
    seen_keys = set() if args.include_seen else load_seen_keys(env, cfg)

    companies = nested_get(cfg, 'target_companies') or []
    all_scanned: list[dict] = []
    all_new: list[dict] = []

    for company_cfg in companies:
        if not isinstance(company_cfg, dict):
            continue
        if not company_cfg.get('enabled', True):
            continue
        name = company_cfg.get('name', '')
        if company_filter and not any(f in name.lower() for f in company_filter):
            continue
        ats = (company_cfg.get('ats') or '').lower()
        list_fn, jd_fn = ATS_ADAPTERS.get(ats, (None, None))
        if not list_fn:
            print(f'Skipping {name}: ats {ats!r} not supported')
            continue
        print(f'Scanning {name} ({ats})...')
        spec = {**company_cfg, 'name': name}
        try:
            jobs = list_fn(spec, timeout)
        except Exception as exc:
            print(f'  list error: {exc}')
            continue

        matched = [j for j in jobs if title_matches(j['title'], *_company_title_filter(company_cfg, include_pats, exclude_pats))]
        loc_passed = [j for j in matched if location_passes(j.get('location', ''), city_pats, allow_remote)]
        new_jobs = [j for j in loc_passed if j['job_key'] not in seen_keys]

        if jd_fn and new_jobs:
            jd_targets = [j for j in new_jobs if not j.get('jd_text')]
            if jd_targets:
                print(f'  fetching JD for {min(len(jd_targets), max_jd)} of {len(jd_targets)} new matches')
            for j in jd_targets[:max_jd]:
                try:
                    text = jd_fn(j.get('external_jd_url', ''), timeout)
                except Exception as exc:
                    print(f'    jd fetch error ({j.get("external_jd_url","")}): {exc}')
                    continue
                j['jd_text'] = text
                j['jd_text_preview'] = text[:1200]

        print(f'  {len(jobs)} total → {len(matched)} title match → {len(loc_passed)} location match → {len(new_jobs)} new')
        all_scanned.extend(loc_passed)
        all_new.extend(new_jobs)

    upsert_seen_jobs(env, cfg, all_scanned)

    out = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source': 'target_scan',
        'job_count': len(all_new),
        'resolved_count': len(all_new),
        'jobs': all_new,
    }
    history_path, history_log = write_json_snapshot_and_history(
        env, output_path, out, history_group='jobs_target_scan'
    )
    print(f'wrote {output_path} ({len(all_new)} new jobs)')
    print(f'archived: {history_path}')


if __name__ == '__main__':
    main()
