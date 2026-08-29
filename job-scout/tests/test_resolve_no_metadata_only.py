import resolve_external_jd as r


def test_metadata_only_fallback_removed_from_source():
    """A code-level guard: the metadata_only fast-path strings must be gone."""
    src = (
        __import__('pathlib').Path(r.__file__).read_text()
    )
    # No active code path should set external_jd_status to metadata_only.
    assert "external_jd_status'] = 'metadata_only'" not in src
    assert 'is_high_value_job' not in src


def test_unresolved_stays_unresolved_when_no_resolver(monkeypatch):
    job = {
        'title': 'Staff Software Engineer',
        'company': 'AcmeCo',  # no resolver in cfg
        'location': '',
        'linkedin_url': 'https://www.linkedin.com/comm/jobs/search?...',  # not a /jobs/view/<id>
        'snippet': '',
        'job_key': 'k1',
    }
    cfg = {'external_jd': {'resolvers': {}}, 'level_calibration': {'unknown_policy': 'in_band'}}
    env = {'JOB_SCOUT_DISABLE_LINKEDIN_FETCH': '1'}
    out = r.enrich_job(job, env, cfg)
    assert out['external_jd_status'] == 'unresolved'
    assert 'jd_text' in out and out['jd_text'] == ''
