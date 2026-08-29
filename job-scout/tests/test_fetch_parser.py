from email.message import EmailMessage
from pathlib import Path

import fetch_job_alert_emails as fa


def _make_msg(html: str) -> EmailMessage:
    msg = EmailMessage()
    msg['From'] = 'jobs-noreply@linkedin.com'
    msg['To'] = 'me@example.com'
    msg['Subject'] = "You may be a fit for GitHub's Staff Software Engineer role"
    msg['Date'] = 'Fri, 24 Apr 2026 20:23:20 +0000'
    msg['Message-Id'] = '<digest-12345@linkedin.com>'
    msg.add_alternative(html, subtype='html')
    return msg


def test_digest_extracts_six_distinct_jobs(fixtures_dir):
    html = (fixtures_dir / 'linkedin_digest_email.html').read_text()
    msg = _make_msg(html)
    jobs = fa.build_jobs_from_message(msg)
    # Six job cards in the fixture; expect six entries (no offset duplicates, no fallback message-level row).
    assert len(jobs) == 6
    titles = [j.get('title', '').lower() for j in jobs]
    companies = [j.get('company', '') for j in jobs]
    assert any('staff software engineer' in t for t in titles)
    assert 'GitHub' in companies
    assert 'OpenAI' in companies
    assert 'Anthropic' not in companies  # not in fixture; sanity


def test_digest_no_offset_company_field(fixtures_dir):
    html = (fixtures_dir / 'linkedin_digest_email.html').read_text()
    jobs = fa.build_jobs_from_message(_make_msg(html))
    # Bug regression: company must never equal 'Your job alert for software engineer'
    bad_phrases = ['your job alert', 'new jobs in', 'see all jobs']
    for j in jobs:
        c = (j.get('company', '') or '').lower()
        for bp in bad_phrases:
            assert bp not in c, f'company looks like email chrome: {c!r}'


def test_digest_dedupes_within_email(fixtures_dir):
    html = (fixtures_dir / 'linkedin_digest_email.html').read_text()
    jobs = fa.build_jobs_from_message(_make_msg(html))
    keys = [j.get('linkedin_url', '') for j in jobs]
    # Each linkedin_url should be unique.
    assert len(keys) == len(set(keys))


def test_digest_jobs_have_clean_title_company_location(fixtures_dir):
    html = (fixtures_dir / 'linkedin_digest_email.html').read_text()
    jobs = fa.build_jobs_from_message(_make_msg(html))
    for j in jobs:
        assert j.get('title', '').strip(), f'job missing title: {j}'
        assert j.get('company', '').strip(), f'job missing company: {j}'
        # Location may legitimately be empty; not asserted.


def test_digest_distinguishes_duplicate_titles_by_position(fixtures_dir):
    """Two jobs with identical title must NOT both inherit the first occurrence's company."""
    html = (fixtures_dir / 'linkedin_digest_email_dup_titles.html').read_text()
    jobs = fa.build_jobs_from_message(_make_msg(html))
    assert len(jobs) == 2
    job_a = next(j for j in jobs if '1111111111' in j['linkedin_url'])
    job_b = next(j for j in jobs if '2222222222' in j['linkedin_url'])
    assert job_a['company'] == 'CompanyA', f'expected CompanyA for first job, got {job_a["company"]!r}'
    assert job_b['company'] == 'CompanyB', f'expected CompanyB for second job, got {job_b["company"]!r}'
