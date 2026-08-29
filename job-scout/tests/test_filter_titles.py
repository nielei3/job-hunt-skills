import filter_titles as ft


CFG = {
    'filter': {
        'exclude_keywords': ['frontend', 'ml engineer', 'manager', 'mobile'],
        'include_keywords': ['staff', 'principal', 'platform', 'infrastructure'],
        'allow_override_companies': ['Anthropic', 'OpenAI'],
        'ambiguous_policy': 'pending',  # rules-only; no LLM in this task
        'llm_model': 'claude-haiku-4.5',
    }
}


def test_override_company_passes_even_with_excluded_title():
    job = {'title': 'Frontend Engineer', 'company': 'Anthropic'}
    out = ft.classify_one(job, CFG)
    assert out['filter_status'] == 'passes_rules'
    assert 'override' in out['filter_reason'].lower()


def test_exclude_keyword_filters_out():
    job = {'title': 'Senior Frontend Engineer', 'company': 'Figma'}
    out = ft.classify_one(job, CFG)
    assert out['filter_status'] == 'filtered_out'
    assert 'frontend' in out['filter_reason']


def test_exclude_match_is_case_insensitive():
    job = {'title': 'SR. ML ENGINEER, Recommendations', 'company': 'Netflix'}
    out = ft.classify_one(job, CFG)
    assert out['filter_status'] == 'filtered_out'
    assert 'ml engineer' in out['filter_reason']


def test_include_keyword_passes():
    job = {'title': 'Staff Software Engineer', 'company': 'GitHub'}
    out = ft.classify_one(job, CFG)
    assert out['filter_status'] == 'passes_rules'
    assert 'staff' in out['filter_reason']


def test_exclude_beats_include():
    # "Staff Frontend Engineer" — has both. exclude wins.
    job = {'title': 'Staff Frontend Engineer', 'company': 'Figma'}
    out = ft.classify_one(job, CFG)
    assert out['filter_status'] == 'filtered_out'


def test_include_match_via_substring():
    job = {'title': 'Software Engineer, AI Platforms', 'company': 'Figma'}
    out = ft.classify_one(job, CFG)
    # 'platform' is in include_keywords as substring -> matches
    assert out['filter_status'] == 'passes_rules'


def test_truly_ambiguous_with_pending_policy():
    job = {'title': 'Software Engineer, Habitat', 'company': 'OpenAI Habitat Inc'}
    # company override wouldn't match (different company); no include/exclude hit
    cfg = {'filter': {**CFG['filter'], 'allow_override_companies': []}}
    out = ft.classify_one(job, cfg)
    assert out['filter_status'] == 'passes_rules'  # ambiguous_policy: pending → passes
    assert 'ambiguous' in out['filter_reason']


def test_classify_all_writes_summary_payload():
    jobs = [
        {'title': 'Staff Software Engineer', 'company': 'GitHub', 'job_key': 'a'},
        {'title': 'Senior Frontend Engineer', 'company': 'Figma', 'job_key': 'b'},
    ]
    payload = ft.classify_all(jobs, CFG)
    assert payload['input_count'] == 2
    assert payload['passes_count'] == 1
    assert payload['filtered_out_count'] == 1
    assert payload['jobs'][0]['filter_status'] == 'passes_rules'
    assert payload['jobs'][1]['filter_status'] == 'filtered_out'


def test_dedupe_within_run_by_company_and_title():
    jobs = [
        {'title': 'Staff Software Engineer', 'company': 'GitHub', 'job_key': 'a'},
        {'title': 'staff software engineer', 'company': 'github', 'job_key': 'b'},  # dup
    ]
    payload = ft.classify_all(jobs, CFG)
    assert payload['input_count'] == 2
    assert payload['passes_count'] == 1  # second is dropped as dup
    assert any(j.get('filter_status') == 'duplicate' for j in payload['jobs'])
