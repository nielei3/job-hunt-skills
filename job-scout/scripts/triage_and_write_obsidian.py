#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from job_scout_lib import (  # noqa: E402
    append_jsonl,
    ensure_parent,
    is_us_location,
    jobs_enriched_json_path,
    load_env,
    load_project_config,
    location_passes_city_filter,
    nested_get,
    obsidian_output_base_dir,
    project_root,
    resume_source_path,
    sanitize_filename,
    write_json_snapshot_and_history,
)
from llm_runner import call_json as llm_call_json


def read_text_with_icloud_fallback(path: Path) -> str:
    original_error: Exception | None = None
    try:
        return path.read_text()
    except PermissionError as exc:
        original_error = exc
    path_str = str(path)
    if 'Mobile Documents' not in path_str:
        if original_error is not None:
            raise original_error
        raise PermissionError(path_str)
    tmp_target = Path('/private/tmp') / sanitize_filename(path.name)
    applescript = f'''
set srcFile to POSIX file "{path_str}" as alias
set dstFolder to POSIX file "/private/tmp/" as alias
tell application "Finder"
  set newFile to duplicate srcFile to dstFolder with replacing
  return POSIX path of (newFile as alias)
end tell
'''
    subprocess.run(['osascript'], input=applescript, text=True, check=True, capture_output=True)
    return tmp_target.read_text()


def unique_job_key(job: dict[str, Any]) -> tuple[str, str]:
    return (
        str(job.get('external_jd_url') or '').strip().lower(),
        str(job.get('jd_title') or job.get('title') or '').strip().lower(),
    )


def load_jobs(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {'jobs': []}
    return json.loads(path.read_text())


RESOLVED_STATUSES = {'resolved', 'linkedin_public', 'ats_api', 'user_supplied'}


def dedupe_resolved_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for job in jobs:
        if job.get('external_jd_status') not in RESOLVED_STATUSES:
            continue
        key = unique_job_key(job)
        current = deduped.get(key)
        if current is None:
            deduped[key] = dict(job)
            deduped[key]['duplicate_count'] = 1
        else:
            current['duplicate_count'] = int(current.get('duplicate_count', 1)) + 1
    return list(deduped.values())


def triage_job(
    job: dict[str, Any],
    resume_text: str,
    *,
    model: str | None = None,
    timeout: int = 120,
    min_jd_text_chars: int = 200,
) -> dict[str, Any]:
    jd_text = (job.get('jd_text') or '').strip()
    status = (job.get('external_jd_status') or '').strip()
    if status and status not in RESOLVED_STATUSES:
        raise ValueError(f'unsupported external_jd_status: {status!r}')
    if len(jd_text) < min_jd_text_chars:
        raise ValueError(
            f'refusing to triage without JD body (got {len(jd_text)} chars, '
            f'need >= {min_jd_text_chars}); job_key={job.get("job_key")!r}'
        )

    prompt = f"""
You are evaluating how well a resume matches a job description.

Scoring rubric (0-100 total):
- Must-have hard requirements: 40
- Core skill overlap: 20
- Relevant domain / industry experience: 15
- Seniority / scope fit: 10
- Location / remote / work authorization fit: 10
- Differentiators / nice-to-haves: 5

Verdict bands:
- 91-100 => strong_match
- 71-90 => medium_match
- 0-70 => weak_match

Rules:
- Use only facts present in the resume and JD.
- Do not invent employers, projects, metrics, dates, or technologies.
- Be conservative.
- Return valid JSON only, no markdown fences.

Return exactly this JSON schema:
{{
  "match_score": 0,
  "verdict": "strong_match|medium_match|weak_match",
  "top_strengths": ["..."],
  "key_gaps": ["..."],
  "quick_recommendation": "...",
  "jd_summary": "..."
}}

Job company: {job.get('company', '')}
Job title: {job.get('jd_title') or job.get('title', '')}
Job location: {job.get('jd_location') or job.get('location', '')}
Job URL: {job.get('external_jd_url', '')}

MASTER RESUME:
{resume_text}

JOB DESCRIPTION:
{jd_text[:12000]}
""".strip()

    result = llm_call_json(prompt, model=model, timeout=timeout)
    score = int(result.get('match_score', 0))
    result['match_score'] = max(0, min(100, score))
    result['triage_mode'] = 'jd_full'
    return result


def notify_discord(message: str) -> None:
    token = os.environ.get('DISCORD_BOT_TOKEN', '').strip()
    channel_id = os.environ.get('DISCORD_HOME_CHANNEL', '').strip()
    if not token or not channel_id:
        return
    try:
        body = json.dumps({'content': message}).encode('utf-8')
        req = Request(
            f'https://discord.com/api/v10/channels/{channel_id}/messages',
            data=body,
            method='POST',
            headers={
                'Authorization': f'Bot {token}',
                'Content-Type': 'application/json',
                'User-Agent': 'job-scout/1.0',
            },
        )
        ctx = ssl.create_default_context()
        with urlopen(req, timeout=15, context=ctx) as resp:
            resp.read()
    except Exception as exc:
        print(f'discord notify failed: {exc}', file=sys.stderr)


def write_markdown(path: Path, content: str) -> None:
    ensure_parent(path)
    _write_with_retry(path, content.rstrip() + '\n')


def _write_with_retry(path: Path, content: str, max_attempts: int = 5, base_delay: float = 1.0) -> None:
    """Write text to an iCloud-backed file with retry on lock contention.

    iCloud evicted files (dataless) raise OSError errno 11 on both read AND write.
    We force a download via brctl first, then retry with exponential backoff.
    """
    import time
    import random

    # Ensure file is materialized before writing
    if path.exists() and "Mobile Documents" in str(path):
        _ensure_icloud_downloaded(path)

    for attempt in range(max_attempts):
        try:
            path.write_text(content, encoding='utf-8')
            return
        except OSError as exc:
            if "deadlock" in str(exc).lower() or "resource temporarily unavailable" in str(exc).lower():
                if attempt == max_attempts - 1:
                    raise
                delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                time.sleep(delay)
            else:
                raise


def _ensure_icloud_downloaded(path: Path, timeout: int = 15) -> None:
    """Force iCloud Drive to download a file locally via brctl.

    Waits until the 'dataless' flag is cleared (file materialized on disk).
    """
    import time

    subprocess.run(["brctl", "download", str(path)], capture_output=True, timeout=10)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = subprocess.run(
                ["stat", "-f", "%f", str(path)],
                capture_output=True, text=True, timeout=5
            )
            flags = int(result.stdout.strip())
            # SF_DATALESS = 0x40000000
            if not (flags & 0x40000000):
                return  # File is materialized
        except (ValueError, subprocess.TimeoutExpired):
            pass
        time.sleep(1)


def append_markdown(path: Path, content: str) -> None:
    ensure_parent(path)
    addition = content.rstrip()
    if path.exists():
        existing = path.read_text().rstrip()
        if existing:
            _write_with_retry(path, existing + '\n\n---\n\n' + addition + '\n')
            return
    _write_with_retry(path, addition + '\n')


def obsidian_root(env: dict[str, str]) -> Path:
    return Path(env['OBSIDIAN_VAULT_PATH']).expanduser()


def build_paths(env: dict[str, str], cfg: dict[str, Any]) -> dict[str, Path]:
    base = obsidian_root(env) / obsidian_output_base_dir(env, cfg)
    return {
        'base': base,
        'daily_reports': base / 'Daily Reports',
        'opportunities': base / 'Opportunities',
    }


def opportunity_filename(job: dict[str, Any], score: int | None = None) -> str:
    """Generate opportunity note filename with optional score prefix.

    If score is provided, prepend zero-padded score for filesystem sort order:
        '72 - Company - Role.md'
    Without score (legacy behavior):
        'Company - Role.md'
    """
    title = job.get('jd_title') or job.get('title') or 'Untitled'
    company = job.get('jd_company') or job.get('company') or 'Company'
    base = sanitize_filename(f'{company} - {title}.md')
    if score is not None:
        return f'{score:02d} - {base}'
    return base


def opportunity_link(name: str) -> str:
    return f'[[Opportunities/{name[:-3]}]]' if name.endswith('.md') else f'[[Opportunities/{name}]]'


def _find_existing_opportunity(opps_dir: Path, low_score_dir: Path, bare_name: str) -> Path | None:
    """Find an existing opportunity note by bare name, checking both score-prefixed and non-prefixed variants.

    Searches in opps_dir, low_score_dir, and Location filter dir for files matching:
      - '<bare_name>' (exact, no prefix)
      - '<NN> - <bare_name>' (with score prefix)
    Returns the first match found, or None.
    """
    import re as _re
    location_dir = opps_dir / 'Filtered' / 'Location'
    search_dirs = [opps_dir, low_score_dir, location_dir]
    # Check exact match first
    for d in search_dirs:
        candidate = d / bare_name
        if candidate.exists():
            return candidate
    # Check score-prefixed variants
    pattern = _re.compile(r'^\d{2,3} - ' + _re.escape(bare_name) + '$')
    for d in search_dirs:
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.is_file() and pattern.match(f.name):
                return f
    return None


def build_opportunity_frontmatter(triage: dict[str, Any]) -> str:
    score = int(triage.get('match_score', 0))
    verdict = triage.get('verdict', 'unknown')
    triage_mode = triage.get('triage_mode', 'jd_full')
    return f"---\nmatch_score: {score}\nverdict: {verdict}\ntriage_mode: {triage_mode}\n---\n\n"


def update_opportunity_frontmatter(content: str, triage: dict[str, Any]) -> str:
    """Update match_score, verdict, and triage_mode in existing YAML frontmatter, or prepend if missing."""
    import re as _re
    score = int(triage.get('match_score', 0))
    verdict = triage.get('verdict', 'unknown')
    triage_mode = triage.get('triage_mode', 'jd_full')
    if content.startswith('---\n'):
        end_idx = content.index('---\n', 4) + 4
        fm = content[:end_idx]
        body = content[end_idx:]
        fm = _re.sub(r'match_score:\s*\d+', f'match_score: {score}', fm)
        fm = _re.sub(r'verdict:\s*\w+', f'verdict: {verdict}', fm)
        if _re.search(r'triage_mode:\s*\S+', fm):
            fm = _re.sub(r'triage_mode:\s*\S+', f'triage_mode: {triage_mode}', fm)
        else:
            # Insert triage_mode before closing ---
            fm = fm.rstrip('\n').rstrip('-').rstrip('\n') + f'\ntriage_mode: {triage_mode}\n---\n'
        return fm + body
    return build_opportunity_frontmatter(triage) + content


def build_opportunity_note(job: dict[str, Any], triage: dict[str, Any] | None = None) -> str:
    company = job.get('jd_company') or job.get('company', '')
    role = job.get('jd_title') or job.get('title', '')
    fm = build_opportunity_frontmatter(triage) if triage else ''
    return f"""{fm}# {company} — {role}

- **Company:** {company}
- **Role:** {role}
- **Location:** {job.get('jd_location') or job.get('location', '')}
- **Source:** {job.get('external_jd_source', '')}
- **JD URL:** {job.get('external_jd_url', '')}
"""


def build_opportunity_review_section(
    job: dict[str, Any],
    triage: dict[str, Any],
    *,
    run_label: str,
) -> str:
    strengths = '\n'.join(f'- {item}' for item in triage.get('top_strengths', [])[:6]) or '- None captured'
    gaps = '\n'.join(f'- {item}' for item in triage.get('key_gaps', [])[:6]) or '- None captured'
    jd_excerpt = (job.get('jd_text_preview') or job.get('jd_text') or '')[:2500].strip()
    return f"""## Review — {run_label}

- **Match score:** {triage.get('match_score', 0)}
- **Verdict:** {triage.get('verdict', 'unknown')}

### Top strengths
{strengths}

### Key gaps
{gaps}

### Quick recommendation
{triage.get('quick_recommendation', '')}

### JD summary
{triage.get('jd_summary', '')}

### JD excerpt
{jd_excerpt}
"""


def build_daily_report_section(
    *,
    run_label: str,
    total_jobs: int,
    resolved_jobs: list[dict[str, Any]],
    unresolved_jobs: list[dict[str, Any]],
    triage_results: list[dict[str, Any]],
) -> str:
    lines = [
        f'## Run — {run_label}',
        '',
        f'- Total alert jobs: **{total_jobs}**',
        f'- Resolved unique roles: **{len(resolved_jobs)}**',
        f'- Unresolved jobs: **{len(unresolved_jobs)}**',
        '',
        '## Resolved opportunities',
        '',
    ]
    sorted_results = sorted(triage_results, key=lambda item: int(item['triage'].get('match_score', 0)), reverse=True)
    for item in sorted_results:
        job = item['job']
        triage = item['triage']
        opp = item['opportunity_filename']
        dup = job.get('duplicate_count', 1)
        dup_note = f' | alerts={dup}' if dup and dup > 1 else ''
        lines.append(
            f"- **{triage.get('match_score', 0)}** · {triage.get('verdict', 'unknown')} · "
            f"{opportunity_link(opp)}{dup_note}"
        )
    lines.extend(['', '## Unresolved jobs', ''])
    for job in unresolved_jobs:
        lines.append(f"- {job.get('company', '')} | {job.get('title', '')} | {job.get('location', '')}")
    return '\n'.join(lines)


def append_daily_report(path: Path, report_date: str, section: str) -> None:
    if path.exists():
        append_markdown(path, section)
        return
    write_markdown(path, f'# Job Report — {report_date}\n\n{section}')


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description='Score resolved jobs and write Obsidian notes + daily report.')
    parser.add_argument('--input', type=str, default='',
                        help='Path to jobs JSON file (default: jobs_enriched.json from config)')
    parser.add_argument('--run-label', type=str, default='',
                        help='Custom run label (default: current timestamp)')
    cli_args = parser.parse_args()

    env = load_env()
    cfg = load_project_config(env)
    paths = build_paths(env, cfg)
    now = datetime.now()
    report_date = now.strftime('%Y-%m-%d')
    run_label = cli_args.run_label or now.strftime('%Y-%m-%d %H:%M:%S')

    if cli_args.input:
        jobs_path = Path(cli_args.input)
    else:
        jobs_path = jobs_enriched_json_path(env, cfg)
    payload = load_jobs(jobs_path)
    jobs = payload.get('jobs', [])
    resolved_unique = dedupe_resolved_jobs(jobs)
    unresolved = [job for job in jobs if job.get('external_jd_status') not in RESOLVED_STATUSES]

    # Geo gate: skip non-US jobs before any LLM scoring. Defensive — filter_titles.py
    # already drops these for the daily pipeline, but target-scan jobs bypass that.
    location_filter_enabled = bool((cfg.get('location_filter') or {}).get('enabled', True))
    non_us_filtered: list[tuple[dict[str, Any], str]] = []
    city_filtered: list[dict[str, Any]] = []
    if location_filter_enabled:
        kept: list[dict[str, Any]] = []
        for job in resolved_unique:
            loc = job.get('jd_location') or job.get('location') or ''
            is_us, reason = is_us_location(loc, cfg)
            if not is_us:
                non_us_filtered.append((job, reason or 'non-us'))
            elif not location_passes_city_filter(loc, cfg):
                city_filtered.append(job)
            else:
                kept.append(job)
        resolved_unique = kept
        if non_us_filtered:
            print(f'geo filter: skipped {len(non_us_filtered)} non-US jobs', flush=True)
            for job, reason in non_us_filtered[:20]:
                loc = job.get('jd_location') or job.get('location') or ''
                print(f'  - [{reason}] {job.get("company","")} | '
                      f'{job.get("jd_title") or job.get("title","")} | {loc}', flush=True)
        if city_filtered:
            print(f'city filter: skipped {len(city_filtered)} non-local jobs', flush=True)
            for job in city_filtered[:20]:
                loc = job.get('jd_location') or job.get('location') or ''
                print(f'  - {job.get("company","")} | '
                      f'{job.get("jd_title") or job.get("title","")} | {loc}', flush=True)

    # Write location-filtered notes to Filtered/Location/ without LLM scoring
    location_dir = paths['opportunities'] / 'Filtered' / 'Location'
    low_score_dir = paths['opportunities'] / 'Filtered' / 'Low Score'
    low_score_dir.mkdir(parents=True, exist_ok=True)
    if city_filtered:
        location_dir.mkdir(parents=True, exist_ok=True)
    for job in city_filtered:
        opp_name_bare = opportunity_filename(job)
        opp_name = f'00 - {opp_name_bare}'
        existing = _find_existing_opportunity(paths['opportunities'], low_score_dir, opp_name_bare)
        if existing:
            continue  # already have a note for this job
        loc = job.get('jd_location') or job.get('location') or ''
        stub = (f'---\nmatch_score: 0\nverdict: location_filtered\n---\n\n'
                f'# {job.get("company", "")} — {job.get("jd_title") or job.get("title", "")}\n\n'
                f'- **Company:** {job.get("company", "")}\n'
                f'- **Role:** {job.get("jd_title") or job.get("title", "")}\n'
                f'- **Location:** {loc}\n'
                f'- **Filtered reason:** Location outside configured metro\n')
        write_markdown(location_dir / opp_name, stub)

    resume_path = resume_source_path(env, cfg)
    resume_text = read_text_with_icloud_fallback(resume_path)

    min_jd_chars = int(nested_get(cfg, 'scoring', 'min_jd_text_chars', default=200) or 200)
    model = os.environ.get('JOB_TRIAGE_MODEL', '').strip() or None
    triage_timeout = int(os.environ.get('JOB_TRIAGE_TIMEOUT_SECONDS', '120'))

    triage_results: list[dict[str, Any]] = []
    for job in resolved_unique:
        print(f"triaging: {job.get('company', '')} | {job.get('jd_title') or job.get('title', '')}", flush=True)
        triage = triage_job(job, resume_text, model=model, timeout=triage_timeout, min_jd_text_chars=min_jd_chars)
        score = int(triage.get('match_score', 0))
        opp_name_with_score = opportunity_filename(job, score=score)
        opp_name_bare = opportunity_filename(job)

        # Determine destination: score <= 70 → Filtered/Low Score/, else main.
        if score <= 70:
            dest_dir = low_score_dir
        else:
            dest_dir = paths['opportunities']
        opp_path = dest_dir / opp_name_with_score

        # Check for existing file under any name pattern (with or without score prefix)
        existing_path = _find_existing_opportunity(paths['opportunities'], low_score_dir, opp_name_bare)

        review_section = build_opportunity_review_section(job, triage, run_label=run_label)
        if existing_path and existing_path.exists():
            # Update frontmatter with latest score, then append review
            # Ensure iCloud file is materialized before reading
            if "Mobile Documents" in str(existing_path):
                _ensure_icloud_downloaded(existing_path)
            existing = existing_path.read_text(encoding='utf-8')
            updated = update_opportunity_frontmatter(existing, triage)
            _write_with_retry(existing_path, updated)
            append_markdown(existing_path, review_section)
            # Rename/move if score changed or prefix was missing
            if existing_path != opp_path:
                existing_path.rename(opp_path)
                print(f"  moved: {existing_path.name} -> {opp_path.parent.name}/{opp_path.name}", flush=True)
        else:
            note = build_opportunity_note(job, triage) + '\n## Review history\n\n' + review_section
            write_markdown(opp_path, note)
        print(f"wrote opportunity note: {opp_name_with_score}", flush=True)
        triage_results.append({
            'job': job,
            'triage': triage,
            'opportunity_filename': opp_name_with_score,
        })

    daily_report = build_daily_report_section(
        run_label=run_label,
        total_jobs=len(jobs),
        resolved_jobs=resolved_unique,
        unresolved_jobs=unresolved,
        triage_results=triage_results,
    )
    daily_report_path = paths['daily_reports'] / f'{report_date}.md'
    append_daily_report(daily_report_path, report_date, daily_report)

    out_path = project_root(env) / 'data' / 'inbox' / 'jobs_triaged.json'
    triage_payload = {
        'generated_at': datetime.now(UTC).isoformat(),
        'report_date': report_date,
        'run_label': run_label,
        'total_jobs': len(jobs),
        'resolved_unique_jobs': len(resolved_unique),
        'unresolved_jobs': len(unresolved),
        'items': triage_results,
    }
    history_snapshot_path, history_log_path = write_json_snapshot_and_history(env, out_path, triage_payload, history_group='jobs_triaged')
    history_path = project_root(env) / 'data' / 'inbox' / 'jobs_triaged_history.jsonl'
    ensure_parent(history_path)
    append_jsonl(history_path, {
            'generated_at': datetime.now(UTC).isoformat(),
            'report_date': report_date,
            'run_label': run_label,
            'total_jobs': len(jobs),
            'resolved_unique_jobs': len(resolved_unique),
            'unresolved_jobs': len(unresolved),
            'items': triage_results,
        })
    print(f'wrote daily report: {daily_report_path}')
    print(f'wrote triage json: {out_path}')
    print(f'archived triage snapshot: {history_snapshot_path}')
    print(f'appended triage history log: {history_log_path}')
    print(f'appended triage history: {history_path}')

    # Discord notification
    sorted_triage = sorted(triage_results, key=lambda x: x['triage'].get('match_score', 0), reverse=True)
    strong = [r for r in sorted_triage if r['triage'].get('verdict') == 'strong_match']
    medium = [r for r in sorted_triage if r['triage'].get('verdict') == 'medium_match']
    weak = [r for r in sorted_triage if r['triage'].get('verdict') == 'weak_match']

    if not resolved_unique:
        discord_msg = (
            f'📊 **Job Scout {report_date}**\n'
            f'今日无新职位，pipeline 正常运行。'
        )
    else:
        lines = [f'📊 **Job Scout {report_date}** — {len(resolved_unique)} 个职位已评分']
        if strong:
            hits = ', '.join(
                f"{r['job'].get('company','')} {r['job'].get('jd_title') or r['job'].get('title','')} ({r['triage'].get('match_score',0)})"
                for r in strong[:5]
            )
            lines.append(f'🔥 Strong ({len(strong)}): {hits}')
        if medium:
            lines.append(f'✅ Medium: {len(medium)}')
        if weak:
            lines.append(f'❌ Weak: {len(weak)}')
        discord_msg = '\n'.join(lines)

    notify_discord(discord_msg)
    print(f'discord notified: {len(resolved_unique)} jobs, {len(strong)} strong')


if __name__ == '__main__':
    main()
