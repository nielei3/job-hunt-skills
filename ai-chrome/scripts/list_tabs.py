#!/usr/bin/env python3
"""List all real (non-chrome://, non-devtools://) tabs in the debug Chrome.

Talks to Chrome's CDP HTTP discovery endpoint at /json — no Playwright needed.

Output: JSON array on stdout. Each item:
    {"id": "...", "title": "...", "url": "...", "active": true|false}

The "active" flag is heuristic: Chrome's CDP discovery doesn't expose which tab
is foregrounded, but it does mark devtools-paired pages — we filter those and
return everything that looks like a normal tab. The first one is usually the
most-recently-active, but don't rely on that — match by URL substring instead.

Exit codes:
    0  success
    3  cannot reach Chrome (run ensure_chrome.sh first)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def fetch_tabs(port: int, timeout: float = 2.0) -> list[dict]:
    url = f"http://127.0.0.1:{port}/json"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    # No baked default: the port belongs to the company profile, which the caller
    # resolves and passes in. A second copy here is how the port and the
    # user-data-dir drifted apart and produced two Chrome windows.
    p.add_argument("--port", type=int, default=(int(os.environ["PORT"]) if os.environ.get("PORT") else None),
                   help="CDP port; defaults to $PORT (set it from profiles/<company>.md)")
    p.add_argument(
        "--include-internal",
        action="store_true",
        help="Include chrome://, devtools://, and background pages.",
    )
    args = p.parse_args()
    if args.port is None:
        print(json.dumps({"error": "no CDP port given",
                          "hint": "pass --port, or set PORT from profiles/<company>.md "
                                  "(see ai-chrome/SKILL.md → Project profile)"}), file=sys.stderr)
        return 5

    try:
        raw = fetch_tabs(args.port)
    except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
        print(
            json.dumps({
                "error": f"cannot reach Chrome on port {args.port}: {exc!r}",
                "hint": "run scripts/ensure_chrome.sh first",
            }),
            file=sys.stderr,
        )
        return 3

    out = []
    for t in raw:
        url = t.get("url", "") or ""
        type_ = t.get("type", "") or ""
        if not args.include_internal:
            if type_ != "page":
                continue
            if url.startswith(("chrome://", "devtools://", "chrome-extension://")):
                continue
        out.append({
            "id": t.get("id"),
            "title": t.get("title", ""),
            "url": url,
            "type": type_,
        })

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
