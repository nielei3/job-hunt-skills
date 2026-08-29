"""
Multi-source dispatcher.

Reads a company entry from interview-question-scout.yaml, runs every enabled
source for that company, returns a combined `List[Post]`.

YAML schema — **all sources run by default**. The minimum entry is just `name`:

    companies:
      - name: Whatnot
        enabled: true
        obsidian_file: Career/Company/Whatnot/0 interview experience.md
        # Optional: slug for 1p3a / glassdoor manual dir; derived from name if absent
        slug: whatnot

Per-source overrides only needed when defaults are wrong:

    - name: ByteDance
      slug: bytedance
      sources:
        # opt-out: source explicitly disabled
        reddit: { enabled: false }
        # custom search terms (defaults to ["<Name> interview", "<Name> OA"]):
        leetcode: { search_terms: ["ByteDance TikTok interview"] }

CLI:
    python3 scripts/dispatch.py --company Whatnot --since-days 365 --limit 30
    python3 scripts/dispatch.py --company Whatnot --only-source leetcode --verbose
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Make sibling modules importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from interview_question_scout_lib import load_env, project_root  # noqa: E402

try:
    import yaml
except ImportError:
    yaml = None

from sources import SOURCES, Post  # noqa: E402

log = logging.getLogger("dispatch")


def load_config() -> dict[str, Any]:
    if yaml is None:
        raise SystemExit("PyYAML required: pip install pyyaml")
    cfg_path = project_root() / "config" / "interview-question-scout.yaml"
    with cfg_path.open() as f:
        return yaml.safe_load(f)


def normalize_company(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return {source_slug: per_source_config} with **all sources auto-enabled**.

    Defaults are derived from `name` (and `slug` if provided). User overrides
    are applied on top via the optional `sources:` block. To skip a source,
    set `sources: { <slug>: { enabled: false } }`.
    """
    name = entry.get("name") or ""
    slug = entry.get("slug") or _name_to_slug(name)
    user_overrides: dict[str, dict[str, Any]] = dict(entry.get("sources") or {})

    # Auto-defaults: enable every registered source with reasonable params.
    defaults: dict[str, dict[str, Any]] = {
        "1p3a":      {"slug": slug},
        "leetcode":  {"search_terms": [f"{name} interview", f"{name} OA"]},
        "reddit":    {"search_terms": [f"{name} interview", f"{name} OA"]},
        "glassdoor": {"slug": slug},
    }

    out: dict[str, dict[str, Any]] = {}
    for source_slug in SOURCES.keys():
        merged = {**defaults.get(source_slug, {}), **user_overrides.get(source_slug, {})}
        if merged.get("enabled") is False:
            continue
        merged.pop("enabled", None)
        out[source_slug] = merged
    return out


def _name_to_slug(name: str) -> str:
    """Derive a lowercase, hyphenated slug from a display name."""
    import re
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def fetch_for_company(
    entry: dict[str, Any],
    *,
    since_days: int = 365,
    limit_per_source: int = 50,
    only_source: str | None = None,
    verbose: bool = False,
) -> list[Post]:
    env = load_env()
    sources_cfg = normalize_company(entry)
    if only_source:
        sources_cfg = {only_source: sources_cfg.get(only_source, {})} if only_source in sources_cfg else {}
    out: list[Post] = []
    for slug, src_cfg in sources_cfg.items():
        if slug not in SOURCES:
            log.warning("unknown source %r; skipping", slug)
            continue
        src = SOURCES[slug]
        log.info("=== %s | %s (%s) ===", entry.get("name"), src.name, slug)
        try:
            posts = list(src.fetch(
                entry, src_cfg,
                since_days=since_days,
                limit=limit_per_source,
                env=env,
                verbose=verbose,
            ))
            log.info("    %d posts", len(posts))
            out.extend(posts)
        except Exception as exc:
            log.error("    source %s failed: %s", slug, exc)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", required=True, help="company name as in config")
    parser.add_argument("--since-days", type=int, default=365)
    parser.add_argument("--limit-per-source", type=int, default=50)
    parser.add_argument("--only-source", choices=list(SOURCES.keys()))
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--out-json", help="dump combined posts to JSON file")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    cfg = load_config()
    entry = next(
        (c for c in cfg.get("companies", []) if c.get("name") == args.company),
        None,
    )
    if entry is None:
        raise SystemExit(f"company {args.company!r} not in config")

    posts = fetch_for_company(
        entry,
        since_days=args.since_days,
        limit_per_source=args.limit_per_source,
        only_source=args.only_source,
        verbose=args.verbose,
    )
    log.info("TOTAL %d posts across %d sources", len(posts),
             len({p.source for p in posts}))

    if args.out_json:
        Path(args.out_json).write_text(
            json.dumps([p.to_dict() for p in posts], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info("wrote %s", args.out_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
