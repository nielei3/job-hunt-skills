#!/usr/bin/env python3
"""Navigate the user's existing Chrome (over CDP) to a URL and wait for load.

Mirrors the conventions of ai-chrome's read_page.py:
- Connects to ws://localhost:9222 via Playwright over CDP.
- Picks a tab by --url-substring or --tab-id (or first non-internal tab).
- Drives that tab's `Page.navigate` and waits for `load` + `networkidle`.
- Exits 0 on success, 3 if Chrome is unreachable, 4 if no matching tab.

Read-only-ish: this only changes the URL of the chosen tab. No clicks, no typing.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Optional


CDP_URL = "http://localhost:9222"


def fail(code: int, msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(code)


def pick_target(playwright_browser, *, url_substring: Optional[str], tab_id: Optional[str]):
    contexts = playwright_browser.contexts
    pages = [p for ctx in contexts for p in ctx.pages]
    real = [p for p in pages if not p.url.startswith(("chrome://", "devtools://", "chrome-extension://"))]
    if tab_id is not None:
        for p in pages:
            if getattr(p, "_target_id", None) == tab_id or p.url.endswith(tab_id):
                return p
        fail(4, f"no tab matching tab-id={tab_id!r}")
    if url_substring is not None:
        needle = url_substring.lower()
        for p in real:
            if needle in p.url.lower() or needle in (p.title() or "").lower():
                return p
        fail(4, f"no tab matching url-substring={url_substring!r}")
    if not real:
        fail(4, "no real tabs open in Chrome")
    return real[0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url", help="URL to navigate to")
    ap.add_argument("--url-substring", help="pick the tab whose URL or title contains this")
    ap.add_argument("--tab-id", help="pick a specific CDP target id")
    ap.add_argument("--wait", default="load",
                    choices=["load", "domcontentloaded", "networkidle"],
                    help="what to wait for after navigation (default: load). "
                         "networkidle rarely fires on modern SPAs (long-polling, beacons, "
                         "lazy fonts) — only use it for static sites.")
    ap.add_argument("--timeout-ms", type=int, default=45000,
                    help="hard timeout in ms (default: 45000)")
    ap.add_argument("--settle-ms", type=int, default=1500,
                    help="extra sleep after wait condition; lets SPA route effects "
                         "render the new section's content (default: 1500)")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        fail(2, "playwright not installed. Run: pip install playwright")

    started = time.monotonic()
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            fail(3, f"cannot reach Chrome at {CDP_URL}: {e}\n"
                    f"Run ai-chrome's ensure_chrome.sh first.")

        page = pick_target(browser, url_substring=args.url_substring, tab_id=args.tab_id)
        original_url = page.url
        wait_warning: Optional[str] = None
        try:
            page.goto(args.url, wait_until=args.wait, timeout=args.timeout_ms)
        except Exception as e:
            # Playwright raises TimeoutError when the *wait condition* isn't met,
            # but the navigation itself usually succeeds first. Treat that case
            # as a soft warning — the URL change is what matters, and the user
            # often has the new content rendered already.
            if args.url.split("#", 1)[0].rstrip("/") == page.url.split("#", 1)[0].rstrip("/"):
                wait_warning = f"{type(e).__name__}: wait condition not met but URL is correct"
            else:
                fail(5, f"navigation to {args.url!r} failed: {e}")

        if args.settle_ms > 0:
            time.sleep(args.settle_ms / 1000.0)

        result = {
            "ok": True,
            "from": original_url,
            "to": page.url,
            "title": page.title(),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
        if wait_warning:
            result["warning"] = wait_warning
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
