#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from functools import lru_cache
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from job_scout_lib import (
    collapse_ws,
    default_cafile,
    external_search_enabled,
    external_search_site_filters,
    host_of,
    html_to_lines,
    is_http_url,
    is_linkedin_url,
    jobs_enriched_json_path,
    jobs_inbox_json_path,
    load_env,
    load_project_config,
    looks_like_linkedin_job_url,
    nested_get,
    read_json,
    unwrap_url,
    write_json_snapshot_and_history,
)

SCRIPT_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(?P<body>.*?)</script>',
    re.I | re.S,
)
TITLE_RE = re.compile(r'<title[^>]*>(?P<title>.*?)</title>', re.I | re.S)
COMPANY_SUFFIX_RE = re.compile(
    r'\b(?:inc|inc\.|llc|l\.l\.c\.|corp|corporation|co|company|ltd|limited|group|holdings?|technologies|technology)\b',
    re.I,
)
CAMEL_CASE_RE = re.compile(r'(?<=[a-z])(?=[A-Z])')

DEFAULT_SEARCH_FILTERS = [
    'greenhouse.io',
    'lever.co',
    'ashbyhq.com',
    'myworkdayjobs.com',
    'smartrecruiters.com',
]
BING_HOST_SUFFIXES = ('bing.com', 'www.bing.com')
BAD_RESULT_HOSTS = {
    'youtube.com',
    'www.youtube.com',
    'facebook.com',
    'www.facebook.com',
    'instagram.com',
    'www.instagram.com',
    'reddit.com',
    'www.reddit.com',
    'zhihu.com',
    'www.zhihu.com',
    'finance.yahoo.com',
}
BAD_PAGE_HINTS = (
    '/docs/',
    '/documentation/',
    '/blog/',
    '/news/',
    '/article',
    'learn.microsoft.com',
)
JOB_HOST_HINTS = (
    'greenhouse',
    'lever',
    'ashby',
    'smartrecruiters',
    'myworkdayjobs',
    'workday',
    'icims',
    'jobvite',
    'careers',
    'jobs',
)
JOB_TEXT_HINTS = (
    'responsibilities',
    'requirements',
    'qualifications',
    'preferred qualifications',
    'minimum qualifications',
    'what you will do',
    "what you'll do",
    'about the role',
    'about the team',
    'benefits',
    'compensation',
    'salary range',
    'job description',
)
MIN_SEARCH_SCORE = 6
GENERIC_ROLE_TOKENS = {
    'engineer', 'engineering', 'manager', 'lead', 'staff', 'principal', 'senior',
    'sr', 'ii', 'iii', 'iv', 'technical',
}
DEFAULT_COMPANY_RESOLVERS = {
    'Microsoft': {'type': 'microsoft_pcsx', 'domain': 'microsoft.com'},
    'Microsoft AI': {'type': 'microsoft_pcsx', 'domain': 'microsoft.com'},
    'Stripe': {'type': 'greenhouse', 'board_slug': 'stripe'},
    'OfferUp': {'type': 'greenhouse', 'board_slug': 'offerup'},
    'Visa': {'type': 'smartrecruiters', 'company_slug': 'visa'},
    'Whatnot': {'type': 'ashby', 'board_slug': 'whatnot'},
}
ASHBY_OPENING_RE = re.compile(
    r'{"id":"(?P<id>[0-9a-f-]{36})","title":"(?P<title>(?:\\.|[^"])*)","updatedAt":"(?P<updated_at>[^"]*)"'
    r'.*?"locationName":"(?P<location>(?:\\.|[^"])*)".*?"workplaceType":(?:"(?P<workplace>[^"]*)"|null)'
    r'.*?"secondaryLocations":(?P<secondary>\[[^\]]*\])',
    re.S,
)
CURSOR_LINK_RE = re.compile(
    r'<a[^>]+href="(?P<href>/careers/[^"]+)"[^>]*>(?P<body>.*?)</a>',
    re.I | re.S,
)
CURSOR_P_RE = re.compile(r'<p[^>]*>(?P<text>.*?)</p>', re.I | re.S)
CURSOR_SPAN_RE = re.compile(r'<span[^>]*>(?P<text>.*?)</span>', re.I | re.S)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Resolve and fetch external JD pages only (never linkedin.com).')
    parser.add_argument('--in', dest='input_path', default='')
    parser.add_argument('--out', default='')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--debug', action='store_true')
    return parser.parse_args()


def fetch_url(url: str, timeout: int = 20) -> str:
    req = Request(
        url,
        headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/124.0 Safari/537.36'
        },
    )
    cafile = default_cafile()
    ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
    with urlopen(req, timeout=timeout, context=ctx) as resp:
        charset = resp.headers.get_content_charset() or 'utf-8'
        return resp.read().decode(charset, errors='ignore')


def fetch_json_url(url: str, timeout: int = 20) -> Any:
    return json.loads(fetch_url(url, timeout=timeout))


def find_jobposting_objects(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        node_type = value.get('@type') or value.get('type')
        if isinstance(node_type, list):
            node_types = [str(x).lower() for x in node_type]
        else:
            node_types = [str(node_type).lower()] if node_type else []
        if any('jobposting' in node for node in node_types):
            found.append(value)
        for child in value.values():
            found.extend(find_jobposting_objects(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(find_jobposting_objects(child))
    return found


def extract_jsonld_jobposting(html: str) -> dict[str, Any] | None:
    for match in SCRIPT_JSONLD_RE.finditer(html):
        raw = match.group('body').strip()
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        postings = find_jobposting_objects(parsed)
        if postings:
            return postings[0]
    return None


def extract_page_title(html: str) -> str:
    match = TITLE_RE.search(html)
    return collapse_ws(match.group('title')) if match else ''


def html_fragment_to_text(value: str) -> str:
    return '\n'.join(html_to_lines(unescape(value)))


def stringify_job_location(value: Any) -> str:
    if isinstance(value, list):
        return ' | '.join(part for part in (stringify_job_location(item) for item in value) if part)
    if isinstance(value, dict):
        address = value.get('address')
        if isinstance(address, dict):
            country = address.get('addressCountry')
            if isinstance(country, dict):
                country = country.get('name') or country.get('code') or ''
            parts = [
                address.get('addressLocality', ''),
                address.get('addressRegion', ''),
                country or '',
            ]
            text = ', '.join(collapse_ws(str(part)) for part in parts if collapse_ws(str(part)))
            if text:
                return text
        parts = [value.get('name'), value.get('addressLocality'), value.get('addressRegion')]
        text = ', '.join(collapse_ws(str(part)) for part in parts if part)
        return text
    return collapse_ws(str(value or ''))


def extract_jd_text(html: str) -> tuple[str, dict[str, Any]]:
    posting = extract_jsonld_jobposting(html)
    if posting:
        description = posting.get('description') or posting.get('jobDescription') or ''
        title = collapse_ws(str(posting.get('title') or posting.get('name') or ''))
        company = ''
        org = posting.get('hiringOrganization')
        if isinstance(org, dict):
            company = collapse_ws(str(org.get('name') or ''))
        return html_to_lines(str(description))[0] if False else '\n'.join(html_to_lines(str(description))), {
            'jobposting_title': title,
            'jobposting_company': company,
            'jobposting_location': stringify_job_location(posting.get('jobLocation', '')),
            'parser': 'jsonld_jobposting',
        }
    lines = html_to_lines(html)
    useful = []
    for line in lines:
        low = line.lower()
        if len(line) < 2:
            continue
        if any(token in low for token in ['cookie', 'privacy policy', 'terms of service', 'sign in', 'log in']):
            continue
        useful.append(line)
    return '\n'.join(useful[:250]), {
        'jobposting_title': extract_page_title(html),
        'jobposting_company': '',
        'jobposting_location': '',
        'parser': 'html_fallback',
    }


def configured_resolvers(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    merged = dict(DEFAULT_COMPANY_RESOLVERS)
    configured = nested_get(cfg, 'external_jd', 'resolvers', default={})
    if isinstance(configured, dict):
        for company, spec in configured.items():
            if isinstance(spec, dict):
                merged[str(company)] = spec
    return merged


def resolver_spec_for_job(job: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any] | None:
    company = collapse_ws(job.get('company', ''))
    if not company:
        return None
    resolvers = configured_resolvers(cfg)
    if company in resolvers:
        return resolvers[company]
    lowered = company.lower()
    for key, spec in resolvers.items():
        if key.lower() == lowered:
            return spec
    return None


def normalized_tokens(value: str) -> list[str]:
    expanded = str(value or '').lower().replace('&', ' and ').replace('fullstack', 'full stack')
    out: list[str] = []
    for token in re.findall(r'[a-z0-9]+', expanded):
        if token == 'swe':
            out.extend(['software', 'engineer'])
            continue
        if token == 'sde':
            out.extend(['software', 'development', 'engineer'])
            continue
        out.append(token)
    return out


def location_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for token in normalized_tokens(value):
        if len(token) >= 3 or token.isdigit():
            tokens.add(token)
    return tokens


def title_match_score(target: str, candidate: str) -> int:
    target_norm = ' '.join(normalized_tokens(target))
    candidate_norm = ' '.join(normalized_tokens(candidate))
    target_tokens = set(normalized_tokens(target))
    candidate_tokens = set(normalized_tokens(candidate))
    shared = target_tokens & candidate_tokens
    meaningful_shared = shared - GENERIC_ROLE_TOKENS
    score = 0
    if target_norm and target_norm == candidate_norm:
        score += 20
    elif target_norm and candidate_norm and (target_norm in candidate_norm or candidate_norm in target_norm):
        score += 10
    score += len(meaningful_shared) * 4
    score += len(shared & GENERIC_ROLE_TOKENS)
    if meaningful_shared and len(shared) >= 3:
        score += 4
    if 'software' in shared and 'engineer' in shared:
        score += 3
    if {'full', 'stack'} <= shared:
        score += 2
    if 'ml' in shared:
        score += 2
    if ('manager' in target_tokens) != ('manager' in candidate_tokens):
        score -= 3
    if ('engineer' in target_tokens) != ('engineer' in candidate_tokens):
        score -= 2
    return score


def location_match_score(target: str, candidate: str) -> int:
    target_tokens = location_tokens(target)
    candidate_tokens = location_tokens(candidate)
    if not target_tokens or not candidate_tokens:
        return 0
    shared = target_tokens & candidate_tokens
    if not shared:
        return 0
    target_norm = ' '.join(sorted(target_tokens))
    candidate_norm = ' '.join(sorted(candidate_tokens))
    if target_norm and (target_norm == candidate_norm or target_norm in candidate_norm or candidate_norm in target_norm):
        return 4
    return 2


def location_compatible(job_location: str, candidate_location: str) -> bool:
    job_location = collapse_ws(job_location)
    candidate_location = collapse_ws(candidate_location)
    if not job_location:
        return True
    if location_match_score(job_location, candidate_location) > 0:
        return True
    return 'remote' in candidate_location.lower()


def select_best_match(
    job: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    title_key: str = 'title',
    location_fn: callable | None = None,
) -> tuple[dict[str, Any] | None, int]:
    best_item: dict[str, Any] | None = None
    best_score = -10_000
    for item in items:
        title = collapse_ws(str(item.get(title_key, '')))
        location = collapse_ws(location_fn(item) if location_fn else str(item.get('location', '')))
        score = title_match_score(job.get('title', ''), title) + location_match_score(job.get('location', ''), location)
        if score > best_score:
            best_score = score
            best_item = item
    return best_item, best_score


@lru_cache(maxsize=64)
def greenhouse_board_jobs(board_slug: str, timeout: int) -> list[dict[str, Any]]:
    payload = fetch_json_url(
        f'https://boards-api.greenhouse.io/v1/boards/{board_slug}/jobs?content=true',
        timeout=timeout,
    )
    jobs = payload.get('jobs', []) if isinstance(payload, dict) else []
    return jobs if isinstance(jobs, list) else []


@lru_cache(maxsize=32)
def smartrecruiters_company_jobs(company_slug: str, timeout: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for offset in range(0, 300, 100):
        payload = fetch_json_url(
            f'https://api.smartrecruiters.com/v1/companies/{company_slug}/postings?limit=100&offset={offset}',
            timeout=timeout,
        )
        items = payload.get('content', []) if isinstance(payload, dict) else []
        if not isinstance(items, list) or not items:
            break
        out.extend(item for item in items if isinstance(item, dict))
        if len(items) < 100:
            break
    return out


@lru_cache(maxsize=64)
def smartrecruiters_job_detail(company_slug: str, posting_id: str, timeout: int) -> dict[str, Any]:
    payload = fetch_json_url(
        f'https://api.smartrecruiters.com/v1/companies/{company_slug}/postings/{posting_id}',
        timeout=timeout,
    )
    return payload if isinstance(payload, dict) else {}


@lru_cache(maxsize=32)
def ashby_board_openings(board_slug: str, timeout: int) -> list[dict[str, Any]]:
    html = fetch_url(f'https://jobs.ashbyhq.com/{board_slug}', timeout=timeout)
    openings: list[dict[str, Any]] = []
    for match in ASHBY_OPENING_RE.finditer(html):
        secondary_raw = match.group('secondary')
        secondary_locations = re.findall(r'"locationName":"((?:\\.|[^"])*)"', secondary_raw)
        openings.append({
            'id': bytes(match.group('id'), 'utf-8').decode('unicode_escape'),
            'title': bytes(match.group('title'), 'utf-8').decode('unicode_escape'),
            'location': bytes(match.group('location'), 'utf-8').decode('unicode_escape'),
            'workplace_type': bytes((match.group('workplace') or ''), 'utf-8').decode('unicode_escape'),
            'secondary_locations': [
                bytes(value, 'utf-8').decode('unicode_escape')
                for value in secondary_locations
            ],
            'updated_at': match.group('updated_at'),
        })
    return openings


def location_string_for_ashby(item: dict[str, Any]) -> str:
    parts = [item.get('location', ''), *item.get('secondary_locations', [])]
    return ' | '.join(collapse_ws(part) for part in parts if collapse_ws(part))


@lru_cache(maxsize=64)
def cached_fetch_url(url: str, timeout: int) -> str:
    return fetch_url(url, timeout=timeout)


def direct_resolution_result(
    *,
    job: dict[str, Any],
    url: str,
    source: str,
    jd_text: str,
    parser: str,
    title: str,
    company: str,
    location: str,
    debug_note: str = '',
) -> dict[str, Any]:
    return {
        'external_jd_status': 'resolved',
        'external_jd_url': url,
        'external_jd_source': source,
        'jd_text': jd_text,
        'jd_text_preview': jd_text[:1200],
        'jd_parser': parser,
        'jd_title': title or job.get('title', ''),
        'jd_company': company or job.get('company', ''),
        'jd_location': location or job.get('location', ''),
        'direct_resolver': source,
        'direct_resolver_debug': debug_note,
    }


@lru_cache(maxsize=8)
def cursor_careers_openings(timeout: int) -> list[dict[str, Any]]:
    html = cached_fetch_url('https://cursor.com/careers', timeout=timeout)
    openings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in CURSOR_LINK_RE.finditer(html):
        href = collapse_ws(match.group('href'))
        if not href or href == '/careers' or href in seen:
            continue
        body = match.group('body')
        title_match = CURSOR_P_RE.search(body)
        title = collapse_ws(unescape(title_match.group('text'))) if title_match else ''
        spans = [
            collapse_ws(unescape(item))
            for item in CURSOR_SPAN_RE.findall(body)
        ]
        spans = [
            item for item in spans
            if item and item not in {'·', 'Apply →', 'Apply'}
        ]
        location = spans[-1] if spans else ''
        if not title:
            continue
        openings.append({
            'title': title,
            'location': location.replace(';', ' | '),
            'url': 'https://cursor.com' + href,
        })
        seen.add(href)
    return openings


def resolve_via_microsoft_pcsx(job: dict[str, Any], spec: dict[str, Any], timeout: int) -> dict[str, Any] | None:
    title = collapse_ws(job.get('title', ''))
    location = collapse_ws(job.get('location', ''))
    domain = str(spec.get('domain') or 'microsoft.com').strip()
    url = (
        'https://apply.careers.microsoft.com/api/pcsx/search?domain=' + quote_plus(domain)
        + '&query=' + quote_plus(title)
        + '&location=' + quote_plus(location)
        + '&start=0'
    )
    payload = fetch_json_url(url, timeout=timeout)
    positions = payload.get('data', {}).get('positions', []) if isinstance(payload, dict) else []
    candidates = []
    for item in positions if isinstance(positions, list) else []:
        position_url = str(item.get('positionUrl') or '').strip()
        if not position_url:
            continue
        candidates.append({
            'title': item.get('name', ''),
            'location': ' | '.join(item.get('standardizedLocations') or item.get('locations') or []),
            'url': 'https://apply.careers.microsoft.com' + position_url,
        })
    best, score = select_best_match(job, candidates)
    if not best or score < 8:
        return None
    if not location_compatible(job.get('location', ''), best.get('location', '')):
        return None
    html = cached_fetch_url(best['url'], timeout=timeout)
    jd_text, meta = extract_jd_text(html)
    if len(collapse_ws(jd_text)) < 200:
        return None
    return direct_resolution_result(
        job=job,
        url=best['url'],
        source='direct_microsoft_pcsx',
        jd_text=jd_text,
        parser=meta.get('parser', 'jsonld_jobposting'),
        title=meta.get('jobposting_title') or best.get('title', ''),
        company=meta.get('jobposting_company') or job.get('company', ''),
        location=meta.get('jobposting_location') or best.get('location', '') or job.get('location', ''),
        debug_note=f'match_score={score}',
    )


def resolve_via_greenhouse(job: dict[str, Any], spec: dict[str, Any], timeout: int) -> dict[str, Any] | None:
    board_slug = str(spec.get('board_slug') or '').strip()
    if not board_slug:
        return None
    postings = greenhouse_board_jobs(board_slug, timeout)
    candidates = []
    for item in postings:
        candidates.append({
            'title': item.get('title', ''),
            'location': nested_get(item, 'location', 'name', default='') or '',
            'url': item.get('absolute_url', ''),
            'raw': item,
        })
    best, score = select_best_match(job, candidates)
    if not best or score < 8:
        return None
    if not location_compatible(job.get('location', ''), best.get('location', '')):
        return None
    raw = best['raw']
    jd_text = html_fragment_to_text(str(raw.get('content', '')))
    if len(collapse_ws(jd_text)) < 200:
        return None
    return direct_resolution_result(
        job=job,
        url=str(raw.get('absolute_url') or ''),
        source='direct_greenhouse_api',
        jd_text=jd_text,
        parser='greenhouse_api',
        title=str(raw.get('title') or ''),
        company=str(raw.get('company_name') or job.get('company', '')),
        location=str(nested_get(raw, 'location', 'name', default='') or job.get('location', '')),
        debug_note=f'board={board_slug};match_score={score}',
    )


def smartrecruiters_text(detail: dict[str, Any]) -> str:
    sections = nested_get(detail, 'jobAd', 'sections', default={})
    if not isinstance(sections, dict):
        return ''
    ordered = ['companyDescription', 'jobDescription', 'qualifications', 'additionalInformation']
    chunks: list[str] = []
    for key in ordered:
        section = sections.get(key)
        if not isinstance(section, dict):
            continue
        title = collapse_ws(str(section.get('title') or ''))
        text = html_fragment_to_text(str(section.get('text') or ''))
        if text:
            chunks.append(f'{title}\n{text}' if title else text)
    return '\n\n'.join(chunks)


def resolve_via_smartrecruiters(job: dict[str, Any], spec: dict[str, Any], timeout: int) -> dict[str, Any] | None:
    company_slug = str(spec.get('company_slug') or '').strip()
    if not company_slug:
        return None
    postings = smartrecruiters_company_jobs(company_slug, timeout)
    candidates = []
    for item in postings:
        candidates.append({
            'title': item.get('name', ''),
            'location': nested_get(item, 'location', 'fullLocation', default='') or '',
            'raw': item,
        })
    best, score = select_best_match(job, candidates)
    if not best or score < 8:
        return None
    if not location_compatible(job.get('location', ''), best.get('location', '')):
        return None
    raw = best['raw']
    detail = smartrecruiters_job_detail(company_slug, str(raw.get('id') or ''), timeout)
    jd_text = smartrecruiters_text(detail)
    if len(collapse_ws(jd_text)) < 200:
        return None
    url = (
        str(detail.get('applyUrl') or '').strip()
        or str(detail.get('postingUrl') or '').strip()
        or f'https://jobs.smartrecruiters.com/{company_slug}/{raw.get("id","")}'
    )
    return direct_resolution_result(
        job=job,
        url=url,
        source='direct_smartrecruiters_api',
        jd_text=jd_text,
        parser='smartrecruiters_api',
        title=str(detail.get('name') or raw.get('name') or ''),
        company=collapse_ws(str(nested_get(detail, 'company', 'name', default='') or job.get('company', ''))),
        location=str(nested_get(detail, 'location', 'fullLocation', default='') or nested_get(raw, 'location', 'fullLocation', default='') or job.get('location', '')),
        debug_note=f'company_slug={company_slug};match_score={score}',
    )


def resolve_via_ashby(job: dict[str, Any], spec: dict[str, Any], timeout: int) -> dict[str, Any] | None:
    board_slug = str(spec.get('board_slug') or '').strip()
    if not board_slug:
        return None
    openings = ashby_board_openings(board_slug, timeout)
    candidates = []
    for item in openings:
        candidates.append({
            'title': item.get('title', ''),
            'location': location_string_for_ashby(item),
            'raw': item,
        })
    best, score = select_best_match(job, candidates)
    if not best or score < 8:
        return None
    if not location_compatible(job.get('location', ''), best.get('location', '')):
        return None
    raw = best['raw']
    url = f'https://jobs.ashbyhq.com/{board_slug}/{raw["id"]}'
    html = cached_fetch_url(url, timeout=timeout)
    jd_text, meta = extract_jd_text(html)
    if len(collapse_ws(jd_text)) < 200:
        return None
    return direct_resolution_result(
        job=job,
        url=url,
        source='direct_ashby_board',
        jd_text=jd_text,
        parser=meta.get('parser', 'jsonld_jobposting'),
        title=meta.get('jobposting_title') or raw.get('title', ''),
        company=meta.get('jobposting_company') or job.get('company', ''),
        location=meta.get('jobposting_location') or location_string_for_ashby(raw) or job.get('location', ''),
        debug_note=f'board={board_slug};match_score={score}',
    )


def resolve_via_cursor_careers(job: dict[str, Any], spec: dict[str, Any], timeout: int) -> dict[str, Any] | None:
    openings = cursor_careers_openings(timeout)
    best, score = select_best_match(job, openings)
    if not best or score < 8:
        return None
    if not location_compatible(job.get('location', ''), best.get('location', '')):
        return None
    html = cached_fetch_url(best['url'], timeout=timeout)
    jd_text, meta = extract_jd_text(html)
    if len(collapse_ws(jd_text)) < 200:
        return None
    return direct_resolution_result(
        job=job,
        url=best['url'],
        source='direct_cursor_careers',
        jd_text=jd_text,
        parser=meta.get('parser', 'jsonld_jobposting'),
        title=meta.get('jobposting_title') or best.get('title', ''),
        company=meta.get('jobposting_company') or job.get('company', ''),
        location=meta.get('jobposting_location') or best.get('location', '') or job.get('location', ''),
        debug_note=f'match_score={score}',
    )


def resolve_via_direct_source(
    job: dict[str, Any],
    env: dict[str, str],
    cfg: dict[str, Any],
    *,
    timeout: int,
) -> dict[str, Any] | None:
    spec = resolver_spec_for_job(job, cfg)
    if not spec:
        return None
    resolver_type = str(spec.get('type') or '').strip().lower()
    try:
        if resolver_type == 'microsoft_pcsx':
            return resolve_via_microsoft_pcsx(job, spec, timeout)
        if resolver_type == 'greenhouse':
            return resolve_via_greenhouse(job, spec, timeout)
        if resolver_type == 'smartrecruiters':
            return resolve_via_smartrecruiters(job, spec, timeout)
        if resolver_type == 'ashby':
            return resolve_via_ashby(job, spec, timeout)
        if resolver_type == 'cursor_careers':
            return resolve_via_cursor_careers(job, spec, timeout)
    except Exception as exc:
        return {'resolver_error': str(exc), 'direct_resolver': resolver_type}
    return None


def resolve_jobright_jd(job: dict[str, Any], timeout: int = 15) -> dict[str, Any] | None:
    """Resolve a JD from a Jobright detail page using JSON-LD.

    Jobright pages embed a schema.org/JobPosting JSON-LD block with the full
    job description, salary, company info, etc.
    """
    url = job.get('jobright_url')
    if not url:
        return None
    try:
        html = fetch_url(url, timeout=timeout)
        m = re.search(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.DOTALL | re.I,
        )
        if not m:
            return None
        data = json.loads(m.group(1))
        if data.get('@type') != 'JobPosting':
            return None
        description_html = data.get('description', '')
        # Strip HTML tags for plain text
        jd_text = re.sub(r'<[^>]+>', ' ', description_html)
        jd_text = re.sub(r'\s+', ' ', jd_text).strip()
        if len(jd_text) < 100:
            return None

        # Extract salary from baseSalary if present
        jd_salary_min = None
        jd_salary_max = None
        base_salary = data.get('baseSalary')
        if isinstance(base_salary, dict):
            value = base_salary.get('value')
            if isinstance(value, dict):
                jd_salary_min = value.get('minValue')
                jd_salary_max = value.get('maxValue')

        # Extract location
        jd_location = ''
        job_location = data.get('jobLocation')
        if isinstance(job_location, dict):
            address = job_location.get('address')
            if isinstance(address, dict):
                locality = address.get('addressLocality', '')
                region = address.get('addressRegion', '')
                parts = [p for p in [locality, region] if p]
                jd_location = ', '.join(parts) if parts else ''

        result = {
            'jd_text': jd_text,
            'jd_text_preview': jd_text[:1200],
            'jd_title': data.get('title', ''),
            'jd_company': (data.get('hiringOrganization') or {}).get('name', ''),
            'jd_location': jd_location or job.get('location', ''),
            'jd_parser': 'jobright_jsonld',
            'external_jd_url': url,
            'external_jd_source': 'jobright_page',
            'external_jd_status': 'resolved',
        }
        if jd_salary_min is not None:
            result['jd_salary_min'] = jd_salary_min
        if jd_salary_max is not None:
            result['jd_salary_max'] = jd_salary_max
        return result
    except Exception:
        return None


def collect_external_candidates(job: dict[str, Any]) -> list[dict[str, str]]:
    urls = []
    seen = set()
    for field in ('external_candidates', 'links'):
        for url in job.get(field, []) or []:
            if not is_http_url(url) or is_linkedin_url(url):
                continue
            if url in seen:
                continue
            seen.add(url)
            urls.append({'url': url, 'source': field})
    return urls


def split_company_aliases(company: str) -> list[str]:
    raw = collapse_ws(company)
    if not raw:
        return []
    candidates = [raw, collapse_ws(CAMEL_CASE_RE.sub(' ', raw))]
    stripped = collapse_ws(COMPANY_SUFFIX_RE.sub(' ', raw))
    if stripped and stripped.lower() != raw.lower():
        candidates.append(stripped)
    pieces = re.split(r'[/|,()-]+', raw)
    candidates.extend(collapse_ws(piece) for piece in pieces if collapse_ws(piece))
    words = raw.split()
    if len(words) >= 2:
        candidates.append(collapse_ws(' '.join(words[:-1])))
        if words[0].lower() not in {'the', 'a', 'an'} and len(words[0]) >= 3:
            candidates.append(words[0])
    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.lower()
        if not candidate or key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out[:4]


def company_slug_terms(company: str) -> set[str]:
    terms: set[str] = set()
    for alias in split_company_aliases(company):
        slug = re.sub(r'[^a-z0-9]+', ' ', alias.lower())
        for token in slug.split():
            if len(token) >= 3:
                terms.add(token)
    return terms


def title_terms(title: str) -> list[str]:
    return [
        token for token in re.sub(r'[^a-z0-9]+', ' ', str(title).lower()).split()
        if len(token) >= 4
    ]


def allowed_domains(env: dict[str, str], cfg: dict[str, Any]) -> list[str]:
    raw = external_search_site_filters(env, cfg, DEFAULT_SEARCH_FILTERS)
    out: list[str] = []
    for part in raw.split():
        token = part.strip()
        if token.startswith('site:'):
            token = token[5:]
        token = token.strip().lower()
        if token:
            out.append(token)
    return out


def make_search_queries(job: dict[str, Any], env: dict[str, str], cfg: dict[str, Any]) -> list[str]:
    title = collapse_ws(job.get('title', ''))
    company = collapse_ws(job.get('company', ''))
    if not (title and company):
        return []
    aliases = split_company_aliases(company) or [company]
    domains = allowed_domains(env, cfg)
    queries: list[str] = []
    seen: set[str] = set()

    def add(query: str) -> None:
        normalized = collapse_ws(query)
        if normalized and normalized not in seen:
            seen.add(normalized)
            queries.append(normalized)

    primary_alias = aliases[0]
    add(f'"{title}" "{primary_alias}" careers')
    add(f'"{title}" "{primary_alias}" jobs')
    if len(aliases) > 1:
        add(f'"{title}" "{aliases[1]}" careers')
    for domain in domains:
        add(f'"{title}" "{primary_alias}" site:{domain}')
        if len(aliases) > 1:
            add(f'"{title}" "{aliases[1]}" site:{domain}')
    return queries[:10]


def fetch_bing_rss(url: str, timeout: int) -> list[dict[str, str]]:
    xml_text = fetch_url(url, timeout=timeout)
    root = ET.fromstring(xml_text)
    out: list[dict[str, str]] = []
    for item in root.findall('./channel/item'):
        title = collapse_ws(''.join(item.findtext('title', default='')))
        link = collapse_ws(''.join(item.findtext('link', default='')))
        description = collapse_ws(''.join(item.findtext('description', default='')))
        if link:
            out.append({'url': link, 'title': title, 'description': description})
    return out


def candidate_score(job: dict[str, Any], candidate: dict[str, str], allowed: list[str]) -> int:
    url = candidate.get('url', '')
    host = host_of(url)
    if not host or host in BAD_RESULT_HOSTS:
        return -100
    if host.endswith(BING_HOST_SUFFIXES) or is_linkedin_url(url):
        return -100
    score = 0
    lowered = ' '.join(
        collapse_ws(candidate.get(key, '')).lower()
        for key in ('title', 'description')
    )
    pathish = f'{host} {url.lower()}'
    company_terms = company_slug_terms(job.get('company', ''))
    role_terms = title_terms(job.get('title', ''))
    matched_role_terms = sum(1 for term in role_terms if term in lowered)
    matched_company_terms = sum(1 for term in company_terms if term in lowered or term in pathish)

    if any(host.endswith(domain) for domain in allowed):
        score += 6
    if any(hint in pathish for hint in JOB_HOST_HINTS):
        score += 4
    if matched_company_terms:
        score += 4
    if matched_role_terms >= 2:
        score += 4
    elif matched_role_terms == 1:
        score += 2
    full_title = collapse_ws(str(job.get('title', '')).lower())
    if full_title and full_title in lowered:
        score += 6
    if 'job' in lowered or 'career' in lowered or 'opening' in lowered:
        score += 1
    return score


def looks_like_job_page(
    job: dict[str, Any],
    url: str,
    html: str,
    jd_text: str,
    meta: dict[str, Any],
    allowed: list[str],
) -> bool:
    if meta.get('parser') == 'jsonld_jobposting':
        return True
    page_title = collapse_ws(meta.get('jobposting_title') or extract_page_title(html)).lower()
    url_lower = url.lower()
    if any(bad in url_lower for bad in BAD_PAGE_HINTS) or any(bad in page_title for bad in ('documentation', 'docs', 'news', 'blog', 'article')):
        return False
    text = collapse_ws(jd_text).lower()
    company_terms = company_slug_terms(job.get('company', ''))
    role_terms = title_terms(job.get('title', ''))
    signals = 0
    if any(host_of(url).endswith(domain) for domain in allowed) or any(hint in url_lower for hint in JOB_HOST_HINTS):
        signals += 1
    if sum(1 for term in company_terms if term in f'{page_title} {text[:3000]} {url_lower}') >= 1:
        signals += 1
    if sum(1 for term in role_terms if term in f'{page_title} {text[:3000]}') >= max(1, min(2, len(role_terms))):
        signals += 1
    if sum(1 for hint in JOB_TEXT_HINTS if hint in text[:6000]) >= 2:
        signals += 1
    return signals >= 3 and len(collapse_ws(jd_text)) >= 500


def search_external_candidates(job: dict[str, Any], env: dict[str, str], cfg: dict[str, Any], debug: bool = False) -> list[dict[str, str]]:
    if not external_search_enabled(env, cfg):
        return []
    timeout = int(env.get('JOB_HTTP_TIMEOUT_SECONDS', '20'))
    queries = make_search_queries(job, env, cfg)
    allowed = allowed_domains(env, cfg)
    candidates: list[dict[str, str]] = []
    by_url: dict[str, dict[str, str]] = {}
    for query in queries:
        url = 'https://www.bing.com/search?format=rss&q=' + quote_plus(query)
        try:
            rss_candidates = fetch_bing_rss(url, timeout=timeout)
        except Exception:
            continue
        for item in rss_candidates:
            href = unwrap_url(item.get('url', ''))
            if not is_http_url(href) or is_linkedin_url(href):
                continue
            candidate = {
                'url': href,
                'source': 'web_search',
                'query': query,
                'title': item.get('title', ''),
                'description': item.get('description', ''),
            }
            score = candidate_score(job, candidate, allowed)
            if score < MIN_SEARCH_SCORE:
                continue
            candidate['score'] = str(score)
            existing = by_url.get(href)
            if existing is None or int(candidate['score']) > int(existing.get('score', '0')):
                by_url[href] = candidate
    candidates = sorted(
        by_url.values(),
        key=lambda item: (-int(item.get('score', '0')), item.get('url', '')),
    )
    if debug and candidates:
        print(
            f"[debug] {job.get('company','?')} | {job.get('title','?')} -> "
            f"{len(candidates)} candidates",
        )
        for item in candidates[:8]:
            print(
                f"  score={item.get('score')} host={host_of(item.get('url',''))} "
                f"url={item.get('url','')} query={item.get('query','')}",
            )
    return candidates[:8]


LINKEDIN_JD_MARKUP_RE = re.compile(
    r'class="show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>',
    re.S | re.I,
)


def extract_linkedin_jd(html: str) -> str:
    match = LINKEDIN_JD_MARKUP_RE.search(html)
    if not match:
        return ''
    lines = [l for l in html_to_lines(match.group(1)) if collapse_ws(l)]
    return '\n'.join(lines)


# --- Seniority-band filtering ------------------------------------------------

def _calibration(cfg: dict[str, Any]) -> dict[str, Any]:
    return nested_get(cfg, 'level_calibration', default={}) or {}


def _apply_rules(title: str, rules: list[dict[str, Any]]) -> str | None:
    title_lc = (title or '').lower()
    for rule in rules or []:
        pat = (rule or {}).get('pattern', '')
        if pat and pat.lower() in title_lc:
            return (rule or {}).get('tier')
    return None


def determine_tier(job: dict[str, Any], cfg: dict[str, Any]) -> str | None:
    """Return the job's tier name per level_calibration config, or None if
    no rule matched and unknown_policy leaves it unclassified.
    """
    calib = _calibration(cfg)
    if not calib:
        return None
    title = job.get('title', '') or ''
    company = (job.get('company', '') or '').strip()
    companies = calib.get('companies', {}) or {}
    # Exact match first, then case-insensitive fallback.
    company_rules = companies.get(company)
    if company_rules is None:
        lowered = company.lower()
        for key, val in companies.items():
            if key.lower() == lowered:
                company_rules = val
                break
    if company_rules:
        tier = _apply_rules(title, (company_rules or {}).get('rules', []))
        if tier:
            return tier
    # Fall through to defaults.
    return _apply_rules(title, calib.get('default_rules', []))


def band_check(job: dict[str, Any], cfg: dict[str, Any]) -> tuple[str, str | None]:
    """Evaluate a job against the target seniority band.

    Returns (verdict, tier):
        verdict ∈ {'in_band', 'too_high', 'too_low', 'unknown'}
        tier    = the resolved tier name, or None if unclassified.
    """
    calib = _calibration(cfg)
    if not calib:
        return 'in_band', None
    tier = determine_tier(job, cfg)
    order = calib.get('tier_order', {}) or {}
    band = calib.get('target_band', {}) or {}
    min_tier = band.get('min_tier')
    max_tier = band.get('max_tier')
    if not tier:
        policy = calib.get('unknown_policy', 'in_band')
        return ('unknown' if policy != 'in_band' else 'in_band'), None
    t_rank = order.get(tier)
    mn = order.get(min_tier)
    mx = order.get(max_tier)
    if t_rank is None or mn is None or mx is None:
        return 'in_band', tier
    if t_rank < mn:
        return 'too_low', tier
    if t_rank > mx:
        return 'too_high', tier
    return 'in_band', tier


def enrich_job(job: dict[str, Any], env: dict[str, str], cfg: dict[str, Any], debug: bool = False) -> dict[str, Any]:
    timeout = int(env.get('JOB_HTTP_TIMEOUT_SECONDS', '20'))
    allowed = allowed_domains(env, cfg)
    enriched = dict(job)
    enriched['external_jd_status'] = 'unresolved'
    enriched['external_jd_url'] = ''
    enriched['external_jd_source'] = ''
    enriched['search_queries'] = []
    enriched['search_candidates'] = []
    enriched['jd_text'] = ''
    enriched['jd_text_preview'] = ''
    enriched['jd_parser'] = ''

    # Seniority-band hard filter: skip jobs that are outside the target band.
    # This runs BEFORE any network fetch or LLM call, so out-of-band jobs cost
    # effectively zero to reject.
    verdict, tier = band_check(job, cfg)
    if verdict in ('too_high', 'too_low'):
        enriched['external_jd_status'] = f'out_of_band_{verdict}'
        enriched['level_tier'] = tier
        enriched['level_band_verdict'] = verdict
        if debug:
            print(f"[band] skipped {job.get('company','?')} | {job.get('title','?')} → {verdict} (tier={tier})")
        return enriched
    enriched['level_tier'] = tier
    enriched['level_band_verdict'] = verdict

    direct = resolve_via_direct_source(job, env, cfg, timeout=timeout)
    if direct:
        if direct.get('external_jd_status') == 'resolved':
            enriched.update(direct)
            return enriched
        if direct.get('resolver_error'):
            enriched.setdefault('fetch_errors', []).append({
                'resolver': direct.get('direct_resolver', ''),
                'error': direct.get('resolver_error', ''),
            })

    # Jobright page resolver — if job has a jobright_url, try JSON-LD extraction
    jobright_result = resolve_jobright_jd(job, timeout=timeout)
    if jobright_result and jobright_result.get('external_jd_status') == 'resolved':
        enriched.update(jobright_result)
        return enriched

    candidates = collect_external_candidates(job)
    search_candidates = search_external_candidates(job, env, cfg, debug=debug)
    enriched['search_queries'] = [item.get('query', '') for item in search_candidates if item.get('query')]
    enriched['search_candidates'] = search_candidates
    for candidate in search_candidates:
        if candidate['url'] not in {item['url'] for item in candidates}:
            candidates.append(candidate)

    for candidate in candidates:
        url = candidate['url']
        try:
            html = fetch_url(url, timeout=timeout)
            jd_text, meta = extract_jd_text(html)
        except Exception as exc:
            enriched.setdefault('fetch_errors', []).append({'url': url, 'error': str(exc)})
            continue
        if len(collapse_ws(jd_text)) < 200:
            continue
        if not looks_like_job_page(job, url, html, jd_text, meta, allowed):
            enriched.setdefault('fetch_errors', []).append({'url': url, 'error': 'page_failed_job_validation'})
            continue
        enriched['external_jd_status'] = 'resolved'
        enriched['external_jd_url'] = url
        enriched['external_jd_source'] = candidate['source']
        enriched['jd_text'] = jd_text
        enriched['jd_text_preview'] = jd_text[:1200]
        enriched['jd_parser'] = meta.get('parser', '')
        enriched['jd_title'] = meta.get('jobposting_title') or job.get('title', '')
        enriched['jd_company'] = meta.get('jobposting_company') or job.get('company', '')
        enriched['jd_location'] = meta.get('jobposting_location') or job.get('location', '')
        break

    if enriched['external_jd_status'] == 'unresolved':
        disable_linkedin = os.environ.get('JOB_SCOUT_DISABLE_LINKEDIN_FETCH', '').strip().lower() in {'1', 'true', 'yes'}
        linkedin_url = job.get('linkedin_url', '')
        if not disable_linkedin and linkedin_url and looks_like_linkedin_job_url(linkedin_url):
            try:
                li_html = fetch_url(linkedin_url, timeout=timeout)
                jd_text = extract_linkedin_jd(li_html)
                if len(collapse_ws(jd_text)) >= 200:
                    enriched['external_jd_status'] = 'linkedin_public'
                    enriched['external_jd_url'] = linkedin_url
                    enriched['external_jd_source'] = 'linkedin_public'
                    enriched['jd_text'] = jd_text
                    enriched['jd_text_preview'] = jd_text[:1200]
                    enriched['jd_parser'] = 'linkedin_markup'
                    enriched['jd_title'] = job.get('title', '')
                    enriched['jd_company'] = job.get('company', '')
                    enriched['jd_location'] = job.get('location', '')
            except Exception as exc:
                enriched.setdefault('fetch_errors', []).append({'url': linkedin_url, 'error': str(exc)})

    return enriched


def main() -> None:
    args = parse_args()
    env = load_env()
    cfg = load_project_config(env)
    input_path = Path(args.input_path) if args.input_path else jobs_inbox_json_path(env, cfg)
    output_path = Path(args.out) if args.out else jobs_enriched_json_path(env, cfg)
    payload = read_json(input_path, {'jobs': []})
    jobs = payload.get('jobs', [])
    if args.offset:
        jobs = jobs[args.offset:]
    if args.limit and args.limit > 0:
        jobs = jobs[:args.limit]
    enriched_jobs = [enrich_job(job, env, cfg, debug=args.debug) for job in jobs]
    out = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source': payload.get('source', 'linkedin_alert_email'),
        'job_count': len(enriched_jobs),
        'resolved_count': sum(1 for job in enriched_jobs if job.get('external_jd_status') in ('resolved', 'linkedin_public')),
        'jobs': enriched_jobs,
    }
    history_path, history_log = write_json_snapshot_and_history(env, output_path, out, history_group='jobs_enriched')
    print(f"wrote {output_path} ({out['resolved_count']} resolved / {out['job_count']} total)")
    print(f"archived enriched snapshot: {history_path}")
    print(f"appended enriched history log: {history_log}")


if __name__ == '__main__':
    main()
