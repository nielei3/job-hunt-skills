#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from job_scout_lib import (
    jobs_enriched_json_path,
    load_env,
    load_project_config,
    nested_get,
    obsidian_output_base_dir,
    profile_source_path,
    project_root,
    read_json,
    resume_source_path,
    write_json_snapshot_and_history,
)

RESOLVED_STATUSES = {'resolved', 'linkedin_public', 'ats_api', 'user_supplied'}


def run(script: Path) -> None:
    subprocess.run([sys.executable, str(script)], check=True)


def jobs_target_scan_json_path(env: dict, cfg: dict) -> Path:
    root = project_root(env)
    spec = nested_get(cfg, 'jobs_target_scan_json') or {}
    rel_path = spec.get('path') or 'data/inbox/jobs_target_scan.json'
    return root / rel_path


def merge_scan_into_enriched(env: dict, cfg: dict, enriched_path: Path, scan_path: Path) -> int:
    enriched_data = read_json(enriched_path, {'jobs': []})
    scan_data = read_json(scan_path, {'jobs': []})

    existing_keys = {j.get('job_key') for j in enriched_data.get('jobs', []) if j.get('job_key')}
    existing_urls = {j.get('external_jd_url') for j in enriched_data.get('jobs', []) if j.get('external_jd_url')}

    scan_jobs = [
        j for j in scan_data.get('jobs', [])
        if j.get('job_key') not in existing_keys
        and j.get('external_jd_url') not in existing_urls
    ]

    combined = enriched_data.get('jobs', []) + scan_jobs
    merged = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source': 'merged',
        'job_count': len(combined),
        'resolved_count': sum(1 for j in combined if j.get('external_jd_status') in RESOLVED_STATUSES),
        'jobs': combined,
    }
    enriched_path.parent.mkdir(parents=True, exist_ok=True)
    enriched_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False))
    return len(scan_jobs)


def main() -> None:
    env = load_env()
    cfg = load_project_config(env)
    root = project_root(env)
    fetch_script = root / 'scripts' / 'fetch_job_alert_emails.py'
    scan_script = root / 'scripts' / 'scan_target_companies.py'
    enrich_script = root / 'scripts' / 'resolve_external_jd.py'
    jobs_json = jobs_enriched_json_path(env, cfg)
    scan_json = jobs_target_scan_json_path(env, cfg)
    resume_source = resume_source_path(env, cfg)
    profile_source = profile_source_path(env, cfg)
    output_base_dir = obsidian_output_base_dir(env, cfg)

    run(fetch_script)
    run(enrich_script)
    run(scan_script)
    scan_added = merge_scan_into_enriched(env, cfg, jobs_json, scan_json)

    payload = json.loads(jobs_json.read_text()) if jobs_json.exists() else {'jobs': []}
    jobs = payload.get('jobs', [])
    resolved = [j for j in jobs if j.get('external_jd_status') in RESOLVED_STATUSES]
    unresolved = [j for j in jobs if j.get('external_jd_status') not in RESOLVED_STATUSES]

    print('Daily job workflow context:')
    print(f'- Project root: {root}')
    print(f'- Config file: {root / "config" / "job-scout.yaml"}')
    print(f'- Enriched jobs JSON: {jobs_json}')
    print(f'- Resume source: {resume_source}')
    print(f'- Profile source: {profile_source}')
    print(f'- Obsidian output base dir: {output_base_dir}')
    print(f'- Total new jobs: {len(jobs)} (includes {scan_added} from target company scan)')
    print(f'- Jobs with resolved external JD: {len(resolved)}')
    print(f'- Jobs needing manual review: {len(unresolved)}')
    if resolved:
        print('- Resolved jobs:')
        for job in resolved[:20]:
            src = job.get('external_jd_status', '')
            print(f"  - [{src}] {job.get('company','?')} | {job.get('title','?')} | {job.get('external_jd_url','')}")
    if unresolved:
        print('- Unresolved jobs:')
        for job in unresolved[:10]:
            print(f"  - {job.get('company','?')} | {job.get('title','?')} | linkedin={job.get('linkedin_url','')}")
    print('Use the job-scout skill with JOB_SCOUT_ROOT + config/job-scout.yaml to score the resolved jobs and write markdown outputs into Obsidian.')


if __name__ == '__main__':
    main()
