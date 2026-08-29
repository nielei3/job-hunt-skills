#!/usr/bin/env python3
"""Install a macOS launchd job that runs interview-question-scout on an interval.

Defaults to every 12 hours. Adapted from job-scout/scripts/install_launchd.py.
"""
from __future__ import annotations

import argparse
import plistlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from interview_question_scout_lib import load_env, project_root  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install a macOS launchd job for interview-question-scout."
    )
    parser.add_argument(
        "--every-hours",
        type=int,
        default=12,
        help="interval between runs in hours (default 12)",
    )
    parser.add_argument(
        "--label",
        default="com.example.interview-question-scout.periodic",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="write plist but do not bootstrap it",
    )
    parser.add_argument(
        "--unload",
        action="store_true",
        help="remove the existing launchd job for the given label and exit",
    )
    return parser.parse_args()


def _uid() -> str:
    return subprocess.check_output(["id", "-u"], text=True).strip()


def main() -> None:
    args = parse_args()
    env = load_env()
    root = project_root()
    uid = _uid()
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    log_dir = root / "data" / "logs"
    launch_agents.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    plist_path = launch_agents / f"{args.label}.plist"

    if args.unload:
        if plist_path.exists():
            subprocess.run(
                ["launchctl", "bootout", f"gui/{uid}", str(plist_path)], check=False
            )
            plist_path.unlink()
            print(f"removed {plist_path}")
        else:
            print(f"no plist at {plist_path}")
        return

    runner = root / "scripts" / "run_interview_question_scout.py"
    program_args = [sys.executable, str(runner)]

    interval_seconds = max(1, int(args.every_hours * 3600))

    plist = {
        "Label": args.label,
        "ProgramArguments": program_args,
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "INTERVIEW_SCOUT_ROOT": str(root),
        },
        "StandardOutPath": str(log_dir / "launchd.stdout.log"),
        "StandardErrorPath": str(log_dir / "launchd.stderr.log"),
        "StartInterval": interval_seconds,
        "RunAtLoad": False,
    }

    with plist_path.open("wb") as fh:
        plistlib.dump(plist, fh)

    print(f"wrote {plist_path}")
    if args.print_only:
        print("--print-only set; not bootstrapping")
        return

    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist_path)], check=False)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)], check=True)
    subprocess.run(["launchctl", "enable", f"gui/{uid}/{args.label}"], check=False)
    print(f"loaded {args.label} (every {args.every_hours}h)")


if __name__ == "__main__":
    main()
