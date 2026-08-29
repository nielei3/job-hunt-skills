#!/usr/bin/env python3
"""Fetch a URL using the live Chrome session's auth and save it to disk.

Why not plain requests? Course platforms gate images/audio behind cookies, JWTs,
and Referer checks tied to the logged-in session. By going through the browser's
APIRequestContext (over the existing CDP connection), we inherit the full auth
state of the tab the user is on — no cookie scraping needed.

Usage:
    python3 fetch_resource.py <url> --out <path>
    python3 fetch_resource.py <url> --out-dir <dir>     # filename inferred from URL

Exits 0 on success, 2 on bad args, 3 if Chrome is unreachable, 6 on HTTP error.
Prints JSON {ok, url, path, bytes, status, content_type} to stdout on success.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


CDP_URL = "http://localhost:9222"


def fail(code: int, msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(code)


EXT_FROM_CT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/avif": ".avif",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "video/mp4": ".mp4",
    "application/pdf": ".pdf",
}


def infer_filename(url: str, content_type: Optional[str]) -> str:
    name = Path(urlparse(url).path).name or "resource"
    if "." in name:
        return name
    ext = ""
    if content_type:
        ct = content_type.split(";", 1)[0].strip().lower()
        ext = EXT_FROM_CT.get(ct) or mimetypes.guess_extension(ct) or ""
    return name + ext


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url")
    ap.add_argument("--out", help="exact output path (overrides --out-dir)")
    ap.add_argument("--out-dir", help="output directory; filename inferred from URL/content-type")
    ap.add_argument("--referer", help="explicit Referer header (defaults to URL host)")
    ap.add_argument("--timeout-ms", type=int, default=30000)
    args = ap.parse_args()

    if not args.out and not args.out_dir:
        fail(2, "must pass --out <path> or --out-dir <dir>")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        fail(2, "playwright not installed. Run: pip install playwright")

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            fail(3, f"cannot reach Chrome at {CDP_URL}: {e}")

        contexts = browser.contexts
        if not contexts:
            fail(3, "Chrome has no open contexts")
        ctx = contexts[0]

        headers = {}
        if args.referer:
            headers["Referer"] = args.referer
        else:
            parsed = urlparse(args.url)
            headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"

        try:
            resp = ctx.request.get(args.url, headers=headers, timeout=args.timeout_ms)
        except Exception as e:
            fail(6, f"fetch failed: {e}")

        if not resp.ok:
            fail(6, f"HTTP {resp.status} for {args.url}")

        body = resp.body()
        content_type = resp.headers.get("content-type")

        if args.out:
            out_path = Path(args.out)
        else:
            fname = infer_filename(args.url, content_type)
            out_path = Path(args.out_dir) / fname

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(body)

        result = {
            "ok": True,
            "url": args.url,
            "path": str(out_path),
            "bytes": len(body),
            "status": resp.status,
            "content_type": content_type,
        }
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
