from pathlib import Path

import pending_inbox as pi


PENDING_HEADER = pi.PENDING_HEADER  # constant for clarity


def test_read_pending_empty_file_returns_empty_list(tmp_path):
    path = tmp_path / 'Pending JDs.md'
    assert pi.read_pending(path) == []


def test_read_pending_missing_file_returns_empty_list(tmp_path):
    assert pi.read_pending(tmp_path / 'nope.md') == []


def test_sync_new_writes_sections_for_unresolved_jobs(tmp_path):
    path = tmp_path / 'Pending JDs.md'
    jobs = [
        {
            'job_key': 'aaa111',
            'title': 'Staff Software Engineer',
            'company': 'GitHub',
            'location': 'United States (Remote)',
            'linkedin_url': 'https://www.linkedin.com/comm/jobs/view/4392832600/',
            'filter_reason': 'matched include: staff',
            'external_jd_status': 'unresolved',
        },
        {
            'job_key': 'bbb222',
            'title': 'Infrastructure Engineer, Sandboxing',
            'company': 'Anthropic',
            'location': 'Seattle, WA',
            'linkedin_url': 'https://www.linkedin.com/comm/jobs/view/440447649/',
            'filter_reason': 'override: Anthropic',
            'external_jd_status': 'unresolved',
        },
    ]
    added = pi.sync_new(path, jobs, today='2026-04-25')
    assert added == 2

    text = path.read_text()
    assert PENDING_HEADER in text
    assert '<!-- JOB-START id=aaa111' in text
    assert 'GitHub — Staff Software Engineer' in text
    assert '<!-- JOB-START id=bbb222' in text
    assert 'Anthropic — Infrastructure Engineer, Sandboxing' in text


def test_sync_new_skips_already_present_keys(tmp_path):
    path = tmp_path / 'Pending JDs.md'
    j1 = {'job_key': 'aaa', 'title': 'T1', 'company': 'C1', 'location': '', 'linkedin_url': '', 'filter_reason': '', 'external_jd_status': 'unresolved'}
    pi.sync_new(path, [j1], today='2026-04-25')
    added2 = pi.sync_new(path, [j1, dict(j1, job_key='bbb', title='T2')], today='2026-04-26')
    assert added2 == 1
    entries = pi.read_pending(path)
    assert {e['job_key'] for e in entries} == {'aaa', 'bbb'}


def test_extract_ready_for_triage_picks_up_filled_jds(tmp_path):
    path = tmp_path / 'Pending JDs.md'
    j = {'job_key': 'k1', 'title': 'Staff Software Engineer', 'company': 'GitHub', 'location': 'Remote', 'linkedin_url': 'https://x/', 'filter_reason': '', 'external_jd_status': 'unresolved'}
    pi.sync_new(path, [j], today='2026-04-25')
    # User pastes JD content into the section.
    raw = path.read_text()
    long_jd = ('GitHub is hiring a Staff Software Engineer to build platform tooling. ' * 8).strip()
    assert len(long_jd) >= 200
    raw = raw.replace(
        '<!-- paste JD body below this line, then save -->',
        '<!-- paste JD body below this line, then save -->\n\n' + long_jd,
    )
    path.write_text(raw)

    ready = pi.extract_ready_for_triage(path, min_jd_text_chars=200)
    assert len(ready) == 1
    assert ready[0]['job_key'] == 'k1'
    assert long_jd[:50] in ready[0]['jd_text']
    assert ready[0]['external_jd_status'] == 'user_supplied'
    assert ready[0]['external_jd_source'] == 'pending_inbox_manual'


def test_extract_ready_skips_empty_or_short_jd(tmp_path):
    path = tmp_path / 'Pending JDs.md'
    j = {'job_key': 'k1', 'title': 'X', 'company': 'Y', 'location': '', 'linkedin_url': '', 'filter_reason': '', 'external_jd_status': 'unresolved'}
    pi.sync_new(path, [j], today='2026-04-25')
    # No paste -> empty section
    assert pi.extract_ready_for_triage(path, min_jd_text_chars=200) == []
    # Short paste -> still skipped
    raw = path.read_text().replace(
        '<!-- paste JD body below this line, then save -->',
        '<!-- paste JD body below this line, then save -->\n\nshort',
    )
    path.write_text(raw)
    assert pi.extract_ready_for_triage(path, min_jd_text_chars=200) == []


def test_remove_scored_drops_only_named_sections(tmp_path):
    path = tmp_path / 'Pending JDs.md'
    j1 = {'job_key': 'k1', 'title': 'A', 'company': 'C', 'location': '', 'linkedin_url': '', 'filter_reason': '', 'external_jd_status': 'unresolved'}
    j2 = {'job_key': 'k2', 'title': 'B', 'company': 'C', 'location': '', 'linkedin_url': '', 'filter_reason': '', 'external_jd_status': 'unresolved'}
    j3 = {'job_key': 'k3', 'title': 'C', 'company': 'C', 'location': '', 'linkedin_url': '', 'filter_reason': '', 'external_jd_status': 'unresolved'}
    pi.sync_new(path, [j1, j2, j3], today='2026-04-25')
    removed = pi.remove_scored(path, ['k1', 'k3'])
    assert removed == 2
    remaining = pi.read_pending(path)
    assert [e['job_key'] for e in remaining] == ['k2']


def test_round_trip_preserves_user_edits_in_other_sections(tmp_path):
    path = tmp_path / 'Pending JDs.md'
    j = {'job_key': 'k1', 'title': 'X', 'company': 'Y', 'location': '', 'linkedin_url': '', 'filter_reason': '', 'external_jd_status': 'unresolved'}
    pi.sync_new(path, [j], today='2026-04-25')
    text = path.read_text()
    text = text.replace('# Pending JDs', '# Pending JDs\n\n> User-added prose at top')
    path.write_text(text)
    pi.sync_new(path, [dict(j, job_key='k2', title='X2')], today='2026-04-26')
    out = path.read_text()
    assert '> User-added prose at top' in out
    assert 'k2' in out


def test_remove_scored_all_sections_leaves_clean_file(tmp_path):
    """Removing all sections must not leave stale '---' dividers."""
    path = tmp_path / 'Pending JDs.md'
    jobs = [
        {'job_key': f'k{i}', 'title': f'T{i}', 'company': 'C', 'location': '',
         'linkedin_url': '', 'filter_reason': '', 'external_jd_status': 'unresolved'}
        for i in range(3)
    ]
    pi.sync_new(path, jobs, today='2026-04-25')
    pi.remove_scored(path, ['k0', 'k1', 'k2'])
    text = path.read_text()
    # No section markers remain.
    assert '<!-- JOB-START' not in text
    # No two adjacent '---' lines.
    import re as _re
    assert _re.search(r'\n---\s*\n+\s*---', text) is None, f'stale divider in:\n{text!r}'
    # Re-syncing a new job after wipe still works cleanly.
    pi.sync_new(path, [{'job_key': 'fresh', 'title': 'T', 'company': 'C', 'location': '',
                        'linkedin_url': '', 'filter_reason': '', 'external_jd_status': 'unresolved'}],
                today='2026-04-26')
    assert pi.read_pending(path)[0]['job_key'] == 'fresh'


def test_section_for_rejects_unsafe_job_key():
    """job_key containing '-->' or whitespace must raise ValueError."""
    import pytest
    base = {'title': 'T', 'company': 'C', 'location': '', 'linkedin_url': '', 'filter_reason': ''}
    with pytest.raises(ValueError, match='not safe'):
        pi._section_for({**base, 'job_key': 'abc-->bad'}, '2026-04-25')
    with pytest.raises(ValueError, match='not safe'):
        pi._section_for({**base, 'job_key': 'abc def'}, '2026-04-25')
    with pytest.raises(ValueError, match='not safe'):
        pi._section_for({**base, 'job_key': ''}, '2026-04-25')
    # Sanity: a clean key works.
    out = pi._section_for({**base, 'job_key': 'goodkey123'}, '2026-04-25')
    assert 'JOB-START id=goodkey123' in out
