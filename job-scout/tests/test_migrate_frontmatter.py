from pathlib import Path

import migrate_opportunity_frontmatter as m


def test_adds_triage_mode_when_missing(tmp_path):
    p = tmp_path / '60 - Reddit - Senior SWE.md'
    p.write_text('---\nmatch_score: 60\nverdict: medium_match\n---\n\n# Reddit — Senior SWE\nbody\n')
    changed = m.migrate_dir(tmp_path, default_mode='metadata_only')
    assert changed == 1
    text = p.read_text()
    assert 'triage_mode: metadata_only' in text


def test_skips_when_already_set(tmp_path):
    p = tmp_path / 'a.md'
    p.write_text('---\nmatch_score: 50\nverdict: weak_match\ntriage_mode: jd_full\n---\n\nbody\n')
    changed = m.migrate_dir(tmp_path, default_mode='metadata_only')
    assert changed == 0
    assert 'triage_mode: jd_full' in p.read_text()


def test_skips_files_without_frontmatter(tmp_path):
    p = tmp_path / 'no_fm.md'
    p.write_text('# bare\nbody\n')
    changed = m.migrate_dir(tmp_path, default_mode='metadata_only')
    assert changed == 0
