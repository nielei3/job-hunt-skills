#!/usr/bin/env python3
"""Fetch interview threads for a given company from jobs.1point3acres.com via
an existing Chrome instance exposed on CDP (localhost:9222).

Exports:
    open_browser(cdp_url) -> Playwright browser context
    fetch_company_list(context, company_slug, delay) -> list[dict]
    fetch_thread(context, url, delay, raw_save_path=None) -> dict
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright


LIST_URL_TEMPLATE = "https://jobs.1point3acres.com/companies/{slug}/interview"
THREAD_URL_RE = re.compile(r"https?://www\.1point3acres\.com/bbs/thread-(\d+)-1-1\.html")


@dataclass
class ThreadListing:
    tid: str
    url: str
    title: str
    tags: list[str]
    posted_at: str  # from the <time title="..."> attribute
    messages: int | None
    likes: int | None


def connect_to_cdp(cdp_url: str):
    """Return (playwright, browser) attached to an already-running Chrome."""
    p = sync_playwright().start()
    browser = p.chromium.connect_over_cdp(cdp_url)
    return p, browser


def _new_page(browser: Browser) -> tuple[BrowserContext, Page]:
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()
    return context, page


def _goto(page: Page, url: str, render_delay: float = 4.0) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    # 1p3a keeps firing ad/analytics requests so networkidle rarely fires.
    # Fixed delay is simpler and reliable.
    time.sleep(render_delay)


def parse_list_page(html: str) -> list[ThreadListing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[ThreadListing] = []
    seen_tids: set[str] = set()

    for anchor in soup.find_all("a", href=THREAD_URL_RE):
        href = anchor.get("href", "")
        m = THREAD_URL_RE.match(href)
        if not m:
            continue
        tid = m.group(1)
        if tid in seen_tids:
            continue

        title_node = anchor.find("div", class_="flex-1")
        title = title_node.get_text(strip=True) if title_node else ""
        if not title:
            continue  # pure-logo link or similar, skip

        # Date via the preceding <time title="...">
        time_node = None
        prev = anchor.find_previous("time")
        if prev is not None:
            time_node = prev
        posted_at = time_node.get("title", "") if time_node else ""

        # Tags are spans inside a sibling div with `.text-primary` classes in anchor
        tags: list[str] = []
        tag_container = anchor.find("div", class_=re.compile(r"\btext-primary\b"))
        if tag_container:
            for span in tag_container.find_all("span"):
                text = span.get_text(strip=True)
                if text and len(text) <= 30 and "\n" not in text:
                    tags.append(text)

        seen_tids.add(tid)
        listings.append(
            ThreadListing(
                tid=tid,
                url=href,
                title=title,
                tags=tags,
                posted_at=posted_at,
                messages=None,
                likes=None,
            )
        )
    return listings


def fetch_company_list(
    browser: Browser, slug: str, render_delay: float = 4.0
) -> list[ThreadListing]:
    _ctx, page = _new_page(browser)
    try:
        _goto(page, LIST_URL_TEMPLATE.format(slug=slug), render_delay)
        html = page.content()
    finally:
        page.close()
    return parse_list_page(html)


def fetch_company_list_paginated(
    browser: Browser,
    slug: str,
    max_pages: int = 50,
    cutoff_date: str | None = None,
    render_delay: float = 4.0,
    page_click_delay: float = 2.5,
) -> list[ThreadListing]:
    """Paginate through the company listing to collect threads across multiple pages.

    Args:
        browser: CDP-connected browser instance
        slug: company slug on 1point3acres
        max_pages: stop after this many pages (safety limit)
        cutoff_date: ISO date string (e.g. '2025-05-05'); stop when posts are older
        render_delay: seconds to wait after each page load
        page_click_delay: seconds to wait between pagination clicks

    Returns:
        All ThreadListing items found across pages (deduplicated by tid).
    """
    from datetime import datetime

    cutoff_dt = None
    if cutoff_date:
        cutoff_dt = datetime.fromisoformat(cutoff_date)

    _ctx, page = _new_page(browser)
    all_listings: list[ThreadListing] = []
    seen_tids: set[str] = set()

    try:
        _goto(page, LIST_URL_TEMPLATE.format(slug=slug), render_delay)

        for page_num in range(1, max_pages + 1):
            html = page.content()
            page_listings = parse_list_page(html)

            if not page_listings:
                break

            # Dedup and collect
            new_on_page = 0
            hit_cutoff = False
            for listing in page_listings:
                if listing.tid in seen_tids:
                    continue
                # Check date cutoff
                if cutoff_dt and listing.posted_at:
                    try:
                        post_dt = datetime.fromisoformat(listing.posted_at.replace(" ", "T"))
                        if post_dt < cutoff_dt:
                            hit_cutoff = True
                            break
                    except ValueError:
                        pass  # can't parse date, include it
                seen_tids.add(listing.tid)
                all_listings.append(listing)
                new_on_page += 1

            if hit_cutoff:
                break

            if new_on_page == 0:
                break  # all were dupes, likely last page

            # Click next page
            next_btn = page.query_selector("li.ant-pagination-next:not(.ant-pagination-disabled)")
            if not next_btn:
                break  # no more pages

            # Get first thread link to detect page change
            first_link = page.query_selector('a[href*="thread-"]')
            old_href = first_link.get_attribute("href") if first_link else None

            next_btn.click()
            time.sleep(page_click_delay)

            # Wait for content change (up to 10s)
            if old_href:
                for _ in range(20):
                    new_first = page.query_selector('a[href*="thread-"]')
                    new_href = new_first.get_attribute("href") if new_first else None
                    if new_href and new_href != old_href:
                        break
                    time.sleep(0.5)

            time.sleep(render_delay - page_click_delay if render_delay > page_click_delay else 0.5)

    finally:
        page.close()

    return all_listings


# --------------- thread page ---------------


def _extract_dami_lock(soup: BeautifulSoup) -> bool:
    """Detect that the post body content is gated behind 大米."""
    # Discuz "hidden until N 大米 spent" marker
    # Common markers: class contains 'hide' in post body, or specific text
    body = soup.find("td", id=re.compile(r"^postmessage_\d+"))
    if body is None:
        return False
    text = body.get_text(" ", strip=True)
    # Typical lock phrases for 大米
    patterns = [
        r"本帖隐藏.{0,30}需要",
        r"本帖隐藏.{0,30}回复",
        r"购买.{0,10}大米.{0,10}查看",
        r"查看此.{0,10}需要.{0,10}大米",
    ]
    for pat in patterns:
        if re.search(pat, text):
            return True
    return False


def _extract_post_body(soup: BeautifulSoup) -> str:
    """Return the OP (first post) body as cleaned text."""
    body = soup.find("td", id=re.compile(r"^postmessage_\d+"))
    if body is None:
        return ""
    for tag in body.find_all(["script", "style", "ignore_js_op"]):
        tag.decompose()
    text = body.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _extract_all_replies(soup: BeautifulSoup) -> list[str]:
    """Return all reply bodies (excluding the OP which is the first postmessage)."""
    all_posts = soup.find_all("td", id=re.compile(r"^postmessage_\d+"))
    replies = []
    for post in all_posts[1:]:  # skip OP
        for tag in post.find_all(["script", "style", "ignore_js_op"]):
            tag.decompose()
        text = post.get_text("\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        if text.strip():
            replies.append(text)
    return replies


def _extract_title(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    if h1:
        # Remove bracketed prefixes like "[面试经验]"
        text = h1.get_text(" ", strip=True)
        return re.sub(r"^\[[^\]]+\]\s*", "", text).strip()
    title_tag = soup.find("title")
    return title_tag.get_text(strip=True) if title_tag else ""


def _extract_author(soup: BeautifulSoup) -> str:
    auth = soup.find(class_="authi")
    if not auth:
        return ""
    # Prefer a link to the user's profile page (registered users)
    user_link = auth.find("a", href=re.compile(r"space-uid-\d+"))
    if user_link:
        return user_link.get_text(strip=True)
    # Anonymous users: the .authi text starts with the author name,
    # followed by relative or absolute date, then UI controls like "倒序浏览".
    text = auth.get_text(" ", strip=True)
    if "发表于" in text:
        text = text.split("发表于", 1)[0]
    m = re.match(
        r"^(.+?)\s+(\d{4}-\d{1,2}-\d{1,2}"
        r"|\d+\s*(?:小时|分钟|天|秒)\s*前"
        r"|昨天|前天|刚刚)",
        text,
    )
    if m:
        return m.group(1).strip()
    return text.split()[0] if text else ""


def _extract_posted_at(soup: BeautifulSoup) -> str:
    em = soup.find("em", id=re.compile(r"^authorposton"))
    if em:
        # Often: "发表于 2026-04-15 23:24"
        text = em.get_text(strip=True)
        m = re.search(r"(\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)", text)
        if m:
            return m.group(1)
        return text
    return ""


def parse_thread_page(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    body = _extract_post_body(soup)
    replies = _extract_all_replies(soup)
    full_body = body
    if replies:
        full_body = body + "\n\n---\n\n" + "\n\n---\n\n".join(replies)
    return {
        "title": _extract_title(soup),
        "author": _extract_author(soup),
        "posted_at": _extract_posted_at(soup),
        "body": full_body,
        "body_chars": len(full_body),
        "op_body": body,
        "replies": replies,
        "reply_count": len(replies),
        "locked_by_dami": _extract_dami_lock(soup),
    }


def fetch_thread(
    browser: Browser,
    url: str,
    render_delay: float = 4.0,
    raw_save_path: Path | None = None,
) -> dict[str, Any]:
    _ctx, page = _new_page(browser)
    try:
        _goto(page, url, render_delay)
        html = page.content()
        if raw_save_path is not None:
            raw_save_path.write_text(html)
        return parse_thread_page(html)
    finally:
        page.close()


# --------------- CLI for debug ---------------


def _main_cli() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Debug fetch helper for interview-question-scout.")
    parser.add_argument("action", choices=["list", "thread"])
    parser.add_argument("target", help="company slug (for list) or full thread url")
    parser.add_argument("--cdp-url", default="http://localhost:9222")
    args = parser.parse_args()

    p, browser = connect_to_cdp(args.cdp_url)
    try:
        if args.action == "list":
            listings = fetch_company_list(browser, args.target)
            for li in listings:
                print(json.dumps(li.__dict__, ensure_ascii=False))
            print(f"total: {len(listings)}")
        else:
            data = fetch_thread(browser, args.target)
            print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])
    finally:
        # Do NOT browser.close() — that would close the user's Chrome. Just stop playwright.
        p.stop()


if __name__ == "__main__":
    _main_cli()
