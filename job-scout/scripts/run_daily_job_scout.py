#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from job_scout_lib import (  # noqa: E402
    jobs_enriched_json_path,
    jobs_filtered_json_path,
    jobs_inbox_json_path,
    load_env,
    load_project_config,
    obsidian_output_base_dir,
    project_root,
    read_json,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run the daily job-scout pipeline end to end.')
    parser.add_argument('--include-seen', action='store_true', help='Pass --include-seen to fetch_job_alert_emails.py')
    parser.add_argument('--skip-fetch', action='store_true')
    parser.add_argument('--skip-resolve', action='store_true')
    parser.add_argument('--skip-triage', action='store_true')
    parser.add_argument('--triage-model', default='', help='Optional JOB_TRIAGE_MODEL override')
    parser.add_argument('--resolve-limit', type=int, default=0, help='Optional --limit for resolve step')
    parser.add_argument('--resolve-offset', type=int, default=0, help='Optional --offset for resolve step')
    return parser.parse_args()


def run_step(cmd: list[str], *, extra_env: dict[str, str] | None = None) -> None:
    print(f'==> {" ".join(cmd)}', flush=True)
    env = None
    if extra_env:
        env = dict(**extra_env)
    subprocess.run(cmd, check=True, env=env)


def main() -> None:
    args = parse_args()
    env = load_env()
    cfg = load_project_config(env)
    root = project_root(env)

    fetch_script = root / 'scripts' / 'fetch_job_alert_emails.py'
    filter_script = root / 'scripts' / 'filter_titles.py'
    resolve_script = root / 'scripts' / 'resolve_external_jd.py'
    retriage_script = root / 'scripts' / 'retriage.py'

    base_env = dict(**env)
    if args.triage_model.strip():
        base_env['JOB_TRIAGE_MODEL'] = args.triage_model.strip()

    if not args.skip_fetch:
        cmd = [sys.executable, str(fetch_script)]
        if args.include_seen:
            cmd.append('--include-seen')
        run_step(cmd, extra_env=base_env)

    # New: title-level filter between fetch and resolve.
    run_step([sys.executable, str(filter_script)], extra_env=base_env)

    # Resolve only operates on jobs that passed the filter.
    if not args.skip_resolve:
        cmd = [sys.executable, str(resolve_script)]
        if args.resolve_offset:
            cmd.extend(['--offset', str(args.resolve_offset)])
        if args.resolve_limit:
            cmd.extend(['--limit', str(args.resolve_limit)])
        cmd.extend(['--input', str(_filtered_passes_path(env, cfg, root))])
        run_step(cmd, extra_env=base_env)

    # Sync unresolved into Pending JDs.md and run triage on filled JDs.
    if not args.skip_triage:
        run_step([sys.executable, str(retriage_script)], extra_env=base_env)

    print('\nDaily pipeline summary:')
    print(f'- Project root: {root}')


def _filtered_passes_path(env: dict[str, str], cfg: dict[str, Any], root: Path) -> Path:
    """Materialize a jobs_today-shaped file containing only passes_rules entries.

    The resolver expects a file with a top-level 'jobs' list. We slice
    jobs_filtered.json and write it to data/inbox/jobs_to_resolve.json.
    """
    src = jobs_filtered_json_path(env, cfg)
    out = root / 'data' / 'inbox' / 'jobs_to_resolve.json'
    payload = read_json(src, {'jobs': []})
    passes = [j for j in payload.get('jobs', []) if j.get('filter_status') == 'passes_rules']
    write_json(out, {
        'generated_at': payload.get('generated_at', ''),
        'source': 'jobs_filtered_passes',
        'job_count': len(passes),
        'jobs': passes,
    })
    return out


if __name__ == '__main__':
    main()
