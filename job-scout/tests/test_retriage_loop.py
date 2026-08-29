import json
from pathlib import Path

import retriage


def test_retriage_processes_filled_jd_and_removes_section(tmp_path, monkeypatch):
    pending_path = tmp_path / 'Pending JDs.md'
    opportunities_dir = tmp_path / 'Opportunities'
    opportunities_dir.mkdir()
    daily_reports_dir = tmp_path / 'Daily Reports'
    daily_reports_dir.mkdir()
    resume_path = tmp_path / 'resume.md'
    resume_path.write_text('# Resume\n- Staff platform engineer with 20y experience.\n')

    # Seed Pending JDs.md with one entry whose JD has been pasted.
    import pending_inbox as pi
    pi.sync_new(pending_path, [{
        'job_key': 'k1', 'title': 'Staff Software Engineer', 'company': 'GitHub',
        'location': 'Remote', 'linkedin_url': 'https://x/', 'filter_reason': '',
        'external_jd_status': 'unresolved',
    }], today='2026-04-25')
    txt = pending_path.read_text()
    long_jd = ('GitHub seeks a Staff Software Engineer for platform infra. ' * 8).strip()
    assert len(long_jd) >= 200
    pending_path.write_text(txt.replace('<!-- paste JD body below this line, then save -->',
                                         '<!-- paste JD body below this line, then save -->\n\n' + long_jd))

    # Stub the LLM call.
    import triage_and_write_obsidian as triage
    monkeypatch.setattr(triage, 'llm_call_json', lambda p, model=None, timeout=120: {
        'match_score': 80, 'verdict': 'strong_match',
        'top_strengths': ['platform'], 'key_gaps': [], 'quick_recommendation': 'apply', 'jd_summary': 'GitHub staff role.',
    })

    # Run.
    paths = retriage.RetriagePaths(
        pending_md=pending_path,
        opportunities_dir=opportunities_dir,
        daily_reports_dir=daily_reports_dir,
        resume_path=resume_path,
    )
    summary = retriage.run(paths, min_jd_text_chars=200, today='2026-04-25')

    assert summary['scored_count'] == 1
    assert summary['removed_count'] == 1
    # Section gone:
    assert 'k1' not in pending_path.read_text()
    # Opportunity note exists with 80 prefix:
    notes = list(opportunities_dir.glob('80 - GitHub - *.md'))
    assert len(notes) == 1
    note_text = notes[0].read_text()
    assert 'match_score: 80' in note_text
    assert 'verdict: strong_match' in note_text
    assert 'triage_mode: jd_full' in note_text


def test_retriage_syncs_new_unresolved_into_pending(tmp_path, monkeypatch):
    pending_path = tmp_path / 'Pending JDs.md'
    opportunities_dir = tmp_path / 'Opportunities'; opportunities_dir.mkdir()
    daily_reports_dir = tmp_path / 'Daily Reports'; daily_reports_dir.mkdir()
    resume_path = tmp_path / 'resume.md'; resume_path.write_text('# r')
    enriched = tmp_path / 'jobs_enriched.json'
    import json
    enriched.write_text(json.dumps({'jobs': [
        {'job_key': 'newkey', 'title': 'Staff Software Engineer', 'company': 'GitHub',
         'location': 'Remote', 'linkedin_url': 'https://x/', 'external_jd_status': 'unresolved'},
        {'job_key': 'skipme', 'title': 'X', 'company': 'Y',
         'external_jd_status': 'resolved', 'jd_text': 'long' * 100},
    ]}))

    paths = retriage.RetriagePaths(
        pending_md=pending_path, opportunities_dir=opportunities_dir,
        daily_reports_dir=daily_reports_dir, resume_path=resume_path,
    )
    summary = retriage.run(paths, min_jd_text_chars=200, today='2026-04-25', enriched_path=enriched)

    assert summary['appended_to_pending'] == 1
    assert 'newkey' in pending_path.read_text()
    assert 'skipme' not in pending_path.read_text()


def test_collect_stats_reads_disk_json(tmp_path, monkeypatch):
    import retriage
    import json
    (tmp_path / 'data' / 'inbox').mkdir(parents=True)
    (tmp_path / 'data' / 'inbox' / 'jobs_today.json').write_text(json.dumps({
        'job_count': 53, 'jobs': []
    }))
    (tmp_path / 'data' / 'inbox' / 'jobs_filtered.json').write_text(json.dumps({
        'passes_count': 6, 'filtered_out_count': 9,
        'jobs': [
            {'filter_status': 'filtered_out', 'company': 'Figma', 'title': 'Frontend Engineer', 'filter_reason': 'matched exclude: frontend'},
            {'filter_status': 'passes_rules', 'company': 'GitHub', 'title': 'Staff SWE'},
        ],
    }))
    (tmp_path / 'data' / 'inbox' / 'jobs_enriched.json').write_text(json.dumps({
        'jobs': [
            {'external_jd_status': 'resolved'}, {'external_jd_status': 'unresolved'},
        ],
    }))
    env = {'JOB_SCOUT_ROOT': str(tmp_path)}
    cfg = {}
    stats = retriage._collect_stats(env, cfg)
    assert stats.raw_count == 53
    assert stats.passes_count == 6
    assert stats.resolved_count == 1
    assert len(stats.filtered_out) == 1
    assert stats.filtered_out[0]['company'] == 'Figma'
