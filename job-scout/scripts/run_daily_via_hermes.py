#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from job_scout_lib import load_env, project_root  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Use Hermes as the top-level controller for the daily job-scout workflow.')
    parser.add_argument('--model', default='', help='Optional Hermes model override')
    parser.add_argument('--include-seen', action='store_true')
    parser.add_argument('--quiet', action='store_true')
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    env = load_env()
    root = project_root(env)

    hermes_bin = shutil.which('hermes') or str(Path.home() / '.local' / 'bin' / 'hermes')
    prompt = (
        'Use the job-scout skill to run the full daily job workflow end to end for this machine. '
        'Operate from JOB_SCOUT_ROOT. '
        'First run the deterministic helper scripts needed to fetch LinkedIn job alert emails, resolve external JD pages, '
        'scan target companies for new matching jobs, '
        'and write triage outputs into Obsidian. '
        'Then return a concise summary including total inbox jobs, resolved JD count, triaged unique roles, '
        'and the strongest matches.'
    )
    if args.include_seen:
        prompt += ' Include already-seen emails when fetching.'

    cmd = [hermes_bin, 'chat', '--skills', 'job-scout', '-q', prompt]
    if args.quiet:
        cmd.insert(2, '-Q')
    if args.model.strip():
        cmd.extend(['-m', args.model.strip()])

    run_env = dict(os.environ)
    run_env['JOB_SCOUT_ROOT'] = str(root)
    subprocess.run(cmd, cwd=root, env=run_env, check=True)


if __name__ == '__main__':
    main()
