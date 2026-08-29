#!/usr/bin/env python3
from __future__ import annotations

import argparse
import email
import hashlib
import imaplib
import re
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.policy import default
from pathlib import Path
from typing import Any

from job_scout_lib import (
    collapse_ws,
    default_cafile,
    extract_links,
    html_to_lines,
    is_http_url,
    is_linkedin_url,
    jobs_inbox_json_path,
    load_env,
    load_project_config,
    looks_like_linkedin_job_url,
    read_json,
    seen_jobs_json_path,
    truthy,
    write_json,
    write_json_snapshot_and_history,
)

# Re-export compound-title helpers for offline-reparse and backwards compat
from email_parsers.linkedin_parser import (
    GENERIC_LINK_TEXT,
    load_known_companies,
    looks_like_compound_anchor,
    split_compound_title,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Read job alert emails (LinkedIn + Jobright) and emit structured jobs JSON.')
    parser.add_argument('--out', default='')
    parser.add_argument('--seen', default='')
    parser.add_argument('--include-seen', action='store_true')
    parser.add_argument('--eml-dir', default='')
    parser.add_argument('--offline-reparse', default='',
                        help='Re-parse an existing jobs_today.json file in place using the new compound-title splitter, without hitting IMAP.')
    return parser.parse_args()


def fetch_candidate_messages(env: dict[str, str], eml_dir: str) -> list[EmailMessage]:
    if eml_dir:
        return read_eml_dir(Path(eml_dir))
    if env.get('JOB_ALERT_LOCAL_EML_DIR'):
        return read_eml_dir(Path(env['JOB_ALERT_LOCAL_EML_DIR']))
    return fetch_imap_messages(env)


def read_eml_dir(folder: Path) -> list[EmailMessage]:
    messages: list[EmailMessage] = []
    if not folder.exists():
        return messages
    for path in sorted(folder.glob('*.eml')):
        try:
            msg = email.message_from_bytes(path.read_bytes(), policy=default)
            messages.append(msg)
        except Exception:
            continue
    return messages


def fetch_imap_messages(env: dict[str, str]) -> list[EmailMessage]:
    host = env.get('JOB_ALERT_IMAP_HOST', '')
    user = env.get('JOB_ALERT_IMAP_USER', '')
    password = env.get('JOB_ALERT_IMAP_PASSWORD', '')
    port = int(env.get('JOB_ALERT_IMAP_PORT', '993'))
    folder = env.get('JOB_ALERT_IMAP_FOLDER', 'INBOX')
    lookback_days = int(env.get('JOB_ALERT_LOOKBACK_DAYS', '3'))
    unread_only = truthy(env.get('JOB_ALERT_UNREAD_ONLY'), default=True)
    max_messages = int(env.get('JOB_ALERT_MAX_MESSAGES', '25'))

    if not host or not user or not password:
        raise SystemExit(
            'Missing IMAP config. Set JOB_ALERT_IMAP_HOST / USER / PASSWORD in ~/.hermes/.env '
            'or pass --eml-dir for local .eml testing.'
        )

    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime('%d-%b-%Y')
    criteria = ['SINCE', since]
    if unread_only:
        criteria.insert(0, 'UNSEEN')

    cafile = default_cafile()
    ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
    messages: list[EmailMessage] = []
    with imaplib.IMAP4_SSL(host, port, ssl_context=ctx) as client:
        client.login(user, password)
        status, _ = client.select(folder)
        if status != 'OK':
            raise SystemExit(f'Could not select IMAP folder: {folder}')
        status, data = client.search(None, *criteria)
        if status != 'OK':
            raise SystemExit('IMAP search failed')
        msg_ids = data[0].split()[-max_messages:]
        for msg_id in msg_ids:
            status, payload = client.fetch(msg_id, '(RFC822)')
            if status != 'OK' or not payload:
                continue
            raw = payload[0][1]
            try:
                msg = email.message_from_bytes(raw, policy=default)
                messages.append(msg)
            except Exception:
                continue
    return messages


def finalize_job(job: dict[str, Any]) -> dict[str, Any]:
    # Use first available URL as dedup seed
    seed = job.get('linkedin_url') or job.get('jobright_url') or '|'.join(
        [job.get('title', ''), job.get('company', ''), job.get('location', ''), job.get('subject', '')]
    )
    job_key = hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]
    job['job_key'] = job_key
    job['discovered_at'] = datetime.now(timezone.utc).isoformat()
    job['needs_external_jd'] = True
    return job


def upsert_seen_jobs(env: dict[str, str], seen_path: Path, jobs: list[dict[str, Any]]) -> set[str]:
    db = read_json(seen_path, {'jobs': []})
    existing = {item.get('job_key'): item for item in db.get('jobs', []) if item.get('job_key')}
    previous_keys = set(existing)
    now = datetime.now(timezone.utc).isoformat()
    for job in jobs:
        key = job['job_key']
        if key in existing:
            existing[key]['last_seen_at'] = now
        else:
            existing[key] = {
                'job_key': key,
                'first_seen_at': now,
                'last_seen_at': now,
                'title': job.get('title', ''),
                'company': job.get('company', ''),
                'location': job.get('location', ''),
                'linkedin_url': job.get('linkedin_url', ''),
                'jobright_url': job.get('jobright_url', ''),
            }
    db['jobs'] = sorted(existing.values(), key=lambda item: item.get('last_seen_at', ''))
    write_json_snapshot_and_history(env, seen_path, db, history_group='seen_jobs')
    return previous_keys


def reparse_jobs_file(path: Path, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Re-apply split_compound_title to every job in an existing jobs_today.json.

    Drops rows whose title is generic navigation text (e.g., 'Manage
    recommendations') and whose LinkedIn URL contains no jobId.
    """
    payload = read_json(path, {})
    jobs = payload.get('jobs', []) if isinstance(payload, dict) else []
    known = load_known_companies(cfg)
    cleaned: list[dict[str, Any]] = []
    for job in jobs:
        raw_title = job.get('title', '') or ''
        url = job.get('linkedin_url', '') or ''
        generic = raw_title.strip().lower() in GENERIC_LINK_TEXT
        has_jobid = bool(re.search(r'/jobs/view/(\d+)', url))
        # Drop if URL isn't a specific job page — search-results links etc.
        if not has_jobid:
            continue
        if generic:
            continue
        # Prefer the raw anchor form if we still have it; otherwise work off title.
        candidate = raw_title
        if looks_like_compound_anchor(candidate):
            parsed = split_compound_title(candidate, known_companies=known)
            if parsed['title']:
                job['title'] = parsed['title']
            if parsed['company']:
                job['company'] = parsed['company']
            if parsed['location']:
                job['location'] = parsed['location']
            if parsed['modality']:
                job['modality'] = parsed['modality']
        cleaned.append(job)
    payload['jobs'] = cleaned
    payload['job_count'] = len(cleaned)
    write_json(path, payload)
    return payload


def main() -> None:
    args = parse_args()
    env = load_env()
    cfg = load_project_config(env)

    if args.offline_reparse:
        target = Path(args.offline_reparse)
        out = reparse_jobs_file(target, cfg)
        print(f"reparsed {target} — {out.get('job_count', 0)} jobs retained")
        return

    messages = fetch_candidate_messages(env, args.eml_dir)

    # Dispatch ALL messages through the multi-source dispatcher
    from email_parsers.dispatcher import dispatch_message, identify_source

    jobs: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    source_counts: dict[str, int] = {}
    for msg in messages:
        msg_jobs = dispatch_message(msg, cfg=cfg)
        for j in msg_jobs:
            j = finalize_job(j)
            # Deduplicate across emails within the same run
            if j['job_key'] not in seen_keys:
                seen_keys.add(j['job_key'])
                jobs.append(j)
        # Track per-source stats
        src = identify_source(msg)
        source_counts[src] = source_counts.get(src, 0) + 1

    seen_path = Path(args.seen) if args.seen else seen_jobs_json_path(env, cfg)
    previous_keys = upsert_seen_jobs(env, seen_path, jobs)
    if not args.include_seen:
        jobs = [job for job in jobs if job['job_key'] not in previous_keys]

    out_path = Path(args.out) if args.out else jobs_inbox_json_path(env, cfg)
    out = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source': 'job_alert_email',
        'message_count': len(messages),
        'source_counts': source_counts,
        'job_count': len(jobs),
        'candidate_subjects': [collapse_ws(msg.get('subject', '')) for msg in messages[:20]],
        'jobs': jobs,
    }
    history_path, history_log = write_json_snapshot_and_history(env, out_path, out, history_group='jobs_inbox')
    print(f"wrote {out_path} ({len(jobs)} new jobs from {len(messages)} messages)")
    print(f"  sources: {source_counts}")
    print(f"archived inbox snapshot: {history_path}")
    print(f"appended inbox history log: {history_log}")


if __name__ == '__main__':
    main()
