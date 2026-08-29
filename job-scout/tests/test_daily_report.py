from pathlib import Path

import daily_report as dr


def test_daily_report_renders_summary_and_sections(tmp_path):
    payload = dr.DailyReportInput(
        report_date='2026-04-25',
        raw_count=53,
        unique_count=16,
        filtered_out=[
            {'company': 'Figma', 'title': 'Senior Frontend Engineer', 'filter_reason': 'matched exclude: frontend'},
            {'company': 'Netflix', 'title': 'ML Engineer', 'filter_reason': 'matched exclude: ml engineer'},
        ],
        passes_count=7,
        resolved_count=1,
        awaiting_jd=[
            {'company': 'GitHub', 'title': 'Staff Software Engineer', 'job_key': 'k1'},
            {'company': 'Anthropic', 'title': 'Infrastructure Engineer, Sandboxing', 'job_key': 'k2'},
        ],
        scored_today=[
            {'company': 'Anthropic', 'title': 'Infrastructure Engineer, Sandboxing', 'score': 65, 'verdict': 'medium_match', 'opportunity_filename': '65 - Anthropic - Infrastructure Engineer, Sandboxing.md'},
        ],
    )
    text = dr.render(payload)
    assert '# Job Report — 2026-04-25' in text
    assert '| Raw jobs from LinkedIn alerts | 53 |' in text
    assert '| Filtered out (rules + LLM) | 2 |' in text
    assert '| Awaiting JD (in Pending JDs.md) | 2 |' in text
    assert '| Scored today (incl. manual JD fills) | 1 |' in text
    assert '## Awaiting your JD' in text
    assert 'GitHub — Staff Software Engineer' in text
    assert '[[Opportunities/65 - Anthropic - Infrastructure Engineer, Sandboxing]]' in text
    assert '<details>' in text
    assert 'Senior Frontend Engineer' in text


def test_daily_report_writes_to_correct_path(tmp_path):
    out = tmp_path / 'Daily Reports' / '2026-04-25.md'
    out.parent.mkdir(parents=True)
    payload = dr.DailyReportInput(
        report_date='2026-04-25', raw_count=0, unique_count=0,
        filtered_out=[], passes_count=0, resolved_count=0,
        awaiting_jd=[], scored_today=[],
    )
    dr.write(out, payload)
    assert out.exists()
    assert out.read_text().startswith('# Job Report — 2026-04-25')
