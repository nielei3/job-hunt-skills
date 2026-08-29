"""Adapter-level tests for scan_target_companies.

Mocks HTTP fetchers so tests don't hit the network. Focus is parsing/normalization
correctness for each adapter's response shape.
"""
from __future__ import annotations

import json

import scan_target_companies as st


# ---------------------------------------------------------------------------
# Greenhouse / Lever (existing — sanity that normalizers haven't regressed)
# ---------------------------------------------------------------------------

def test_normalize_greenhouse_extracts_jd_inline():
    raw = {
        'id': 12345,
        'title': '  Staff Software Engineer  ',
        'absolute_url': 'https://boards.greenhouse.io/anthropic/jobs/12345',
        'location': {'name': 'San Francisco, CA'},
        'content': '<p>About the role</p><ul><li>Build stuff</li></ul>',
    }
    job = st.normalize_greenhouse_job(raw, 'Anthropic')
    assert job['title'] == 'Staff Software Engineer'
    assert job['company'] == 'Anthropic'
    assert job['location'] == 'San Francisco, CA'
    assert job['external_jd_url'].endswith('/12345')
    assert 'About the role' in job['jd_text']
    assert 'Build stuff' in job['jd_text']
    assert job['external_jd_status'] == 'ats_api'
    assert job['source'] == 'target_scan'


def test_normalize_lever_extracts_jd_inline():
    raw = {
        'id': 'abc-123',
        'text': 'Senior Backend Engineer',
        'hostedUrl': 'https://jobs.lever.co/example/abc-123',
        'categories': {'location': 'New York, NY'},
        'description': '<p>Hello world</p>',
    }
    job = st.normalize_lever_job(raw, 'ExampleCo')
    assert job['title'] == 'Senior Backend Engineer'
    assert 'Hello world' in job['jd_text']
    assert job['ats'] == 'lever'


# ---------------------------------------------------------------------------
# Microsoft PCSX
# ---------------------------------------------------------------------------

def _pcsx_payload(positions: list[dict]) -> dict:
    return {'data': {'positions': positions}}


def test_pcsx_listing_paginates_and_normalizes(monkeypatch):
    page1 = [
        {'jobId': str(1000 + i), 'title': f'Software Engineer {i}',
         'positionUrl': f'/job/{1000 + i}',
         'standardizedLocations': ['Redmond, WA, USA']}
        for i in range(st.PCSX_PAGE_SIZE)
    ]
    page2 = [
        {'jobId': '2000', 'title': 'Principal Engineer, AI Platform',
         'positionUrl': '/job/2000', 'standardizedLocations': ['Mountain View, CA, USA'],
         'businessGroup': 'Microsoft AI'},
    ]
    pages = [_pcsx_payload(page1), _pcsx_payload(page2), _pcsx_payload([])]
    calls: list[str] = []

    def fake_fetch_json(url, timeout, **_):
        calls.append(url)
        return pages.pop(0) if pages else _pcsx_payload([])

    monkeypatch.setattr(st, 'fetch_json', fake_fetch_json)
    out = st.list_pcsx({'name': 'Microsoft', 'domain': 'microsoft.com'}, timeout=5)
    # 20 from page1 + 1 from page2 (page2 < PCSX_PAGE_SIZE → loop exits)
    assert len(out) == st.PCSX_PAGE_SIZE + 1
    sample = out[0]
    assert sample['company'] == 'Microsoft'
    assert sample['ats'] == 'pcsx'
    assert sample['external_jd_url'].startswith('https://apply.careers.microsoft.com/job/')
    assert sample['jd_text'] == ''  # not fetched yet
    # Confirm we asked for at least 2 pages.
    assert any('start=0' in u for u in calls)
    assert any(f'start={st.PCSX_PAGE_SIZE}' in u for u in calls)


def test_pcsx_team_filter_keeps_only_matching(monkeypatch):
    positions = [
        {'jobId': '1', 'title': 'Software Engineer, Office',
         'positionUrl': '/job/1', 'businessGroup': 'M365'},
        {'jobId': '2', 'title': 'Principal Engineer, AI Platform',
         'positionUrl': '/job/2', 'businessGroup': 'Microsoft AI'},
        {'jobId': '3', 'title': 'Principal Engineer, Azure Storage',
         'positionUrl': '/job/3', 'businessGroup': 'Cloud + AI'},
    ]
    monkeypatch.setattr(st, 'fetch_json', lambda url, timeout, **_: _pcsx_payload(positions if 'start=0' in url else []))
    out = st.list_pcsx({'name': 'Microsoft AI', 'domain': 'microsoft.com', 'team_filter': 'ai'}, timeout=5)
    titles = [j['title'] for j in out]
    # Job 1 (no AI marker) excluded; Jobs 2 (title) and 3 (businessGroup contains "ai") kept.
    assert 'Software Engineer, Office' not in titles
    assert 'Principal Engineer, AI Platform' in titles
    assert 'Principal Engineer, Azure Storage' in titles
    for j in out:
        assert j['company'] == 'Microsoft AI'


def test_pcsx_listing_handles_empty_response(monkeypatch):
    monkeypatch.setattr(st, 'fetch_json', lambda url, timeout, **_: _pcsx_payload([]))
    out = st.list_pcsx({'name': 'Microsoft'}, timeout=5)
    assert out == []


def test_pcsx_listing_handles_http_error(monkeypatch):
    def boom(url, timeout, **_):
        raise RuntimeError('connection refused')
    monkeypatch.setattr(st, 'fetch_json', boom)
    # Must NOT raise — error is logged and listing returns empty.
    out = st.list_pcsx({'name': 'Microsoft'}, timeout=5)
    assert out == []


# ---------------------------------------------------------------------------
# Apple jobs
# ---------------------------------------------------------------------------

def test_apple_listing_normalizes(monkeypatch):
    pages = [
        {'searchResults': [
            {'id': '200000', 'positionId': '200000',
             'postingTitle': 'Software Engineer - Apple AI',
             'transformedPostingTitle': 'software-engineer-apple-ai',
             'locations': [{'name': 'Cupertino, California, United States'}]},
            {'id': '200001', 'postingTitle': 'Staff Engineer, Cloud',
             'transformedPostingTitle': 'staff-engineer-cloud',
             'locations': [{'name': 'Austin, TX, USA'}]},
        ]},
        {'searchResults': []},
    ]

    def fake_post(url, body, timeout, **_):
        assert 'jobs.apple.com' in url
        return pages.pop(0) if pages else {'searchResults': []}

    monkeypatch.setattr(st, 'post_json', fake_post)
    out = st.list_apple({'name': 'Apple'}, timeout=5)
    assert len(out) == 2
    j0 = out[0]
    assert j0['title'] == 'Software Engineer - Apple AI'
    assert j0['external_jd_url'] == 'https://jobs.apple.com/details/200000/software-engineer-apple-ai'
    assert j0['ats'] == 'apple_jobs'
    assert j0['location'] == 'Cupertino, California, United States'
    assert j0['jd_text'] == ''  # not fetched yet


def test_apple_listing_handles_missing_locations(monkeypatch):
    monkeypatch.setattr(st, 'post_json', lambda url, body, timeout, **_: {'searchResults': [
        {'id': '1', 'postingTitle': 'Engineer', 'transformedPostingTitle': 'eng',
         'locations': None},
    ]} if body['page'] == 1 else {'searchResults': []})
    out = st.list_apple({'name': 'Apple'}, timeout=5)
    assert out[0]['location'] == ''


def test_apple_listing_swallows_http_error(monkeypatch):
    def boom(url, body, timeout, **_):
        raise RuntimeError('boom')
    monkeypatch.setattr(st, 'post_json', boom)
    out = st.list_apple({'name': 'Apple'}, timeout=5)
    assert out == []


# ---------------------------------------------------------------------------
# Amazon jobs
# ---------------------------------------------------------------------------

def test_amazon_listing_extracts_inline_jd(monkeypatch):
    pages = [
        {'jobs': [
            {'id': 'A100', 'title': 'Principal Engineer, AWS',
             'normalized_location': 'Seattle, WA',
             'job_path': '/en/jobs/A100/principal-engineer-aws',
             'description': '<p>Lead a team building distributed systems.</p>',
             'description_short': 'Lead a team.'},
        ]},
        {'jobs': []},
    ]
    monkeypatch.setattr(st, 'fetch_json', lambda url, timeout, **_: pages.pop(0) if pages else {'jobs': []})
    out = st.list_amazon({'name': 'Amazon'}, timeout=5)
    assert len(out) == 1
    j = out[0]
    assert j['title'] == 'Principal Engineer, AWS'
    assert j['external_jd_url'] == 'https://www.amazon.jobs/en/jobs/A100/principal-engineer-aws'
    assert 'Lead a team' in j['jd_text']
    assert j['location'] == 'Seattle, WA'


def test_amazon_listing_pagination_stops_when_short_page(monkeypatch):
    # Single page with fewer than AMAZON_PAGE_SIZE entries → loop exits, no offset bump.
    pages_seen: list[int] = []

    def fake(url, timeout, **_):
        # Extract offset
        m = url.split('offset=')[1].split('&')[0]
        pages_seen.append(int(m))
        return {'jobs': [{'id': '1', 'title': 'X', 'job_path': '/x', 'description': 'd' * 300}]}

    monkeypatch.setattr(st, 'fetch_json', fake)
    out = st.list_amazon({'name': 'Amazon'}, timeout=5)
    assert len(out) == 1
    assert pages_seen == [0]  # didn't fetch page 2


# ---------------------------------------------------------------------------
# Google careers (UNTESTED in production — verify HTML parser logic)
# ---------------------------------------------------------------------------

def test_google_listing_parses_anchor_html(monkeypatch):
    page1 = '''
    <html><body>
    <a href="/about/careers/applications/jobs/results/123456789-staff-software-engineer">
        Staff Software Engineer
    </a>
    <a href="/about/careers/applications/jobs/results/987654321-principal-engineer-cloud?someparam=1">
        Principal Engineer, Cloud
    </a>
    </body></html>
    '''
    page2 = '<html><body>no jobs</body></html>'
    pages = [page1, page2]
    monkeypatch.setattr(st, 'fetch_url', lambda url, timeout, **_: pages.pop(0) if pages else '')
    out = st.list_google({'name': 'Google'}, timeout=5)
    assert len(out) == 2
    titles = sorted(j['title'] for j in out)
    assert titles == ['Principal Engineer, Cloud', 'Staff Software Engineer']
    assert out[0]['external_jd_url'].startswith('https://www.google.com/about/careers/')


def test_google_listing_dedupes_repeat_ids(monkeypatch):
    # Same job_id on two pages — listing should keep only one and stop.
    page = ('<a href="/about/careers/applications/jobs/results/111-eng">Engineer</a>'
            '<a href="/about/careers/applications/jobs/results/111-eng-dup">Engineer Dup</a>')
    monkeypatch.setattr(st, 'fetch_url', lambda url, timeout, **_: page)
    out = st.list_google({'name': 'Google'}, timeout=5)
    assert len(out) == 1


def test_google_swallows_http_error(monkeypatch):
    def boom(url, timeout, **_):
        raise RuntimeError('blocked')
    monkeypatch.setattr(st, 'fetch_url', boom)
    out = st.list_google({'name': 'Google'}, timeout=5)
    assert out == []


# ---------------------------------------------------------------------------
# Meta careers (UNTESTED in production)
# ---------------------------------------------------------------------------

def test_meta_listing_parses_anchor_html(monkeypatch):
    page1 = '''
    <html><body>
    <a href="/jobs/12345/staff-software-engineer">Staff Software Engineer</a>
    <a href="/jobs/67890/?location=us">Principal Engineer, AI Platform</a>
    </body></html>
    '''
    page2 = '<html><body>nope</body></html>'
    pages = [page1, page2]
    monkeypatch.setattr(st, 'fetch_url', lambda url, timeout, **_: pages.pop(0) if pages else '')
    out = st.list_meta({'name': 'Meta'}, timeout=5)
    assert len(out) == 2
    assert any('Staff Software Engineer' in j['title'] for j in out)
    assert all(j['external_jd_url'].startswith('https://www.metacareers.com') for j in out)


def test_meta_swallows_http_error(monkeypatch):
    monkeypatch.setattr(st, 'fetch_url', lambda url, timeout, **_: (_ for _ in ()).throw(RuntimeError('blocked')))
    out = st.list_meta({'name': 'Meta'}, timeout=5)
    assert out == []


# ---------------------------------------------------------------------------
# JD fetch helpers (per-adapter)
# ---------------------------------------------------------------------------

def test_jd_fetchers_extract_text_from_html(monkeypatch):
    monkeypatch.setattr(st, 'fetch_url',
                        lambda url, timeout, **_: '<html><body><p>Build cool stuff</p><ul><li>Design APIs</li></ul></body></html>')
    for fn in (st.jd_pcsx, st.jd_apple, st.jd_amazon, st.jd_google, st.jd_meta):
        text = fn('https://example.com/job', 5)
        assert 'Build cool stuff' in text
        assert 'Design APIs' in text


def test_jd_fetchers_return_empty_for_no_url():
    for fn in (st.jd_pcsx, st.jd_apple, st.jd_amazon, st.jd_google, st.jd_meta):
        assert fn('', 5) == ''


# ---------------------------------------------------------------------------
# Adapter registry sanity
# ---------------------------------------------------------------------------

def test_adapter_registry_covers_expected_types():
    for ats in ('greenhouse', 'lever', 'pcsx', 'apple_jobs', 'amazon_jobs',
                'google_careers', 'meta_careers'):
        assert ats in st.ATS_ADAPTERS, f'{ats} not registered'
    # Inline-JD adapters: jd_fn is None
    assert st.ATS_ADAPTERS['greenhouse'][1] is None
    assert st.ATS_ADAPTERS['lever'][1] is None
    # Per-page JD fetch adapters
    for ats in ('pcsx', 'apple_jobs', 'amazon_jobs', 'google_careers', 'meta_careers'):
        assert st.ATS_ADAPTERS[ats][1] is not None


def test_build_job_uses_url_for_job_key():
    j1 = st._build_job('Eng', 'X', 'NY', 'https://x.com/job/1', '', 'pcsx', '1')
    j2 = st._build_job('Eng', 'X', 'NY', 'https://x.com/job/1', '', 'pcsx', '1')
    assert j1['job_key'] == j2['job_key']
    j3 = st._build_job('Eng', 'X', 'NY', 'https://x.com/job/2', '', 'pcsx', '2')
    assert j1['job_key'] != j3['job_key']


def test_build_job_falls_back_when_url_missing():
    # No URL available — use company|title|ats_id as the key seed
    j = st._build_job('Eng', 'X', 'NY', '', '', 'pcsx', 'id-1')
    assert j['job_key']
    assert j['external_jd_url'] == ''
    assert j['links'] == []


# ---------------------------------------------------------------------------
# RemoteOK aggregator
# ---------------------------------------------------------------------------

REMOTEOK_LEGAL_NOTICE = {
    '0': '...',
    'legal': 'By using this API you agree...',
}


def test_remoteok_skips_legal_notice_and_normalizes(monkeypatch):
    payload = [
        REMOTEOK_LEGAL_NOTICE,
        {
            'id': '1234567',
            'slug': '1234567-senior-backend-engineer-acme-corp',
            'company': 'Acme Corp',
            'position': '  Senior Backend Engineer  ',
            'location': 'Worldwide',
            'description': '<p>Build cool stuff.</p><ul><li>Python</li></ul>',
            'apply_url': 'https://acmecorp.com/jobs/1234567',
            'url': 'https://remoteok.com/remote-jobs/1234567-senior-backend-engineer-acme-corp',
        },
        {
            'id': '7654321',
            'company': 'Beta LLC',
            'position': 'Staff Engineer',
            'location': 'USA Only',
            'description': '<p>JD body</p>',
            'url': 'https://remoteok.com/remote-jobs/7654321',
            # no apply_url — should fall back to url
        },
    ]
    monkeypatch.setattr(st, 'fetch_json', lambda url, timeout, **_: payload)
    out = st.list_remoteok({'name': 'RemoteOK'}, timeout=5)
    assert len(out) == 2  # legal notice skipped
    j0 = out[0]
    assert j0['company'] == 'Acme Corp'
    assert j0['title'] == 'Senior Backend Engineer'
    assert j0['location'] == 'Worldwide'
    assert j0['external_jd_url'] == 'https://acmecorp.com/jobs/1234567'  # apply_url preferred
    assert 'Build cool stuff' in j0['jd_text']
    assert 'Python' in j0['jd_text']
    assert j0['ats'] == 'remoteok'
    j1 = out[1]
    assert j1['company'] == 'Beta LLC'
    assert j1['external_jd_url'].startswith('https://remoteok.com/')  # fell back to url


def test_remoteok_swallows_http_error(monkeypatch):
    def boom(url, timeout, **_):
        raise RuntimeError('blocked')
    monkeypatch.setattr(st, 'fetch_json', boom)
    assert st.list_remoteok({'name': 'RemoteOK'}, timeout=5) == []


def test_remoteok_handles_non_list_response(monkeypatch):
    monkeypatch.setattr(st, 'fetch_json', lambda url, timeout, **_: {'error': 'rate-limited'})
    assert st.list_remoteok({'name': 'RemoteOK'}, timeout=5) == []


def test_remoteok_skips_entries_missing_id_or_company(monkeypatch):
    payload = [
        {'id': '1', 'company': '', 'position': 'Eng', 'description': 'x' * 50},
        {'company': 'Acme', 'position': 'Eng', 'description': 'y' * 50},
        {'id': '3', 'company': 'Acme', 'position': 'Eng', 'description': 'z' * 50,
         'url': 'https://remoteok.com/3'},
    ]
    monkeypatch.setattr(st, 'fetch_json', lambda url, timeout, **_: payload)
    out = st.list_remoteok({'name': 'RemoteOK'}, timeout=5)
    assert len(out) == 1
    assert out[0]['ats_job_id'] == '3'


# ---------------------------------------------------------------------------
# We Work Remotely aggregator (RSS)
# ---------------------------------------------------------------------------

def _wwr_rss(items: list[dict]) -> str:
    """Build a minimal WWR-shaped RSS feed."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<rss version="2.0"><channel><title>WWR Programming</title>']
    for it in items:
        desc = it.get('description', '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        lines.append(
            f'<item><title>{it["title"]}</title>'
            f'<link>{it["link"]}</link>'
            f'<description>{desc}</description>'
            f'<pubDate>Wed, 24 Apr 2024 10:00:00 +0000</pubDate>'
            f'<guid>{it["link"]}</guid></item>'
        )
    lines.append('</channel></rss>')
    return '\n'.join(lines)


def test_wwr_parses_title_at_company_format(monkeypatch):
    feed = _wwr_rss([
        {
            'title': 'Senior Backend Engineer at Acme Corp',
            'link': 'https://weworkremotely.com/listings/abc123',
            'description': '<p>Build distributed systems</p>',
        },
    ])
    monkeypatch.setattr(st, 'fetch_url',
                        lambda url, timeout, **_: feed if 'remote-programming-jobs' in url else _wwr_rss([]))
    out = st.list_weworkremotely({'name': 'WWR'}, timeout=5)
    assert len(out) >= 1
    j = next(x for x in out if x['ats_job_id'] == 'abc123')
    assert j['title'] == 'Senior Backend Engineer'
    assert j['company'] == 'Acme Corp'
    assert j['location'] == 'Remote'
    assert 'distributed systems' in j['jd_text']


def test_wwr_parses_company_colon_title_format(monkeypatch):
    feed = _wwr_rss([
        {
            'title': 'Acme Corp: Staff Software Engineer',
            'link': 'https://weworkremotely.com/listings/def456',
            'description': '<p>Description</p>',
        },
    ])
    monkeypatch.setattr(st, 'fetch_url', lambda url, timeout, **_: feed)
    out = st.list_weworkremotely({'name': 'WWR', 'wwr_feeds': ['https://x']}, timeout=5)
    j = out[0]
    assert j['company'] == 'Acme Corp'
    assert j['title'] == 'Staff Software Engineer'


def test_wwr_falls_back_for_unparseable_title(monkeypatch):
    feed = _wwr_rss([
        {
            'title': 'Some Random Headline',
            'link': 'https://weworkremotely.com/listings/xyz',
            'description': '<p>desc</p>',
        },
    ])
    monkeypatch.setattr(st, 'fetch_url', lambda url, timeout, **_: feed)
    out = st.list_weworkremotely({'name': 'WWR', 'wwr_feeds': ['https://x']}, timeout=5)
    j = out[0]
    assert j['company'] == ''
    assert j['title'] == 'Some Random Headline'


def test_wwr_dedupes_across_feeds(monkeypatch):
    same_link = 'https://weworkremotely.com/listings/dup'
    feed = _wwr_rss([
        {'title': 'Engineer at Acme', 'link': same_link, 'description': 'd'},
    ])
    monkeypatch.setattr(st, 'fetch_url', lambda url, timeout, **_: feed)
    out = st.list_weworkremotely(
        {'name': 'WWR', 'wwr_feeds': ['https://x', 'https://y', 'https://z']},
        timeout=5,
    )
    # All 3 feeds return the same item, but seen_links dedup keeps just one.
    assert len(out) == 1


def test_wwr_swallows_per_feed_errors(monkeypatch):
    feed_ok = _wwr_rss([{'title': 'Eng at Acme', 'link': 'https://wwr/ok', 'description': 'd'}])

    def fake_fetch(url, timeout, **_):
        if 'fail' in url:
            raise RuntimeError('connect timeout')
        return feed_ok

    monkeypatch.setattr(st, 'fetch_url', fake_fetch)
    out = st.list_weworkremotely(
        {'name': 'WWR', 'wwr_feeds': ['https://fail/feed1', 'https://ok/feed2']},
        timeout=5,
    )
    # First feed errored, second succeeded — overall return still has the OK job.
    assert len(out) == 1
    assert out[0]['company'] == 'Acme'


def test_wwr_swallows_xml_parse_error(monkeypatch):
    monkeypatch.setattr(st, 'fetch_url', lambda url, timeout, **_: '<not valid xml')
    out = st.list_weworkremotely({'name': 'WWR', 'wwr_feeds': ['https://x']}, timeout=5)
    assert out == []


def test_wwr_title_parser_unit():
    assert st._parse_wwr_title('Engineer at Acme Corp') == ('Acme Corp', 'Engineer')
    assert st._parse_wwr_title('Acme: Engineer') == ('Acme', 'Engineer')
    assert st._parse_wwr_title('Just a Headline') == ('', 'Just a Headline')
    # "at" inside title should still match the LAST " at " — current regex is non-greedy so first match wins
    assert st._parse_wwr_title('Engineering Lead at TechCo') == ('TechCo', 'Engineering Lead')


# ---------------------------------------------------------------------------
# Ashby
# ---------------------------------------------------------------------------

def test_ashby_normalizes_with_inline_jd(monkeypatch):
    payload = {'jobs': [
        {
            'id': 'ashby-1',
            'title': 'Member of Technical Staff',
            'location': {'city': 'San Francisco', 'country': 'United States'},
            'departmentName': 'Research',
            'descriptionHtml': '<p>Build frontier AI</p>',
            'jobUrl': 'https://jobs.ashbyhq.com/openai/ashby-1',
            'applyUrl': 'https://jobs.ashbyhq.com/openai/ashby-1/apply',
        },
        {
            'id': 'ashby-2',
            'title': 'Staff Engineer',
            'locationName': 'Remote — US',
            'descriptionHtml': '<p>Remote role</p>',
            'jobUrl': 'https://jobs.ashbyhq.com/openai/ashby-2',
            'secondaryLocations': [{'locationName': 'New York'}, {'locationName': 'Seattle'}],
        },
    ]}
    monkeypatch.setattr(st, 'fetch_json', lambda url, timeout, **_: payload)
    out = st.list_ashby({'name': 'OpenAI', 'board_slug': 'openai'}, timeout=5)
    assert len(out) == 2
    j0 = out[0]
    assert j0['title'] == 'Member of Technical Staff'
    assert j0['company'] == 'OpenAI'
    assert 'San Francisco' in j0['location']
    assert 'Build frontier AI' in j0['jd_text']
    assert j0['ats'] == 'ashby'
    j1 = out[1]
    assert 'New York' in j1['location'] and 'Seattle' in j1['location']


def test_ashby_no_slug_returns_empty(monkeypatch):
    monkeypatch.setattr(st, 'fetch_json', lambda url, timeout, **_: {'jobs': []})
    assert st.list_ashby({'name': 'X', 'board_slug': ''}, timeout=5) == []


def test_ashby_swallows_http_error(monkeypatch):
    def boom(url, timeout, **_):
        raise RuntimeError('blocked')
    monkeypatch.setattr(st, 'fetch_json', boom)
    assert st.list_ashby({'name': 'X', 'board_slug': 'x'}, timeout=5) == []


# ---------------------------------------------------------------------------
# Cursor careers
# ---------------------------------------------------------------------------

def test_cursor_careers_parses_anchor_html(monkeypatch):
    html = '''
    <a href="/careers/staff-engineer">
        <p>Staff Software Engineer</p>
        <span>San Francisco</span>
        <span>·</span>
        <span>Apply →</span>
    </a>
    <a href="/careers/principal-eng">
        <p>Principal Engineer, Inference</p>
        <span>Remote — USA</span>
    </a>
    <a href="/careers">Just the index link, skip</a>
    '''
    monkeypatch.setattr(st, 'fetch_url', lambda url, timeout, **_: html)
    out = st.list_cursor_careers({'name': 'Cursor'}, timeout=5)
    titles = sorted(j['title'] for j in out)
    assert titles == ['Principal Engineer, Inference', 'Staff Software Engineer']
    j_sf = next(j for j in out if j['title'] == 'Staff Software Engineer')
    assert j_sf['location'] == 'San Francisco'
    assert j_sf['external_jd_url'].startswith('https://cursor.com/careers/')


def test_cursor_careers_swallows_http_error(monkeypatch):
    monkeypatch.setattr(st, 'fetch_url', lambda url, timeout, **_: (_ for _ in ()).throw(RuntimeError('x')))
    assert st.list_cursor_careers({'name': 'Cursor'}, timeout=5) == []


# ---------------------------------------------------------------------------
# Workday
# ---------------------------------------------------------------------------

def test_workday_listing_paginates_and_normalizes(monkeypatch):
    page1 = {'jobPostings': [
        {'title': 'Software Engineer, AI Platform',
         'externalPath': '/job/Cupertino-CA/Eng/12345_R-100',
         'locationsText': 'Cupertino, California',
         'bulletFields': ['R-100']},
        {'title': 'Staff Software Engineer',
         'externalPath': '/job/SF/Staff/67890_R-200',
         'locationsText': 'San Francisco, California',
         'bulletFields': ['R-200']},
    ], 'total': 2}
    pages = [page1, {'jobPostings': []}]

    def fake_post(url, body, timeout, **_):
        assert 'wday/cxs' in url
        return pages.pop(0) if pages else {'jobPostings': []}

    monkeypatch.setattr(st, 'post_json', fake_post)
    spec = {
        'name': 'Salesforce',
        'workday_tenant': 'salesforce',
        'workday_pod': 'wd12',
        'workday_site': 'External_Career_Site',
    }
    out = st.list_workday(spec, timeout=5)
    assert len(out) == 2
    j = out[0]
    assert j['company'] == 'Salesforce'
    assert j['external_jd_url'].startswith(
        'https://salesforce.wd12.myworkdayjobs.com/External_Career_Site/job/')
    assert j['jd_text'] == ''  # not fetched yet


def test_workday_missing_config_returns_empty(monkeypatch):
    out = st.list_workday({'name': 'X', 'workday_tenant': 'x'}, timeout=5)  # missing pod, site
    assert out == []


def test_workday_jd_translates_url_to_cxs(monkeypatch):
    captured: dict = {}

    def fake_fetch_json(url, timeout, **_):
        captured['url'] = url
        return {'jobPostingInfo': {'jobDescription': '<p>Build platform stuff</p>'}}

    monkeypatch.setattr(st, 'fetch_json', fake_fetch_json)
    text = st.jd_workday(
        'https://salesforce.wd12.myworkdayjobs.com/External_Career_Site/job/SF/Eng/123_R-1',
        timeout=5,
    )
    assert 'Build platform stuff' in text
    assert '/wday/cxs/salesforce/External_Career_Site/job/' in captured['url']


def test_workday_jd_falls_back_to_html_on_error(monkeypatch):
    def boom_json(url, timeout, **_):
        raise RuntimeError('cxs blocked')
    monkeypatch.setattr(st, 'fetch_json', boom_json)
    monkeypatch.setattr(st, 'fetch_url', lambda url, timeout, **_: '<p>Fallback HTML JD</p>')
    text = st.jd_workday(
        'https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/SF/Eng/9_R-9',
        timeout=5,
    )
    assert 'Fallback HTML JD' in text


# ---------------------------------------------------------------------------
# TikTok
# ---------------------------------------------------------------------------

def test_tiktok_listing_normalizes(monkeypatch):
    page1 = {'data': {'job_post_list': [
        {'id': '7000000', 'title': 'Software Engineer, Recommendation',
         'city_list': [{'name': 'Mountain View'}],
         'description': '<p>Build recommender systems</p>'},
        {'id': '7000001', 'title': 'Staff Engineer, Search',
         'location_list': ['San Jose', 'Seattle'],
         'description': '<p>Search infra</p>'},
    ]}}
    pages = [page1, {'data': {'job_post_list': []}}]
    monkeypatch.setattr(st, 'fetch_json',
                        lambda url, timeout, **_: pages.pop(0) if pages else {'data': {'job_post_list': []}})
    out = st.list_tiktok({'name': 'TikTok'}, timeout=5)
    assert len(out) == 2
    j0 = out[0]
    assert j0['title'] == 'Software Engineer, Recommendation'
    assert j0['company'] == 'TikTok'
    assert j0['location'] == 'Mountain View'
    assert 'Build recommender' in j0['jd_text']
    assert j0['external_jd_url'] == 'https://careers.tiktok.com/position/7000000/detail'
    j1 = out[1]
    assert 'San Jose' in j1['location'] and 'Seattle' in j1['location']


def test_tiktok_swallows_http_error(monkeypatch):
    def boom(url, timeout, **_):
        raise RuntimeError('blocked')
    monkeypatch.setattr(st, 'fetch_json', boom)
    assert st.list_tiktok({'name': 'TikTok'}, timeout=5) == []


# ---------------------------------------------------------------------------
# Phenom (Netflix)
# ---------------------------------------------------------------------------

def test_phenom_listing_paginates_and_normalizes(monkeypatch):
    page1 = {'positions': [
        {'id': '111', 'name': 'Software Engineer 5 - Ads',
         'locations': ['New York,New York,United States of America'],
         'canonicalPositionUrl': 'https://explore.jobs.netflix.net/careers/job/111?microsite=netflix.com',
         'ats_job_id': 'JR1'},
        {'id': '222', 'name': 'Staff Engineer, Studio',
         'location': 'Los Angeles,California,United States of America',
         'locations': [],
         'canonicalPositionUrl': 'https://explore.jobs.netflix.net/careers/job/222',
         'ats_job_id': 'JR2'},
    ]}
    pages = [page1, {'positions': []}]
    monkeypatch.setattr(st, 'fetch_json',
                        lambda url, timeout, **_: pages.pop(0) if pages else {'positions': []})
    out = st.list_phenom(
        {'name': 'Netflix', 'phenom_host': 'explore.jobs.netflix.net',
         'phenom_domain': 'netflix.com'},
        timeout=5,
    )
    assert len(out) == 2
    j0 = out[0]
    assert j0['title'] == 'Software Engineer 5 - Ads'
    assert j0['company'] == 'Netflix'
    assert 'New York' in j0['location']
    # microsite query stripped from canonical URL
    assert j0['external_jd_url'] == 'https://explore.jobs.netflix.net/careers/job/111'
    assert j0['ats'] == 'phenom'
    assert j0['jd_text'] == ''  # JD not in listing


def test_phenom_missing_config_returns_empty():
    assert st.list_phenom({'name': 'X'}, timeout=5) == []
    assert st.list_phenom({'name': 'X', 'phenom_host': 'h'}, timeout=5) == []


def test_phenom_jd_fetches_per_job_endpoint(monkeypatch):
    captured: dict = {}

    def fake_fetch_json(url, timeout, **_):
        captured['url'] = url
        return {'job_description': '<p>At Netflix, our mission is to entertain.</p>'}

    monkeypatch.setattr(st, 'fetch_json', fake_fetch_json)
    text = st.jd_phenom('https://explore.jobs.netflix.net/careers/job/790314243123', timeout=5)
    assert 'entertain' in text
    assert '/api/apply/v2/jobs/790314243123' in captured['url']


def test_phenom_jd_falls_back_to_html_on_json_error(monkeypatch):
    monkeypatch.setattr(st, 'fetch_json', lambda url, timeout, **_: (_ for _ in ()).throw(RuntimeError('blocked')))
    monkeypatch.setattr(st, 'fetch_url', lambda url, timeout, **_: '<p>Fallback HTML</p>')
    text = st.jd_phenom('https://explore.jobs.netflix.net/careers/job/123', timeout=5)
    assert 'Fallback HTML' in text


def test_phenom_jd_handles_unknown_url_shape(monkeypatch):
    # Some other URL shape — should fall back to plain HTML fetch
    monkeypatch.setattr(st, 'fetch_url', lambda url, timeout, **_: '<p>JD body</p>')
    text = st.jd_phenom('https://other.example/jobs/abc', timeout=5)
    assert 'JD body' in text


def test_config_target_companies_use_supported_ats():
    """Every enabled target company in the shipped config must reference a registered adapter."""
    from pathlib import Path
    import yaml as _yaml
    import job_scout_lib as _lib
    cfg = _yaml.safe_load((Path(_lib.ROOT) / 'config' / 'job-scout.yaml').read_text())
    for entry in cfg.get('target_companies') or []:
        if not isinstance(entry, dict) or not entry.get('enabled', True):
            continue
        ats = (entry.get('ats') or '').lower()
        if ats == 'custom':
            continue  # explicit "not yet wired" marker
        assert ats in st.ATS_ADAPTERS, (
            f'target_companies entry {entry.get("name")!r} references ats={ats!r} '
            f'which is not in ATS_ADAPTERS'
        )
