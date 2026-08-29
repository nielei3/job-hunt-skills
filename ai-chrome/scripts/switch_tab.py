#!/usr/bin/env python3
"""Bring a tab to the foreground in the debug Chrome.

Selects a tab by --tab-id or --url-substring (case-insensitive). Calls Chrome's
CDP discovery endpoint /json/activate/<id> to focus that tab in the window —
no Playwright needed.

Output: JSON on stdout describing the activated tab.

Exit codes:
    0  success
    3  cannot reach Chrome
    4  no matching tab found
    5  Chrome rejected the activate request
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def fetch_tabs(port: int) -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2.0) as r:
        return json.loads(r.read().decode("utf-8"))


def activate(port: int, tab_id: str) -> str:
    # /json/activate/<id> expects a POST in newer Chromes but a GET also works
    # historically. urllib.request.Request defaults to GET; switch to POST with
    # an empty body, which both old and new Chromes accept.
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/json/activate/{tab_id}",
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=2.0) as r:
        return r.read().decode("utf-8", errors="replace")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    # No baked default: the port belongs to the company profile, which the caller
    # resolves and passes in. A second copy here is how the port and the
    # user-data-dir drifted apart and produced two Chrome windows.
    p.add_argument("--port", type=int, default=(int(os.environ["PORT"]) if os.environ.get("PORT") else None),
                   help="CDP port; defaults to $PORT (set it from profiles/<company>.md)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--tab-id", help="CDP target id from list_tabs.py")
    g.add_argument("--url-substring", help="First tab whose URL contains this substring wins.")
    args = p.parse_args()
    if args.port is None:
        print(json.dumps({"error": "no CDP port given",
                          "hint": "pass --port, or set PORT from profiles/<company>.md "
                                  "(see ai-chrome/SKILL.md → Project profile)"}), file=sys.stderr)
        return 5

    try:
        tabs = fetch_tabs(args.port)
    except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
        print(
            json.dumps({
                "error": f"cannot reach Chrome on port {args.port}: {exc!r}",
                "hint": "run scripts/ensure_chrome.sh first",
            }),
            file=sys.stderr,
        )
        return 3

    target = None
    if args.tab_id:
        for t in tabs:
            if t.get("id") == args.tab_id:
                target = t
                break
    else:
        needle = args.url_substring.lower()
        for t in tabs:
            if t.get("type") != "page":
                continue
            url = (t.get("url") or "").lower()
            if needle in url and not url.startswith(("chrome://", "devtools://")):
                target = t
                break

    if target is None:
        print(
            json.dumps({
                "error": "no matching tab",
                "hint": "run list_tabs.py first",
                "selector": {"tab_id": args.tab_id, "url_substring": args.url_substring},
            }),
            file=sys.stderr,
        )
        return 4

    try:
        body = activate(args.port, target["id"])
    except urllib.error.HTTPError as exc:
        print(
            json.dumps({"error": f"activate failed: HTTP {exc.code}", "body": exc.read().decode("utf-8", errors="replace")}),
            file=sys.stderr,
        )
        return 5
    except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
        print(json.dumps({"error": f"activate failed: {exc!r}"}), file=sys.stderr)
        return 5

    print(json.dumps({
        "activated": {
            "id": target.get("id"),
            "url": target.get("url"),
            "title": target.get("title"),
        },
        "chrome_response": body.strip() or None,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
