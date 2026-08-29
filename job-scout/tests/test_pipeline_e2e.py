"""End-to-end test of fetch → filter → (mock resolve) → pending_inbox → retriage → daily report."""
import json
from pathlib import Path

import filter_titles as ft
import pending_inbox as pi
import retriage
import triage_and_write_obsidian as triage


CFG = {
    'filter': {
        'exclude_keywords': ['frontend', 'ml engineer', 'manager', 'mobile', 'ios'],
        'include_keywords': ['staff', 'principal', 'platform', 'infrastructure'],
        'allow_override_companies': ['Anthropic'],
        'ambiguous_policy': 'pending',  # no LLM in this test
    },
    'scoring': {'require_jd_text': True, 'min_jd_text_chars': 200},
}


def _seed_jobs() -> list[dict]:
    return [
        {'job_key': 'a', 'title': 'Staff Software Engineer', 'company': 'GitHub', 'location': 'Remote', 'linkedin_url': 'https://x/a', 'snippet': ''},
        {'job_key': 'b', 'title': 'Senior Frontend Engineer', 'company': 'Figma', 'location': 'Remote', 'linkedin_url': 'https://x/b', 'snippet': ''},
        {'job_key': 'c', 'title': 'ML Engineer, Recommendations', 'company': 'Netflix', 'location': 'Remote', 'linkedin_url': 'https://x/c', 'snippet': ''},
        {'job_key': 'd', 'title': 'Engineering Manager, Platform', 'company': 'Walmart', 'location': 'Bellevue, WA', 'linkedin_url': 'https://x/d', 'snippet': ''},
        {'job_key': 'e', 'title': 'Infrastructure Engineer, Sandboxing', 'company': 'Anthropic', 'location': 'Seattle, WA', 'linkedin_url': 'https://x/e', 'snippet': ''},
        {'job_key': 'f', 'title': 'Principal Software Engineer', 'company': 'Microsoft', 'location': 'Remote', 'linkedin_url': 'https://x/f', 'snippet': ''},
        {'job_key': 'g', 'title': 'iOS Engineer', 'company': 'Snap', 'location': 'Seattle, WA', 'linkedin_url': 'https://x/g', 'snippet': ''},
        {'job_key': 'h', 'title': 'Platform Engineer, Internal Tools', 'company': 'AcmeCo', 'location': 'Remote', 'linkedin_url': 'https://x/h', 'snippet': ''},
        {'job_key': 'i', 'title': 'Frontend Engineer (Anthropic Studio)', 'company': 'Anthropic', 'location': 'Remote', 'linkedin_url': 'https://x/i', 'snippet': ''},  # override beats exclude
        {'job_key': 'j', 'title': 'Solutions Architect', 'company': 'AcmeCo', 'location': 'Remote', 'linkedin_url': 'https://x/j', 'snippet': ''},  # ambiguous → pending policy passes
    ]


def test_full_pipeline(tmp_path, monkeypatch):
    pending_path = tmp_path / 'Pending JDs.md'
    opportunities_dir = tmp_path / 'Opportunities'; opportunities_dir.mkdir()
    daily_reports_dir = tmp_path / 'Daily Reports'; daily_reports_dir.mkdir()
    resume_path = tmp_path / 'resume.md'; resume_path.write_text('# r\n- platform engineer 20y')

    # Step 1: filter.
    filter_payload = ft.classify_all(_seed_jobs(), CFG)
    assert filter_payload['filtered_out_count'] == 4  # b frontend, c ml, d manager, g ios
    assert filter_payload['passes_count'] == 6      # a, e, f, h, i, j
    passes = [j for j in filter_payload['jobs'] if j['filter_status'] == 'passes_rules']

    # Step 2: simulate resolver — only 'f' (Microsoft) gets resolved with a real JD body via the PCSX resolver.
    enriched = []
    for j in passes:
        out = dict(j)
        if j['job_key'] == 'f':
            out.update({
                'external_jd_status': 'resolved',
                'jd_text': 'Microsoft Principal Software Engineer role on the Azure platform. ' * 8,
                'jd_title': j['title'], 'jd_company': j['company'], 'jd_location': j['location'],
                'external_jd_url': 'https://jobs.careers.microsoft.com/...',
            })
        else:
            out['external_jd_status'] = 'unresolved'
        enriched.append(out)

    enriched_path = tmp_path / 'jobs_enriched.json'
    enriched_path.write_text(json.dumps({'jobs': enriched}))

    # Step 3: retriage. (resolved 'f' bypasses pending; unresolved go to inbox)
    # In the real pipeline 'f' also flows through retriage via jobs_to_score; for this test we manually
    # paste a JD into pending and verify the retriage loop.
    monkeypatch.setattr(triage, 'llm_call_json', lambda p, model=None, timeout=120: {
        'match_score': 78, 'verdict': 'strong_match',
        'top_strengths': [], 'key_gaps': [], 'quick_recommendation': 'apply', 'jd_summary': 'good',
    })

    paths = retriage.RetriagePaths(
        pending_md=pending_path, opportunities_dir=opportunities_dir,
        daily_reports_dir=daily_reports_dir, resume_path=resume_path,
    )
    summary = retriage.run(paths, min_jd_text_chars=200, today='2026-04-25', enriched_path=enriched_path)
    # Five unresolved (a, e, h, i, j) appended to Pending.
    assert summary['appended_to_pending'] == 5

    # Step 4: simulate user pasting JD for 'a'.
    text = pending_path.read_text()
    # The first JOB-START block is 'a' (insertion order preserved by sync_new).
    long_jd = ('GitHub Staff Software Engineer building developer platform infrastructure. ' * 8).strip()
    assert len(long_jd) >= 200
    # Replace the first paste placeholder.
    text = text.replace('<!-- paste JD body below this line, then save -->',
                        '<!-- paste JD body below this line, then save -->\n\n' + long_jd, 1)
    pending_path.write_text(text)

    # Step 5: re-run retriage; should score 'a' and remove its section.
    summary2 = retriage.run(paths, min_jd_text_chars=200, today='2026-04-25', enriched_path=enriched_path)
    assert summary2['scored_count'] == 1
    assert summary2['removed_count'] == 1
    notes = list(opportunities_dir.glob('78 - GitHub - *.md'))
    assert len(notes) == 1

    # Pending file no longer contains 'a'; still contains the other 4 unresolved.
    final_pending = pending_path.read_text()
    # The id=a marker should be gone (check both possible spacing).
    assert 'id=a ' not in final_pending and 'id=a-->' not in final_pending and 'id=a -->' not in final_pending
    for k in ['e', 'h', 'i', 'j']:
        assert f'id={k}' in final_pending

    # Daily report exists.
    assert (daily_reports_dir / '2026-04-25.md').exists()
