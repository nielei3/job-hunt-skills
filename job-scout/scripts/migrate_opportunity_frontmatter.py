#!/usr/bin/env python3
"""One-time annotation: tag legacy Opportunity notes with triage_mode."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def migrate_dir(directory: Path, *, default_mode: str = 'metadata_only') -> int:
    changed = 0
    for p in sorted(directory.rglob('*.md')):
        text = p.read_text()
        if not text.startswith('---\n'):
            continue
        try:
            end = text.index('---\n', 4) + 4
        except ValueError:
            continue
        fm = text[:end]
        body = text[end:]
        if re.search(r'^\s*triage_mode:\s*\w+', fm, re.MULTILINE):
            continue
        new_fm = fm.rstrip().rstrip('---').rstrip() + f'\ntriage_mode: {default_mode}\n---\n'
        p.write_text(new_fm + body)
        changed += 1
    return changed


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('directory', type=str)
    p.add_argument('--mode', default='metadata_only')
    args = p.parse_args()
    n = migrate_dir(Path(args.directory).expanduser(), default_mode=args.mode)
    print(f'updated {n} note(s) in {args.directory}')


if __name__ == '__main__':
    main()
