import pytest

import triage_and_write_obsidian as triage


def test_triage_job_refuses_empty_jd():
    job = {'job_key': 'k1', 'company': 'X', 'jd_title': 'T', 'jd_text': ''}
    with pytest.raises(ValueError, match='JD body'):
        triage.triage_job(job, 'RESUME', min_jd_text_chars=200)


def test_triage_job_refuses_short_jd():
    job = {'job_key': 'k1', 'company': 'X', 'jd_title': 'T', 'jd_text': 'short JD ' * 5}
    with pytest.raises(ValueError, match='JD body'):
        triage.triage_job(job, 'RESUME', min_jd_text_chars=200)


def test_triage_job_calls_llm_when_jd_long_enough(monkeypatch):
    captured = {}

    def fake_call_json(prompt, model=None, timeout=120):
        captured['prompt'] = prompt
        captured['model'] = model
        return {
            'match_score': 72,
            'verdict': 'medium_match',
            'top_strengths': ['s1'],
            'key_gaps': ['g1'],
            'quick_recommendation': 'apply',
            'jd_summary': 'sum',
        }

    monkeypatch.setattr(triage, 'llm_call_json', fake_call_json)
    long_jd = 'JD body content. ' * 30
    assert len(long_jd) >= 200
    job = {
        'job_key': 'k1', 'company': 'GitHub', 'jd_title': 'Staff Software Engineer',
        'jd_text': long_jd, 'jd_location': 'Remote', 'external_jd_url': 'https://x/',
    }
    out = triage.triage_job(job, 'RESUME', min_jd_text_chars=200)
    assert out['match_score'] == 72
    assert out['verdict'] == 'medium_match'
    assert 'JD body content' in captured['prompt']
    assert 'metadata_only' not in captured['prompt']  # confirms only the JD-body prompt remains
    assert out.get('triage_mode') == 'jd_full'


def test_triage_job_clamps_score_range(monkeypatch):
    monkeypatch.setattr(triage, 'llm_call_json', lambda p, model=None, timeout=120: {
        'match_score': 200, 'verdict': 'strong_match',
        'top_strengths': [], 'key_gaps': [], 'quick_recommendation': '', 'jd_summary': '',
    })
    long_jd = 'x' * 250
    out = triage.triage_job({'jd_text': long_jd, 'jd_title': 't', 'company': 'c'}, 'R', min_jd_text_chars=200)
    assert out['match_score'] == 100


def test_metadata_only_status_rejected():
    """Legacy metadata_only entries should not be re-triaged."""
    long_jd = 'x' * 250
    job = {'jd_text': long_jd, 'external_jd_status': 'metadata_only'}
    with pytest.raises(ValueError, match='unsupported external_jd_status'):
        triage.triage_job(job, 'R', min_jd_text_chars=200)


def test_triage_mode_appears_in_opportunity_frontmatter():
    """build_opportunity_frontmatter must include triage_mode field."""
    fm = triage.build_opportunity_frontmatter({
        'match_score': 78, 'verdict': 'strong_match', 'triage_mode': 'jd_full',
    })
    assert 'match_score: 78' in fm
    assert 'verdict: strong_match' in fm
    assert 'triage_mode: jd_full' in fm


def test_update_opportunity_frontmatter_updates_triage_mode():
    """update_opportunity_frontmatter must update existing or append triage_mode."""
    # Existing frontmatter with triage_mode → updated.
    content_a = (
        "---\nmatch_score: 50\nverdict: weak_match\ntriage_mode: metadata_only\n"
        "---\n\n# Body\n"
    )
    out_a = triage.update_opportunity_frontmatter(content_a, {
        'match_score': 75, 'verdict': 'strong_match', 'triage_mode': 'jd_full',
    })
    assert 'triage_mode: jd_full' in out_a
    assert 'metadata_only' not in out_a

    # Existing frontmatter without triage_mode → triage_mode added.
    content_b = "---\nmatch_score: 50\nverdict: weak_match\n---\n\n# Body\n"
    out_b = triage.update_opportunity_frontmatter(content_b, {
        'match_score': 60, 'verdict': 'medium_match', 'triage_mode': 'jd_full',
    })
    assert 'triage_mode: jd_full' in out_b
