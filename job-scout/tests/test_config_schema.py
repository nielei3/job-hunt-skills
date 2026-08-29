from pathlib import Path

import yaml

import job_scout_lib as lib


def test_default_config_has_filter_section():
    cfg_path = Path(lib.ROOT) / 'config' / 'job-scout.yaml'
    cfg = yaml.safe_load(cfg_path.read_text())
    f = cfg['filter']
    assert isinstance(f['exclude_keywords'], list) and 'frontend' in f['exclude_keywords']
    assert isinstance(f['include_keywords'], list) and 'staff' in f['include_keywords']
    assert isinstance(f['allow_override_companies'], list)
    assert f['ambiguous_policy'] in {'llm', 'pending'}
    assert isinstance(f.get('llm_model'), str) and f['llm_model']


def test_default_config_has_scoring_section():
    cfg_path = Path(lib.ROOT) / 'config' / 'job-scout.yaml'
    cfg = yaml.safe_load(cfg_path.read_text())
    s = cfg['scoring']
    assert s['require_jd_text'] is True
    assert isinstance(s['min_jd_text_chars'], int) and s['min_jd_text_chars'] >= 100


def test_path_helpers(tmp_path):
    env = {'JOB_SCOUT_ROOT': str(tmp_path), 'OBSIDIAN_VAULT_PATH': str(tmp_path / 'vault')}
    cfg = {'output': {'obsidian_base_dir': 'Career/Jobs'}}
    assert lib.jobs_filtered_json_path(env, cfg) == tmp_path / 'data' / 'inbox' / 'jobs_filtered.json'
    assert lib.jobs_to_score_json_path(env, cfg) == tmp_path / 'data' / 'inbox' / 'jobs_to_score.json'
    assert lib.pending_inbox_md_path(env, cfg) == tmp_path / 'vault' / 'Career' / 'Jobs' / 'Pending JDs.md'
