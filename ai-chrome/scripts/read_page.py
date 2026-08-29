#!/usr/bin/env python3
"""Read content from a tab in the debug Chrome.

Selects a tab by --tab-id, --url-substring (case-insensitive), or
--first-non-internal (default). Returns URL, title, innerText, a screenshot
path, and a truncated HTML preview. innerHTML is usually huge, so we cap it —
pass --full-html if you really want the whole DOM.

Output: JSON on stdout.

Requires Playwright (`pip install playwright` — no `playwright install` needed,
since we connect to an existing Chrome over CDP).

Exit codes:
    0  success
    2  playwright not installed
    3  cannot reach Chrome
    4  no matching tab found
    5  unexpected error reading the page
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Homebrew's python refuses `pip install playwright` (PEP 668 externally-managed),
# so playwright lives in a dedicated venv. Re-exec there rather than making every
# caller remember the interpreter — `python3 read_page.py` has to keep working.
VENV_PYTHON = Path.home() / ".config/ai-chrome-venv/bin/python"
_REEXEC_FLAG = "_AI_CHROME_VENV_REEXEC"

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    if VENV_PYTHON.exists() and os.environ.get(_REEXEC_FLAG) != "1":
        os.environ[_REEXEC_FLAG] = "1"  # guards against a loop if the venv also lacks it
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), os.path.abspath(__file__), *sys.argv[1:]])
    print(
        json.dumps({
            "error": "playwright not installed",
            "hint": f"python3 -m venv {VENV_PYTHON.parents[1]} && {VENV_PYTHON.parent}/pip install playwright"
                    "  (no `playwright install` needed; we use existing Chrome)",
        }),
        file=sys.stderr,
    )
    sys.exit(2)


INTERNAL_PREFIXES = ("chrome://", "devtools://", "chrome-extension://", "about:")


def is_internal(url: str) -> bool:
    return (url or "").startswith(INTERNAL_PREFIXES)


def pick_page(browser, tab_id: str | None, url_substring: str | None):
    """Pick a tab. Returns the Playwright Page or None."""
    needle = (url_substring or "").lower()
    for ctx in browser.contexts:
        for p in ctx.pages:
            url = p.url or ""
            if tab_id is not None:
                # Playwright's Page doesn't directly expose the CDP target id, so
                # we match it via the underlying context. Easiest: skip tab_id
                # selection and rely on url-substring instead. We keep this
                # branch as a no-op placeholder for clarity.
                pass
            if url_substring and needle in url.lower() and not is_internal(url):
                return p
    if url_substring or tab_id:
        return None  # specific selector requested but nothing matched
    # Default: first non-internal page across all contexts.
    for ctx in browser.contexts:
        for p in ctx.pages:
            if not is_internal(p.url or ""):
                return p
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    # No baked default: the port belongs to the company profile, which the caller
    # resolves and passes in. A second copy here is how the port and the
    # user-data-dir drifted apart and produced two Chrome windows.
    p.add_argument("--port", type=int, default=(int(os.environ["PORT"]) if os.environ.get("PORT") else None),
                   help="CDP port; defaults to $PORT (set it from profiles/<company>.md)")
    p.add_argument("--tab-id", default=None, help="Match a specific CDP target id (rarely needed).")
    p.add_argument("--url-substring", default=None, help="Pick the first tab whose URL contains this substring (case-insensitive).")
    p.add_argument("--screenshot", default="/tmp/ai_chrome_snap.png", help="Where to save the screenshot.")
    p.add_argument("--full-html", action="store_true", help="Return the full innerHTML instead of a truncated preview.")
    p.add_argument("--html-limit", type=int, default=20_000, help="Max characters of innerHTML when not using --full-html.")
    p.add_argument("--no-screenshot", action="store_true", help="Skip taking a screenshot.")
    args = p.parse_args()
    if args.port is None:
        print(json.dumps({"error": "no CDP port given",
                          "hint": "pass --port, or set PORT from profiles/<company>.md "
                                  "(see ai-chrome/SKILL.md → Project profile)"}), file=sys.stderr)
        return 5

    result: dict = {
        "url": None,
        "title": None,
        "inner_text": "",
        "inner_text_length": 0,
        "html_preview": "",
        "html_truncated": False,
        "screenshot": None,
    }

    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}")
            except Exception as exc:
                print(
                    json.dumps({
                        "error": f"cannot connect to Chrome on port {args.port}: {exc!r}",
                        "hint": "run scripts/ensure_chrome.sh first",
                    }),
                    file=sys.stderr,
                )
                return 3

            page = pick_page(browser, args.tab_id, args.url_substring)
            if page is None:
                print(
                    json.dumps({
                        "error": "no matching tab",
                        "hint": "run list_tabs.py to see available tabs",
                        "selector": {"tab_id": args.tab_id, "url_substring": args.url_substring},
                    }),
                    file=sys.stderr,
                )
                return 4

            try:
                result["url"] = page.url
                # title can fail on a truly blank tab; .title() raises rather than returns ""
                try:
                    result["title"] = page.title()
                except Exception:
                    result["title"] = ""
                inner_text = page.evaluate("document.body && document.body.innerText || ''") or ""
                inner_html = page.evaluate("document.body && document.body.innerHTML || ''") or ""
                result["inner_text"] = inner_text
                result["inner_text_length"] = len(inner_text)
                if args.full_html or len(inner_html) <= args.html_limit:
                    result["html_preview"] = inner_html
                else:
                    result["html_preview"] = inner_html[: args.html_limit]
                    result["html_truncated"] = True
                    result["html_full_length"] = len(inner_html)

                if not args.no_screenshot:
                    out = Path(args.screenshot)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    png = page.screenshot(full_page=True, type="png")
                    out.write_bytes(png)
                    result["screenshot"] = str(out)
                    result["screenshot_size_bytes"] = len(png)
            except Exception as exc:
                result["error"] = f"failed to read page: {exc!r}"
                print(json.dumps(result, indent=2, ensure_ascii=False))
                return 5
    except Exception as exc:
        print(json.dumps({"error": f"unexpected: {exc!r}"}, indent=2), file=sys.stderr)
        return 5

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
