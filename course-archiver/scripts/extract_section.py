#!/usr/bin/env python3
"""Turn one course-page (already loaded in Chrome) into one Markdown file.

Pipeline:
1. Read the page via ai-chrome's read_page.py (full HTML).
2. Find the article body within inner_text — most course platforms wrap their
   content in a frame whose nav/sidebar text bleeds into the inner_text dump.
   We use a per-platform `body_marker` regex to locate the article boundary.
3. Detect headings, code blocks, images, and tab-separated tables from the
   HTML, and inject Markdown for them at the right line in the body text.
4. Download every content image via fetch_resource.py (which inherits the
   browser's auth) and rewrite refs to relative `images/<file>` paths.
5. Write `<NN-slug>.md` with Obsidian-style frontmatter.

The default extraction patterns are tuned to Next.js course platforms
(ByteByteGo, ngrok-style edu sites, some Vercel-hosted courses). For other
platforms, override `--body-start-regex` and `--body-end-substring`. If your
platform doesn't proxy images through `_next/image`, the script still picks
them up via plain `<img src>` matching.

Exit codes: 0 ok, 2 bad args, 3 read failed, 4 no content found.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse


SCRIPT_DIR = Path(__file__).resolve().parent
# ai-chrome ships alongside this skill in the same repo. Override with
# AI_CHROME_ROOT if you keep it elsewhere.
AI_CHROME_ROOT = Path(os.environ.get("AI_CHROME_ROOT") or SCRIPT_DIR.parents[1] / "ai-chrome")
AI_CHROME_READ = str(AI_CHROME_ROOT / "scripts" / "read_page.py")
FETCH_RESOURCE = SCRIPT_DIR / "fetch_resource.py"


def fail(code: int, msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(code)


def slugify(text: str, max_len: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    s = re.sub(r"[-\s]+", "-", s)
    return s[:max_len].strip("-")


def short_alt(alt: str, max_len: int = 60) -> str:
    """Trim image descriptions to a manageable length: prefer the first
    sentence, fall back to a hard char cap. Keeps Markdown readable; the
    full source is still in the page DOM if anyone needs it."""
    if not alt:
        return ""
    alt = re.sub(r"\s+", " ", alt).strip()
    sentence_end = re.search(r"[.!?]\s", alt)
    if sentence_end and sentence_end.end() <= max_len + 20:
        first = alt[: sentence_end.start() + 1].strip()
        if 8 <= len(first) <= max_len + 20:
            return first
    if len(alt) <= max_len:
        return alt
    cut = alt[:max_len].rsplit(" ", 1)[0]
    return cut + "…"


def collect_images(html: str, page_origin: str) -> list[dict[str, str]]:
    """Extract content images, decoding Next.js image-proxy URLs to their
    canonical `/images/...` paths. Skips logos, avatars, and icons."""
    imgs: list[dict[str, str]] = []
    seen: set[str] = set()
    for tag in re.findall(r"<img\s+([^>]+?)/?>", html, flags=re.IGNORECASE):
        src = re.search(r'\bsrc="([^"]*)"', tag, flags=re.IGNORECASE)
        alt = re.search(r'\balt="([^"]*)"', tag, flags=re.IGNORECASE)
        if not src:
            continue
        src_v = src.group(1)
        m = re.search(r"/_next/image\?url=([^&]+)", src_v)
        clean = unquote(m.group(1)) if m else src_v
        if "/images/courses/" not in clean and "/images/lessons/" not in clean and "course" not in clean.lower():
            # Skip non-content images (logos, avatars, etc.)
            continue
        if clean in seen:
            continue
        seen.add(clean)
        full = (page_origin.rstrip("/") + clean) if clean.startswith("/") else clean
        imgs.append({"url": full, "path": clean, "alt": alt.group(1) if alt else ""})
    return imgs


def collect_headings(html: str) -> dict[str, int]:
    """Map heading text → level for h2..h6 in the page. We skip h1 because
    the section title gets emitted explicitly. Rendered text outside the
    article (sidebar, footer) shares a tag pool, so we keep the *last* level
    seen for any duplicate text — body headings come after the sidebar in
    serialized DOM, so this lets the body win in the rare collision case."""
    out: dict[str, int] = {}
    for m in re.finditer(r"<h([2-6])[^>]*>([^<]+)</h\1>", html, flags=re.IGNORECASE):
        out[m.group(2).strip()] = int(m.group(1))
    return out


def collect_code_blocks(html: str) -> list[str]:
    blocks: list[str] = []
    for m in re.finditer(r"<pre[^>]*>(.*?)</pre>", html, flags=re.DOTALL | re.IGNORECASE):
        inner = m.group(1)
        code = re.sub(r"<[^>]+>", "", inner)
        for ent, ch in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                        ("&#39;", "'"), ("&nbsp;", " "), ("&amp;", "&")):
            code = code.replace(ent, ch)
        blocks.append(code.strip("\n"))
    return blocks


def guess_lang(code: str) -> str:
    s = code.strip()
    if s.startswith("{") and '"' in s and ":" in s:
        return "json"
    if "def " in s or "import " in s or "print(" in s:
        return "python"
    if "function " in s or "const " in s or "=>" in s:
        return "javascript"
    if "public class" in s or "System.out" in s:
        return "java"
    if "#include" in s:
        return "cpp"
    return ""


def is_table_row(line: str) -> bool:
    """A tab-separated line is a table row if it has at least 2 tab chars
    AND every field is short (looks like cell content, not prose)."""
    if line.count("\t") < 2:
        return False
    fields = line.split("\t")
    return all(len(f) <= 80 for f in fields)


def md_table_from_rows(rows: list[str]) -> list[str]:
    """Render a contiguous block of tab-separated rows as a Markdown table.
    The first row is treated as a header. We pad short rows to match the
    widest one — some pages render irregular tables and dropping cells is
    worse than leaving blanks."""
    cols = [r.split("\t") for r in rows]
    width = max(len(c) for c in cols)
    cols = [c + [""] * (width - len(c)) for c in cols]
    out = [
        "| " + " | ".join(cols[0]) + " |",
        "|" + "|".join("---" for _ in range(width)) + "|",
    ]
    for c in cols[1:]:
        out.append("| " + " | ".join(c) + " |")
    return out


def find_body(text: str, body_start_regex: Optional[str], body_end_substr: Optional[str]) -> str:
    body = text
    if body_start_regex:
        m = re.search(body_start_regex, text)
        if m:
            body = text[m.end():]
    if body_end_substr:
        i = body.find(body_end_substr)
        if i > 0:
            body = body[:i]
    return body.strip("\n")


def _emit_image_ref(alt: str, fname: str, image_prefix: str) -> str:
    return f"![{alt}]({image_prefix}{fname})"


def assemble_markdown(
    body: str,
    headings: dict[str, int],
    code_blocks: list[str],
    images: list[dict[str, Any]],
    image_filenames: list[str],
    figure_marker_regex: str,
    drop_section_number_line: bool,
    image_prefix: str = "images/",
) -> tuple[str, str, dict[str, int]]:
    """Returns (title, md_body, stats)."""
    lines = body.splitlines()
    if drop_section_number_line and lines and re.match(r"^\d+$", lines[0].strip()):
        lines = lines[1:]
    title = lines[0].strip() if lines else "Untitled"
    out = [f"# {title}", ""]

    fig_re = re.compile(figure_marker_regex)
    img_by_idx = {i + 1: (img, fname) for i, (img, fname) in enumerate(zip(images, image_filenames))}
    used_codes: set[int] = set()
    # Each figure number can be used as an image marker exactly once. Later
    # occurrences of "Figure N" in the body (prose like "as we saw in
    # Figure 5, ...") are references, not markers — we keep them as plain text.
    used_figures: set[int] = set()
    stats = {"images": 0, "code_blocks": 0, "tables": 0, "h2": 0, "h3": 0}

    i = 1  # skip the title line we already emitted
    while i < len(lines):
        line = lines[i]
        s = line.strip()

        # Image marker: "Figure N" alone, or "Figure N <caption>" (some platforms inline the caption).
        # Only emit on the *first* line that starts with "Figure N" — subsequent occurrences
        # are prose references, not new image insertions.
        fm = fig_re.match(s)
        if fm and int(fm.group(1)) not in used_figures:
            idx = int(fm.group(1))
            used_figures.add(idx)
            inline_caption = (fm.group(2) if fm.lastindex and fm.lastindex >= 2 else "") or ""
            inline_caption = inline_caption.strip(" .—-:")
            payload = img_by_idx.get(idx)
            if payload:
                img, fname = payload
                # Prefer the inline caption from the body (concise, page-author-written)
                # over the long DOM alt text. Fall back to a trimmed DOM alt.
                if inline_caption and len(inline_caption) > 4:
                    alt = inline_caption[:120]
                else:
                    alt = short_alt(img.get("alt", "") or f"Figure {idx}")
                out.append("")
                out.append(f"![{alt}]({image_prefix}{fname})")
                out.append("")
                stats["images"] += 1
            else:
                out.append(f"> *Figure {idx} — image not captured*")
            i += 1
            continue

        # Heading
        if s in headings:
            level = headings[s]
            out.append("")
            out.append(f"{'#' * level} {s}")
            out.append("")
            stats[f"h{level}"] = stats.get(f"h{level}", 0) + 1
            i += 1
            continue

        # Code block (matches the first non-empty line of any unused block)
        matched = -1
        for j, code in enumerate(code_blocks):
            if j in used_codes:
                continue
            first = next((cl for cl in code.splitlines() if cl.strip()), "")
            if first and s == first.strip():
                matched = j
                break
        if matched >= 0:
            code = code_blocks[matched]
            used_codes.add(matched)
            lang = guess_lang(code)
            out.append("")
            out.append(f"```{lang}")
            out.extend(code.splitlines())
            out.append("```")
            out.append("")
            stats["code_blocks"] += 1
            # Skip body lines that match the code block lines, in order
            code_lines = code.splitlines()
            skip = 0
            for cl in code_lines:
                if i + skip < len(lines) and lines[i + skip].strip() == cl.strip():
                    skip += 1
                else:
                    break
            i += max(skip, 1)
            continue

        # Table block (consecutive tab-separated rows)
        if is_table_row(line):
            run: list[str] = []
            j = i
            while j < len(lines) and is_table_row(lines[j]):
                run.append(lines[j])
                j += 1
            if len(run) >= 2:
                out.append("")
                out.extend(md_table_from_rows(run))
                out.append("")
                stats["tables"] += 1
                i = j
                continue

        out.append(line)
        i += 1

    md = "\n".join(out)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return title, md, stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--course-root", required=True,
                    help="path to <platform>/<course> output directory")
    ap.add_argument("--section-idx", type=int, required=True)
    ap.add_argument("--section-url", required=True,
                    help="exact URL of the section (used for frontmatter)")
    ap.add_argument("--course-meta", required=True,
                    help="path to _toc.json with `course` and `platform` fields")
    ap.add_argument("--url-substring",
                    help="ai-chrome read_page selector; defaults to a host-derived substring")
    ap.add_argument("--body-start-regex",
                    default=r"\nThe Learning Continues\nMy Courses\n",
                    help="regex marking the *end* of nav junk; the body starts after the match. "
                         "Default targets ByteByteGo's TOC sentinel.")
    ap.add_argument("--body-end-substring", default="Mark as Complete",
                    help="substring marking the start of the page footer; the body ends there. "
                         "Default targets ByteByteGo's lesson-footer button.")
    ap.add_argument("--figure-regex", default=r"^Figure\s+(\d+)\b\s*(.*)$",
                    help="regex matching the figure marker line in body text. "
                         "Group 1 = figure number (required); Group 2 = inline caption "
                         "(optional). The default handles both 'Figure 3' alone and "
                         "'Figure 3 Caption text here' that some platforms inline.")
    ap.add_argument("--no-images", action="store_true",
                    help="skip image downloads; reference original URLs instead")
    ap.add_argument("--drop-section-number-line", action="store_true", default=True,
                    help="if the first body line is just a number (e.g. '02'), drop it. Default on.")
    ap.add_argument("--chapter-dir",
                    help="optional chapter subdirectory under --course-root, e.g. '01-in-a-hurry'. "
                         "If set, the MD is written to <course-root>/<chapter-dir>/, image refs become "
                         "../images/<file>, and frontmatter includes the chapter title. Required for "
                         "courses that have a meaningful chapter/section grouping (HelloInterview's "
                         "5 sidebar groups, Coursera weeks, etc.). Without it, output is flat.")
    ap.add_argument("--chapter",
                    help="chapter title for frontmatter; defaults to --chapter-dir if not given.")
    ap.add_argument("--in-chapter-idx", type=int,
                    help="1-based index within the chapter. Defaults to --section-idx (global). "
                         "Use this to renumber per-chapter so each chapter's first lesson is 01.")
    args = ap.parse_args()

    meta = json.loads(Path(args.course_meta).read_text(encoding="utf-8"))
    course = meta.get("course") or meta.get("course_title") or "Unknown Course"
    platform = meta.get("platform") or "Unknown"
    host = meta.get("host") or urlparse(args.section_url).netloc
    page_origin = f"https://{host}"

    # 1. Read page
    url_substr = args.url_substring or args.section_url.split("/")[-1].split("?")[0]
    r = subprocess.run([
        "python3", AI_CHROME_READ,
        "--url-substring", url_substr,
        "--full-html", "--no-screenshot",
    ], capture_output=True, text=True)
    if r.returncode != 0:
        fail(3, f"read_page.py failed: {r.stderr[-500:]}")
    try:
        page = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        fail(3, f"could not parse read_page.py JSON: {e}\nstdout head: {r.stdout[:500]}")

    text = page["inner_text"]
    html = page["html_preview"]

    # 2. Find body
    body = find_body(text, args.body_start_regex, args.body_end_substring)
    if not body or len(body.splitlines()) < 3:
        fail(4, f"body looked empty after trimming. Try a different --body-start-regex.\n"
                f"first 200 chars of inner_text: {text[:200]!r}")

    # 3. Collect HTML structure
    headings = collect_headings(html)
    code_blocks = collect_code_blocks(html)
    images = collect_images(html, page_origin)

    # 4. Plan filenames + download
    # Prefer the section title from TOC metadata over the body's first line,
    # which may be polluted by sidebar text (e.g. "My Courses" on ByteByteGo).
    toc_section_title = None
    for s in meta.get("sections", []):
        if s.get("section_idx") == args.section_idx:
            toc_section_title = s.get("section")
            break

    section_lines = body.splitlines()
    if toc_section_title:
        title_line = toc_section_title
    elif section_lines and re.match(r"^\d+$", section_lines[0].strip()):
        title_line = section_lines[1] if len(section_lines) > 1 else ""
    else:
        title_line = section_lines[0] if section_lines else ""
    section_slug = slugify(title_line) or f"section-{args.section_idx:02d}"
    image_filenames = []
    for i, img in enumerate(images, start=1):
        ext = os.path.splitext(img["path"])[1] or ".png"
        image_filenames.append(f"{args.section_idx:02d}-{section_slug[:40]}-fig{i}{ext}")

    course_root = Path(args.course_root).expanduser()
    images_dir = course_root / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    download_failures: list[str] = []
    if not args.no_images:
        for img, fname in zip(images, image_filenames):
            r = subprocess.run([
                "python3", str(FETCH_RESOURCE),
                img["url"], "--out", str(images_dir / fname),
                "--referer", args.section_url,
            ], capture_output=True, text=True)
            if r.returncode != 0:
                download_failures.append(f"{fname}: {r.stderr[-150:]}")

    # 5. Assemble MD
    image_prefix = "../images/" if args.chapter_dir else "images/"
    title, md_body, stats = assemble_markdown(
        body, headings, code_blocks, images, image_filenames,
        args.figure_regex, args.drop_section_number_line,
        image_prefix=image_prefix,
    )
    fm_lines = [
        "---",
        f"course: {course}",
        f"platform: {platform}",
    ]
    if args.chapter or args.chapter_dir:
        fm_lines.append(f"chapter: {args.chapter or args.chapter_dir}")
    fm_lines.extend([
        f"section: {title}",
        f"section_idx: {args.section_idx}",
        f"source_url: {args.section_url}",
        "archived_at: " + date.today().isoformat(),
        f"images: {stats['images']}",
        "---",
        "",
    ])
    frontmatter = "\n".join(fm_lines)

    # Output path: <course-root>/[<chapter-dir>/]<idx>-<slug>.md
    if args.chapter_dir:
        out_dir = course_root / args.chapter_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        idx_for_filename = args.in_chapter_idx if args.in_chapter_idx is not None else args.section_idx
    else:
        out_dir = course_root
        idx_for_filename = args.section_idx
    md_path = out_dir / f"{idx_for_filename:02d}-{section_slug}.md"

    # Atomic write via .tmp + os.replace. Prevents iCloud Drive from creating
    # ' 2.md' conflict copies when retrying a section: if the destination
    # already exists from an earlier partial run, os.replace overwrites it
    # in one filesystem op rather than letting iCloud see two writers race.
    tmp_path = md_path.with_suffix(md_path.suffix + ".tmp")
    tmp_path.write_text(frontmatter + md_body + "\n", encoding="utf-8")
    os.replace(tmp_path, md_path)

    progress_line = json.dumps({
        "section_idx": args.section_idx,
        "url": args.section_url,
        "status": "ok" if not download_failures else "partial",
        "chars": len(md_body),
        "stats": stats,
        "image_failures": download_failures,
    }, ensure_ascii=False)
    with (course_root / "_progress.jsonl").open("a", encoding="utf-8") as f:
        f.write(progress_line + "\n")

    result = {
        "ok": True,
        "path": str(md_path),
        "title": title,
        "chars": len(md_body),
        "stats": stats,
        "image_failures": download_failures,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
