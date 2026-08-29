#!/usr/bin/env python3
from __future__ import annotations

import argparse
import plistlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from job_scout_lib import load_env, project_root  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Install a macOS launchd job for the daily job-scout runner.')
    parser.add_argument('--hour', type=int, default=8)
    parser.add_argument('--minute', type=int, default=30)
    parser.add_argument('--label', default='com.example.job-scout.daily')
    parser.add_argument('--include-seen', action='store_true')
    parser.add_argument('--triage-model', default='')
    parser.add_argument('--print-only', action='store_true', help='Write plist but do not bootstrap it')
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    env = load_env()
    root = project_root(env)
    uid = subprocess.check_output(['id', '-u'], text=True).strip()
    launch_agents = Path.home() / 'Library' / 'LaunchAgents'
    log_dir = root / 'data' / 'logs'
    launch_agents.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    runner = root / 'scripts' / 'run_daily_via_hermes.py'
    plist_path = launch_agents / f'{args.label}.plist'
    program_args = [sys.executable, str(runner)]
    if args.include_seen:
        program_args.append('--include-seen')
    if args.triage_model.strip():
        program_args.extend(['--model', args.triage_model.strip()])

    plist = {
        'Label': args.label,
        'ProgramArguments': program_args,
        'WorkingDirectory': str(root),
        'EnvironmentVariables': {
            'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
            'JOB_SCOUT_ROOT': str(root),
        },
        'StandardOutPath': str(log_dir / 'launchd.stdout.log'),
        'StandardErrorPath': str(log_dir / 'launchd.stderr.log'),
        'StartCalendarInterval': {
            'Hour': args.hour,
            'Minute': args.minute,
        },
        'RunAtLoad': False,
    }

    with plist_path.open('wb') as fh:
        plistlib.dump(plist, fh)

    print(f'wrote {plist_path}')
    if args.print_only:
        return

    subprocess.run(['launchctl', 'bootout', f'gui/{uid}', str(plist_path)], check=False)
    subprocess.run(['launchctl', 'bootstrap', f'gui/{uid}', str(plist_path)], check=True)
    subprocess.run(['launchctl', 'enable', f'gui/{uid}/{args.label}'], check=False)
    print(f'loaded {args.label}')


if __name__ == '__main__':
    main()
