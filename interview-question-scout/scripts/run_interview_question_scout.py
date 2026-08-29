#!/usr/bin/env python3
"""Orchestrator: per-company, fetch newest threads, summarize, publish.

Exit codes:
    0: success
    1: hard failure (CDP unreachable, config broken, etc.)
    2: partial failure (some threads errored; run still counted)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import traceback
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from interview_question_scout_lib import (  # noqa: E402
    bodies_json_path,
    cdp_probe,
    db_connect,
    load_config,
    load_env,
    local_now_iso,
    logs_dir,
    mark_tid_seen,
    obsidian_file_path,
    raw_html_path,
    save_body,
    tid_seen,
    utc_now_iso,
)
from fetch_1point3acres import (  # noqa: E402
    ThreadListing,
    connect_to_cdp,
    fetch_company_list,
    fetch_company_list_paginated,
    fetch_thread,
)
from summarize_posts import (  # noqa: E402
    build_client,
    locked_only_markdown_block,
    summarize_thread,
    summary_to_markdown_block,
)
from publish_obsidian import append_block, append_run_footer, ensure_file_header  # noqa: E402
from publish_discord import (  # noqa: E402
    format_alert,
    format_run_summary,
    format_thread_message,
    send_message,
    sleep_for_rate_limit,
)


log = logging.getLogger("interview_question_scout")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _select_companies(cfg: dict[str, Any], only: str | None) -> list[dict[str, Any]]:
    all_companies = cfg.get("companies", [])
    if only:
        return [c for c in all_companies if c.get("slug") == only]
    return [c for c in all_companies if c.get("enabled")]


def _excerpt(body: str, max_lines: int = 8, max_chars: int = 600) -> str:
    lines = body.splitlines()
    out: list[str] = []
    total = 0
    for line in lines:
        if not line.strip():
            continue
        if len(out) >= max_lines:
            break
        if total + len(line) > max_chars:
            break
        out.append(line)
        total += len(line)
    return "\n".join(out)


def _process_company(
    browser,
    env: dict[str, str],
    scrape_cfg: dict[str, Any],
    summarize_cfg: dict[str, Any],
    discord_cfg: dict[str, Any],
    company: dict[str, Any],
    con,
    llm_client,
    args,
) -> tuple[int, int, int]:
    slug = company["slug"]
    name = company["name"]
    obsidian_rel = company["obsidian_file"]
    obsidian_path = obsidian_file_path(env, obsidian_rel)
    ensure_file_header(obsidian_path, name, slug)

    # Auto-deep on first run: if no bodies JSON exists yet, use deep mode (12 months)
    use_deep = args.deep
    if not use_deep and not bodies_json_path(env, slug).exists():
        log.info("first run for %s: auto-enabling deep mode (12 months lookback)", slug)
        use_deep = True

    log.info("fetching %s list", slug)
    try:
        if use_deep:
            from datetime import datetime, timedelta
            cutoff = (datetime.now() - timedelta(days=args.months * 30)).strftime("%Y-%m-%d")
            log.info("deep mode: paginating %s back to %s", slug, cutoff)
            listings: list[ThreadListing] = fetch_company_list_paginated(
                browser, slug,
                max_pages=50,
                cutoff_date=cutoff,
                render_delay=scrape_cfg.get("page_render_delay_seconds", 4.0),
                page_click_delay=2.5,
            )
        else:
            listings: list[ThreadListing] = fetch_company_list(
                browser, slug, render_delay=scrape_cfg.get("page_render_delay_seconds", 4.0)
            )
    except Exception as exc:
        log.exception("list fetch failed for %s", slug)
        _alert(env, discord_cfg, f"list fetch failed for {name}: {exc}")
        return 0, 0, 1
    log.info("%s: list has %d threads", slug, len(listings))

    # Detect possible login expiry: if list is empty or we see 'login' text heavily,
    # the cookie expired or page redirected. For now a zero-length list is our signal.
    if not listings:
        _alert(env, discord_cfg, f"{name}: list empty. 可能是登录态失效或站点结构变更。")
        return 0, 0, 1

    new_count = 0
    locked_count = 0
    error_count = 0
    posts_limit = int(scrape_cfg.get("posts_per_run_limit", 20))
    if args.limit is not None:
        posts_limit = args.limit  # CLI --limit overrides config cap
    fetch_delay = float(scrape_cfg.get("fetch_interval_seconds", 3))
    render_delay = float(scrape_cfg.get("page_render_delay_seconds", 4.0))

    for listing in listings:
        if new_count + locked_count + error_count >= posts_limit:
            log.info("posts_per_run_limit reached, stopping")
            break
        if tid_seen(con, slug, listing.tid):
            log.debug("skip seen: %s", listing.tid)
            continue

        log.info("fetching thread %s: %s", listing.tid, listing.title[:60])
        try:
            raw_path = raw_html_path(env, slug, listing.tid)
            thread = fetch_thread(browser, listing.url, render_delay=render_delay, raw_save_path=raw_path)
        except Exception as exc:
            log.exception("thread fetch failed for %s", listing.tid)
            error_count += 1
            if args.dry_run:
                continue
            mark_tid_seen(
                con,
                slug,
                listing.tid,
                listing.url,
                listing.title,
                locked=False,
                summary_status="fetch_error",
                summary_error=str(exc),
            )
            continue

        locked = thread.get("locked_by_dami", False)
        title = thread.get("title") or listing.title
        author = thread.get("author", "")
        posted_at = thread.get("posted_at") or listing.posted_at
        body = thread.get("body", "")

        if args.dry_run:
            log.info(
                "DRY-RUN would process tid=%s title=%r locked=%s body_chars=%d",
                listing.tid,
                title[:60],
                locked,
                len(body),
            )
            if locked:
                locked_count += 1
            else:
                new_count += 1
            continue

        if locked:
            save_body(env, slug, listing.tid, {
                "title": title,
                "op_body": body,
                "tag_line": " ".join(listing.tags),
                "href": listing.url,
                "posted_at": posted_at,
                "author": author,
                "locked_by_dami": True,
            })
            block = locked_only_markdown_block(
                title=title,
                url=listing.url,
                author=author,
                posted_at=posted_at,
                listing_tags=listing.tags,
                fetched_at=local_now_iso(),
            )
            append_block(obsidian_path, block)
            if discord_cfg.get("enabled"):
                try:
                    send_message(
                        env["DISCORD_BOT_TOKEN"],
                        env[discord_cfg["channel_env"]],
                        format_thread_message(
                            company=name,
                            title=title,
                            url=listing.url,
                            author=author,
                            posted_at=posted_at,
                            oneliner="🔒 大米锁，仅记录标题",
                            locked=True,
                        ),
                    )
                    sleep_for_rate_limit(discord_cfg.get("rate_limit_sleep_seconds", 1.5))
                except Exception:
                    log.exception("discord send failed (locked notice)")
            mark_tid_seen(
                con, slug, listing.tid, listing.url, title, locked=True, summary_status="locked"
            )
            locked_count += 1
            time.sleep(fetch_delay)
            continue

        # Summarize
        try:
            summary = summarize_thread(
                llm_client,
                summarize_cfg.get("model", "claude-opus-4.6"),
                company=name,
                title=title,
                author=author,
                posted_at=posted_at,
                listing_tags=listing.tags,
                body=body,
                max_body_chars=int(summarize_cfg.get("max_body_chars", 8000)),
                timeout=int(summarize_cfg.get("timeout_seconds", 120)),
            )
        except Exception as exc:
            log.exception("summarize failed for %s", listing.tid)
            mark_tid_seen(
                con,
                slug,
                listing.tid,
                listing.url,
                title,
                locked=False,
                summary_status="summary_error",
                summary_error=str(exc),
            )
            error_count += 1
            time.sleep(fetch_delay)
            continue

        save_body(env, slug, listing.tid, {
            "title": title,
            "op_body": body,
            "tag_line": " ".join(listing.tags),
            "href": listing.url,
            "posted_at": posted_at,
            "author": author,
        })

        body_excerpt = _excerpt(body)
        block = summary_to_markdown_block(
            summary=summary,
            title=title,
            url=listing.url,
            author=author,
            posted_at=posted_at,
            listing_tags=listing.tags,
            fetched_at=local_now_iso(),
            locked_by_dami=False,
            body_excerpt=body_excerpt,
        )
        append_block(obsidian_path, block)

        if discord_cfg.get("enabled"):
            try:
                send_message(
                    env["DISCORD_BOT_TOKEN"],
                    env[discord_cfg["channel_env"]],
                    format_thread_message(
                        company=name,
                        title=title,
                        url=listing.url,
                        author=author,
                        posted_at=posted_at,
                        oneliner=summary.get("discord_oneliner", ""),
                        locked=False,
                    ),
                )
                sleep_for_rate_limit(discord_cfg.get("rate_limit_sleep_seconds", 1.5))
            except Exception:
                log.exception("discord send failed")

        mark_tid_seen(
            con, slug, listing.tid, listing.url, title, locked=False, summary_status="ok"
        )
        new_count += 1
        time.sleep(fetch_delay)

    if not args.dry_run:
        append_run_footer(obsidian_path, name, new_count, locked_count, error_count)
        if discord_cfg.get("enabled"):
            try:
                send_message(
                    env["DISCORD_BOT_TOKEN"],
                    env[discord_cfg["channel_env"]],
                    format_run_summary(name, new_count, locked_count, error_count),
                )
                sleep_for_rate_limit(discord_cfg.get("rate_limit_sleep_seconds", 1.5))
            except Exception:
                log.exception("discord send failed (run summary)")

    return new_count, locked_count, error_count


def _alert(env: dict[str, str], discord_cfg: dict[str, Any], message: str) -> None:
    if not discord_cfg.get("enabled"):
        return
    token = env.get("DISCORD_BOT_TOKEN")
    channel = env.get(discord_cfg.get("channel_env", "DISCORD_HOME_CHANNEL"))
    if not token or not channel:
        return
    try:
        send_message(token, channel, format_alert(message))
    except Exception:
        log.exception("discord alert send failed")


def _update_handbook(env: dict[str, str], llm_client, company: dict[str, Any]) -> None:
    """Re-aggregate handbook from accumulated bodies JSON.

    Uses cached stage1 extraction — only new posts need LLM calls.
    Stage2 always re-runs to update frequency counts and merge 考查点.
    """
    from interview_question_scout_lib import bodies_json_path
    from aggregate_handbook import stage1_extract, stage2_canonicalize, render_handbook

    slug = company["slug"]
    name = company["name"]
    bp = bodies_json_path(env, slug)
    if not bp.exists():
        log.warning("no bodies JSON for %s, skipping handbook update", slug)
        return

    import json
    bodies = json.loads(bp.read_text())
    posts = []
    listings = []
    for tid, entry in bodies.items():
        if entry.get("error"):
            continue
        posts.append({
            "thread_id": tid,
            "title": entry.get("title", ""),
            "tag_line": entry.get("tag_line", ""),
            "meta_line": entry.get("meta_line", ""),
            "op_body": entry.get("op_body", ""),
        })
        listings.append({
            "thread_id": tid,
            "href": entry.get("href", f"https://www.1point3acres.com/bbs/thread-{tid}-1-1.html"),
            "title": entry.get("title", ""),
        })

    if not posts:
        log.warning("no usable posts for %s, skipping handbook update", slug)
        return

    vault = env["OBSIDIAN_VAULT_PATH"]
    company_dir = Path(vault) / "Career" / "Company" / name
    company_dir.mkdir(parents=True, exist_ok=True)
    out_path = company_dir / f"{name}-Interviews.md"
    cache_path = out_path.with_suffix(".stage1.json")
    canon_path = out_path.with_suffix(".canonical.json")

    log.info("handbook update for %s: %d posts -> %s", slug, len(posts), out_path)

    stage1 = stage1_extract(llm_client, posts, name, cache_path)
    canon = stage2_canonicalize(llm_client, stage1, name, len(posts))
    canon_path.write_text(json.dumps(canon, ensure_ascii=False, indent=2))

    handbook = render_handbook(canon, listings, stage1, name, len(posts))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(handbook, encoding="utf-8")
    log.info("handbook written: %s (%d chars, %d questions)", out_path, len(handbook), len(canon.get("canonical_questions", [])))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="interview-question-scout orchestrator")
    parser.add_argument("--company", help="process only this slug (overrides enabled flag)")
    parser.add_argument("--limit", type=int, help="cap posts to process per company this run")
    parser.add_argument(
        "--dry-run", action="store_true", help="fetch and parse but do not summarize/publish/mark"
    )
    parser.add_argument(
        "--deep", action="store_true",
        help="paginate through listing pages instead of first page only (use with --months)"
    )
    parser.add_argument(
        "--months", type=int, default=12,
        help="how many months to look back when using --deep (default: 12)"
    )
    parser.add_argument(
        "--handbook", action="store_true",
        help="re-aggregate handbook after fetching (uses cached stage1, re-runs stage2)"
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _setup_logging(args.verbose)
    env = load_env()
    cfg = load_config()
    source_cfg = cfg.get("source", {})
    scrape_cfg = cfg.get("scrape", {})
    summarize_cfg = cfg.get("summarize", {})
    discord_cfg = cfg.get("discord", {})
    cdp_url = source_cfg.get("cdp_url", env.get("OA_CHROME_CDP_URL", "http://localhost:9222"))

    log.info("interview-question-scout starting (dry_run=%s, company=%s, limit=%s)", args.dry_run, args.company, args.limit)
    log.info("logs dir: %s", logs_dir(env))

    # Pre-flight CDP probe
    info = cdp_probe(cdp_url)
    if info is None:
        msg = f"Chrome CDP unreachable at {cdp_url}. Start ai-chrome before running."
        log.error(msg)
        _alert(env, discord_cfg, msg)
        return 1
    log.info("CDP ok: %s", info.get("Browser"))

    companies = _select_companies(cfg, args.company)
    if not companies:
        log.error("No enabled companies (or --company didn't match)")
        return 1
    log.info("processing companies: %s", [c["slug"] for c in companies])

    # Validate Discord envs up front (not fatal if disabled)
    if discord_cfg.get("enabled"):
        channel_env = discord_cfg.get("channel_env", "DISCORD_HOME_CHANNEL")
        if not env.get("DISCORD_BOT_TOKEN"):
            log.warning("DISCORD_BOT_TOKEN not set; Discord disabled")
            discord_cfg = {**discord_cfg, "enabled": False}
        elif not env.get(channel_env):
            log.warning("%s not set; Discord disabled", channel_env)
            discord_cfg = {**discord_cfg, "enabled": False}

    # LLM client
    if not args.dry_run:
        llm_api_key = env.get("OPENAI_API_KEY")
        if not llm_api_key:
            log.error("OPENAI_API_KEY missing")
            _alert(env, discord_cfg, "OPENAI_API_KEY missing — cannot summarize")
            return 1
        llm_base_url = summarize_cfg.get("base_url", env.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
        llm_client = build_client(llm_api_key, llm_base_url)
    else:
        llm_client = None

    p, browser = connect_to_cdp(cdp_url)
    try:
        con = db_connect(env)
        total_new = total_locked = total_errors = 0
        for company in companies:
            try:
                n, l_, e = _process_company(
                    browser, env, scrape_cfg, summarize_cfg, discord_cfg, company, con, llm_client, args
                )
            except Exception:
                log.exception("unhandled error processing %s", company.get("slug"))
                _alert(env, discord_cfg, f"{company.get('name')}: unhandled exception\n{traceback.format_exc()[:400]}")
                e = 1
                n = 0
                l_ = 0
            total_new += n
            total_locked += l_
            total_errors += e
            if args.handbook and n > 0 and not args.dry_run:
                try:
                    _update_handbook(env, llm_client, company)
                except Exception:
                    log.exception("handbook update failed for %s", company.get("slug"))
        log.info(
            "run done: new=%d locked=%d errors=%d (dry_run=%s) at %s",
            total_new,
            total_locked,
            total_errors,
            args.dry_run,
            utc_now_iso(),
        )
    finally:
        # Do NOT close the shared browser — user owns it
        try:
            p.stop()
        except Exception:
            pass

    return 0 if total_errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
