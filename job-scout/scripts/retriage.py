#!/usr/bin/env python3
"""Manual retriage entry point: scan Pending JDs.md for filled JDs and score them."""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pending_inbox as pi  # noqa: E402
import triage_and_write_obsidian as triage  # noqa: E402
from job_scout_lib import (  # noqa: E402
    load_env,
    load_project_config,
    nested_get,
    obsidian_output_base_dir,
    pending_inbox_md_path,
    resume_source_path,
    sanitize_filename,
)


@dataclass
class PipelineStats:
    raw_count: int = 0
    unique_count: int = 0
    filtered_out: list[dict[str, Any]] = field(default_factory=list)
    passes_count: int = 0
    resolved_count: int = 0


@dataclass
class RetriagePaths:
    pending_md: Path
    opportunities_dir: Path
    daily_reports_dir: Path
    resume_path: Path


def _opp_filename(score: int, company: str, role: str) -> str:
    score = max(0, min(100, score))
    return sanitize_filename(f'{score:02d} - {company} - {role}.md')


def _opp_note_body(job: dict[str, Any], result: dict[str, Any]) -> str:
    company = job.get('jd_company') or job.get('company', '')
    role = job.get('jd_title') or job.get('title', '')
    location = job.get('jd_location') or job.get('location', '')
    url = job.get('external_jd_url', '')
    strengths = '\n'.join(f'- {s}' for s in (result.get('top_strengths') or [])[:6]) or '- None captured'
    gaps = '\n'.join(f'- {s}' for s in (result.get('key_gaps') or [])[:6]) or '- None captured'
    excerpt = (job.get('jd_text_preview') or job.get('jd_text', ''))[:2500].strip()
    return f"""---
match_score: {result.get('match_score', 0)}
verdict: {result.get('verdict', 'unknown')}
triage_mode: {result.get('triage_mode', 'jd_full')}
---

# {company} — {role}

- **Company**: {company}
- **Role**: {role}
- **Location**: {location}
- **Source**: {job.get('external_jd_source', '')}
- **JD URL**: {url}
- **Match score**: {result.get('match_score', 0)}
- **Verdict**: {result.get('verdict', '')}

## Top strengths
{strengths}

## Key gaps
{gaps}

## Quick recommendation
{result.get('quick_recommendation', '')}

## JD summary
{result.get('jd_summary', '')}

## JD excerpt
{excerpt}
"""


def run(
    paths: RetriagePaths,
    *,
    min_jd_text_chars: int = 200,
    today: str | None = None,
    enriched_path: Path | None = None,
    stats: 'PipelineStats | None' = None,
) -> dict[str, Any]:
    today = today or _date.today().isoformat()
    resume_text = paths.resume_path.read_text() if paths.resume_path.exists() else ''

    # Step A: surface today's unresolved jobs into Pending JDs.md.
    appended = 0
    if enriched_path and enriched_path.exists():
        import json as _json
        try:
            enriched_payload = _json.loads(enriched_path.read_text())
        except Exception:
            enriched_payload = {'jobs': []}
        unresolved = [j for j in enriched_payload.get('jobs', [])
                      if (j.get('external_jd_status') or '') == 'unresolved']
        appended = pi.sync_new(paths.pending_md, unresolved, today=today)

    # Step B: pick up filled JDs and score.
    ready = pi.extract_ready_for_triage(paths.pending_md, min_jd_text_chars=min_jd_text_chars)

    scored: list[dict[str, Any]] = []
    scored_keys: list[str] = []
    for job in ready:
        try:
            result = triage.triage_job(job, resume_text, min_jd_text_chars=min_jd_text_chars)
        except Exception as exc:
            scored.append({'job_key': job['job_key'], 'error': str(exc)})
            continue
        body = _opp_note_body(job, result)
        company = job.get('company', '') or 'Company'
        role = job.get('title', '') or 'Untitled'
        fname = _opp_filename(int(result.get('match_score', 0)), company, role)
        out_path = paths.opportunities_dir / fname
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(body)
        scored.append({'job_key': job['job_key'], 'score': int(result.get('match_score', 0)), 'note': str(out_path)})
        scored_keys.append(job['job_key'])

    removed = pi.remove_scored(paths.pending_md, scored_keys) if scored_keys else 0

    # Step C: write the daily report.
    from daily_report import DailyReportInput, write as write_daily_report
    _stats = stats or PipelineStats()

    awaiting = []
    for entry in pi.read_pending(paths.pending_md):
        body = entry.get('body', '')
        company = ''
        title = ''
        for line in body.splitlines():
            s = line.strip()
            if s.startswith('- **Company**:'):
                company = s[len('- **Company**:'):].strip()
            elif s.startswith('- **Title**:'):
                title = s[len('- **Title**:'):].strip()
        awaiting.append({'company': company, 'title': title, 'job_key': entry['job_key']})

    ready_by_key = {j['job_key']: j for j in ready}
    scored_for_report = []
    for s in scored:
        if 'error' in s:
            continue
        job = ready_by_key.get(s['job_key'])
        if not job:
            continue
        scored_for_report.append({
            'company': job.get('company', ''),
            'title': job.get('title', ''),
            'score': s.get('score', 0),
            'verdict': '',
            'opportunity_filename': Path(s['note']).name,
        })

    report_path = paths.daily_reports_dir / f'{today}.md'
    write_daily_report(report_path, DailyReportInput(
        report_date=today,
        raw_count=_stats.raw_count,
        unique_count=_stats.unique_count,
        filtered_out=_stats.filtered_out,
        passes_count=_stats.passes_count,
        resolved_count=_stats.resolved_count,
        awaiting_jd=awaiting,
        scored_today=scored_for_report,
    ))

    return {
        'today': today,
        'appended_to_pending': appended,
        'ready_count': len(ready),
        'scored_count': sum(1 for s in scored if 'score' in s),
        'errored_count': sum(1 for s in scored if 'error' in s),
        'removed_count': removed,
        'scored': scored,
    }


def _collect_stats(env: dict[str, str], cfg: dict[str, Any]) -> 'PipelineStats':
    """Read jobs_today / jobs_filtered / jobs_enriched and build PipelineStats."""
    from job_scout_lib import (
        jobs_inbox_json_path, jobs_filtered_json_path, jobs_enriched_json_path,
    )
    import json as _json

    def _load(p: Path) -> dict[str, Any]:
        if not p.exists():
            return {}
        try:
            return _json.loads(p.read_text())
        except Exception:
            return {}

    today_p = _load(jobs_inbox_json_path(env, cfg))
    filtered_p = _load(jobs_filtered_json_path(env, cfg))
    enriched_p = _load(jobs_enriched_json_path(env, cfg))

    raw = int(today_p.get('job_count') or len(today_p.get('jobs', [])))
    passes = int(filtered_p.get('passes_count') or 0)
    filtered_out_jobs = [
        {'company': j.get('company', ''), 'title': j.get('title', ''),
         'filter_reason': j.get('filter_reason', '')}
        for j in filtered_p.get('jobs', [])
        if j.get('filter_status') == 'filtered_out'
    ]
    unique = int(filtered_p.get('passes_count', 0)) + int(filtered_p.get('filtered_out_count', 0))
    resolved = sum(1 for j in enriched_p.get('jobs', [])
                   if j.get('external_jd_status') in {'resolved', 'linkedin_public', 'ats_api'})
    return PipelineStats(
        raw_count=raw,
        unique_count=unique or raw,
        filtered_out=filtered_out_jobs,
        passes_count=passes,
        resolved_count=resolved,
    )


def main() -> None:
    p = argparse.ArgumentParser(description='Sync Pending JDs.md and score any with filled JDs.')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    env = load_env()
    cfg = load_project_config(env)
    base = Path(env['OBSIDIAN_VAULT_PATH']).expanduser() / obsidian_output_base_dir(env, cfg)
    from job_scout_lib import jobs_enriched_json_path
    paths = RetriagePaths(
        pending_md=pending_inbox_md_path(env, cfg),
        opportunities_dir=base / 'Opportunities',
        daily_reports_dir=base / 'Daily Reports',
        resume_path=resume_source_path(env, cfg),
    )
    enriched_path = jobs_enriched_json_path(env, cfg)
    min_chars = int(nested_get(cfg, 'scoring', 'min_jd_text_chars', default=200) or 200)
    if args.dry_run:
        ready = pi.extract_ready_for_triage(paths.pending_md, min_jd_text_chars=min_chars)
        print(f'dry-run: {len(ready)} ready for triage')
        return
    stats = _collect_stats(env, cfg)
    summary = run(paths, min_jd_text_chars=min_chars, enriched_path=enriched_path, stats=stats)
    print(f'pending_appended={summary["appended_to_pending"]} scored={summary["scored_count"]} '
          f'errored={summary["errored_count"]} removed={summary["removed_count"]}')


if __name__ == '__main__':
    main()
