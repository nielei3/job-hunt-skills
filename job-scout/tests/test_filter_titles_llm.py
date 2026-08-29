import filter_titles as ft


PROFILE = {
    'role_type_description': 'IC platform/infrastructure engineer.',
    'role_type_exclusions': ['ML Engineer', 'Frontend', 'Manager'],
}

CFG_LLM = {
    'filter': {
        'exclude_keywords': ['frontend'],
        'include_keywords': ['staff'],
        'allow_override_companies': [],
        'ambiguous_policy': 'llm',
        'llm_model': 'claude-haiku-4.5',
    }
}


def test_llm_pass_promotes_to_passes_rules(monkeypatch):
    calls = []
    def fake_call_json(prompt, model=None, timeout=120):
        calls.append({'prompt': prompt, 'model': model})
        return {'role_type_match': 'pass'}
    monkeypatch.setattr(ft, 'llm_call_json', fake_call_json)

    job = {'title': 'Software Engineer, AI Platforms', 'company': 'Figma', 'snippet': '...'}
    payload = ft.classify_all([job], CFG_LLM, profile=PROFILE)
    assert payload['jobs'][0]['filter_status'] == 'passes_rules'
    assert 'llm: pass' in payload['jobs'][0]['filter_reason']
    assert payload['jobs'][0]['ambiguous_resolved_by'] == 'claude-haiku-4.5'
    assert len(calls) == 1
    assert 'AI Platforms' in calls[0]['prompt']
    assert 'IC platform/infrastructure' in calls[0]['prompt']


def test_llm_exclude_filters_out(monkeypatch):
    monkeypatch.setattr(ft, 'llm_call_json', lambda p, model=None, timeout=120: {'role_type_match': 'exclude'})
    job = {'title': 'Software Engineer, ML Recommendations', 'company': 'Netflix'}
    payload = ft.classify_all([job], CFG_LLM, profile=PROFILE)
    assert payload['jobs'][0]['filter_status'] == 'filtered_out'
    assert 'llm: exclude' in payload['jobs'][0]['filter_reason']


def test_llm_invalid_output_fails_closed(monkeypatch):
    monkeypatch.setattr(ft, 'llm_call_json', lambda p, model=None, timeout=120: {'foo': 'bar'})
    job = {'title': 'Software Engineer, X', 'company': 'AcmeCo'}
    payload = ft.classify_all([job], CFG_LLM, profile=PROFILE)
    assert payload['jobs'][0]['filter_status'] == 'filtered_out'
    assert 'llm output invalid' in payload['jobs'][0]['filter_reason']


def test_llm_raises_fails_closed(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError('subprocess died')
    monkeypatch.setattr(ft, 'llm_call_json', boom)
    job = {'title': 'Software Engineer, X', 'company': 'AcmeCo'}
    payload = ft.classify_all([job], CFG_LLM, profile=PROFILE)
    assert payload['jobs'][0]['filter_status'] == 'filtered_out'
    assert 'llm error' in payload['jobs'][0]['filter_reason']


def test_llm_with_empty_profile_fails_closed_no_call(monkeypatch):
    """Empty profile dict (e.g. profile.yaml missing) must fail closed without calling LLM."""
    calls = []
    def fake_call_json(prompt, model=None, timeout=120):
        calls.append(prompt)
        return {'role_type_match': 'pass'}
    monkeypatch.setattr(ft, 'llm_call_json', fake_call_json)

    job = {'title': 'Software Engineer, X', 'company': 'AcmeCo'}
    payload = ft.classify_all([job], CFG_LLM, profile={})
    assert payload['jobs'][0]['filter_status'] == 'filtered_out'
    assert 'no profile available' in payload['jobs'][0]['filter_reason']
    assert len(calls) == 0  # MUST NOT have called the LLM
