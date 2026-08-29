#!/usr/bin/env python3
"""Walk an archived course directory and produce a top-level README.md TOC.

Reads YAML-ish frontmatter from each section .md and groups by chapter. Emits
Obsidian wikilinks by default; pass --relative-links for plain markdown links.

Usage:
    python3 make_index.py <course-root>
    python3 make_index.py <course-root> --relative-links
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Cheap YAML-ish frontmatter parser. Only handles `key: value` lines."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    out: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        out[key] = value
    return out


SKIP_DIRS = {"images", "assets", "_build"}
SKIP_FILES = {"README.md"}


def collect_sections(root: Path) -> list[dict[str, Any]]:
    """Collect section MDs from either a flat layout (sections in root) or
    a chaptered layout (sections under chapter subdirs). Many courses don't
    have a chapter level — supporting both keeps the skill simple."""
    sections = []
    # Flat: section .md files directly under root
    for md in sorted(root.glob("*.md")):
        if md.name in SKIP_FILES:
            continue
        fm = parse_frontmatter(md.read_text(encoding="utf-8"))
        sections.append({
            "chapter_dir": "",
            "section_file": md.name,
            "rel_path": md.name,
            "title": fm.get("section") or md.stem,
            "chapter": fm.get("chapter") or "Sections",
            "section_idx": _to_int(fm.get("section_idx")),
            "course": fm.get("course"),
            "platform": fm.get("platform"),
            "source_url": fm.get("source_url"),
        })
    # Chaptered: chapter dirs containing section .md files
    for chapter_dir in sorted(p for p in root.iterdir() if p.is_dir() and p.name not in SKIP_DIRS):
        for md in sorted(chapter_dir.glob("*.md")):
            if md.name in SKIP_FILES:
                continue
            fm = parse_frontmatter(md.read_text(encoding="utf-8"))
            sections.append({
                "chapter_dir": chapter_dir.name,
                "section_file": md.name,
                "rel_path": f"{chapter_dir.name}/{md.name}",
                "title": fm.get("section") or md.stem,
                "chapter": fm.get("chapter") or chapter_dir.name,
                "section_idx": _to_int(fm.get("section_idx")),
                "course": fm.get("course"),
                "platform": fm.get("platform"),
                "source_url": fm.get("source_url"),
            })
    sections.sort(key=lambda s: (s["chapter_dir"], s.get("section_idx", 999), s["section_file"]))
    return sections


def _to_int(v: Any) -> int:
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return 999


def render_wikilink(rel_path: str, title: str) -> str:
    target = rel_path.removesuffix(".md")
    return f"[[{target}|{title}]]"


def render_relative_link(rel_path: str, title: str) -> str:
    return f"[{title}]({rel_path})"


def render_chapter_index(chapter: str, items: list[dict[str, Any]],
                         link_fn) -> list[str]:
    lines = [f"### {chapter}"]
    for s in items:
        lines.append(f"- {link_fn(s['rel_path'], s['title'])}")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("course_root", type=Path)
    ap.add_argument("--relative-links", action="store_true",
                    help="emit standard markdown links instead of Obsidian wikilinks")
    ap.add_argument("--toc-url", default="",
                    help="source URL of the course TOC page (for footer/frontmatter)")
    args = ap.parse_args()

    root: Path = args.course_root
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        sys.exit(2)

    sections = collect_sections(root)
    if not sections:
        print(f"no sections found under {root}", file=sys.stderr)
        sys.exit(3)

    course = sections[0].get("course") or root.name
    platform = sections[0].get("platform") or root.parent.name
    source_url = args.toc_url or sections[0].get("source_url") or ""

    progress_jsonl = root / "_progress.jsonl"
    if progress_jsonl.exists():
        # _progress.jsonl is append-only and may have multiple rows per section
        # if the section was re-extracted. The latest row wins.
        latest_by_idx: dict[Any, dict[str, Any]] = {}
        for line in progress_jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            latest_by_idx[r.get("section_idx")] = r
        rows = list(latest_by_idx.values())
        ok = sum(1 for r in rows if r.get("status") == "ok")
        total = len(rows)
    else:
        ok = len(sections)
        total = len(sections)

    today = dt.date.today().isoformat()
    link_fn = render_relative_link if args.relative_links else render_wikilink

    by_chapter: dict[str, list[dict[str, Any]]] = {}
    for s in sections:
        by_chapter.setdefault(s["chapter"], []).append(s)

    lines = [
        "---",
        f"course: {course}",
        f"platform: {platform}",
        f"source_url: {source_url}" if source_url else "",
        f"archived_at: {today}",
        f"sections_total: {total}",
        f"sections_archived: {ok}",
        "---",
        "",
        f"# {course}",
        "",
    ]
    if source_url:
        lines.append(f"> Archived from [{platform}]({source_url}) on {today}.")
    else:
        lines.append(f"> Archived from {platform} on {today}.")
    lines.append("")
    lines.append("## Table of contents")
    lines.append("")
    for chapter, items in by_chapter.items():
        lines.extend(render_chapter_index(chapter, items, link_fn))
        lines.append("")

    readme = "\n".join(line for line in lines if line is not None) + "\n"
    (root / "README.md").write_text(readme, encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "readme": str(root / "README.md"),
        "chapters": len(by_chapter),
        "sections": len(sections),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
