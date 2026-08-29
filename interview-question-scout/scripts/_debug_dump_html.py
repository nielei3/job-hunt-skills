#!/usr/bin/env python3
"""Connect to ai-chrome via CDP and dump a URL's rendered HTML.

Usage:
    python3 scripts/_debug_dump_html.py https://jobs.1point3acres.com/companies/openai/interview
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

CDP_URL = "http://localhost:9222"


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: _debug_dump_html.py <url> [out_path]", file=sys.stderr)
        sys.exit(2)
    url = sys.argv[1]
    out_path = Path(sys.argv[2]) if len(sys.argv) >= 3 else Path("/tmp/_debug_dump.html")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0]
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # Site has many ad/analytics requests → networkidle rarely fires.
            # Give JS a few seconds to render, then take snapshot.
            time.sleep(4)
            html = page.content()
            title = page.title()
            cur_url = page.url
            out_path.write_text(html, encoding="utf-8")
            print(f"title: {title}")
            print(f"final_url: {cur_url}")
            print(f"bytes: {len(html)}")
            print(f"wrote: {out_path}")
        finally:
            page.close()


if __name__ == "__main__":
    main()
