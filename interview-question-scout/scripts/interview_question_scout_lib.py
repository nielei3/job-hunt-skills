#!/usr/bin/env python3
"""Shared helpers for interview-question-scout.

Reuses utilities from sibling `job-scout` project (env loading, iCloud fallback,
filename sanitization) and adds interview-question-scout-specific helpers:
CDP probe, LLM client factory, SQLite dedup, path resolution, logging.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

# Reuse job-scout utilities (repo was split out of agent-skills into career-skills)
for _cand in (
    Path.home() / "github/career-skills/job-scout/scripts",
    Path.home() / "github/agent-skills/career/job-scout/scripts",
):
    if (_cand / "job_scout_lib.py").exists():
        sys.path.insert(0, str(_cand))
        break
from job_scout_lib import (  # noqa: E402
    parse_env_file,
    ensure_parent,
    sanitize_filename,
)

try:
    import yaml
except ImportError:
    yaml = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HERMES_ENV = Path.home() / ".hermes" / ".env"
PROJECT_ENV = PROJECT_ROOT / ".env"


def load_env() -> dict[str, str]:
    """Merge env from ~/.hermes/.env + project .env + os.environ."""
    import os

    env: dict[str, str] = {}
    env.update(parse_env_file(HERMES_ENV))
    env.update(parse_env_file(PROJECT_ENV))
    env.update({k: v for k, v in os.environ.items() if v is not None})
    env.setdefault("INTERVIEW_SCOUT_ROOT", str(PROJECT_ROOT))
    env.setdefault(
        "OBSIDIAN_VAULT_PATH",
        str(Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents"),
    )
    env.setdefault("INTERVIEW_OBSIDIAN_BASE_DIR", "Career/Interviews")
    env.setdefault("OA_CHROME_CDP_URL", "http://localhost:9222")
    return env


def project_root() -> Path:
    return PROJECT_ROOT


def config_path() -> Path:
    return PROJECT_ROOT / "config" / "interview-question-scout.yaml"


def load_config() -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML required: pip install pyyaml")
    return yaml.safe_load(config_path().read_text())


def data_dir(env: dict[str, str]) -> Path:
    return PROJECT_ROOT / "data"


def logs_dir(env: dict[str, str]) -> Path:
    d = data_dir(env) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def raw_html_path(env: dict[str, str], company_slug: str, tid: str) -> Path:
    p = data_dir(env) / "raw" / company_slug / f"{tid}.html"
    ensure_parent(p)
    return p


def obsidian_file_path(env: dict[str, str], relative: str) -> Path:
    vault = Path(env["OBSIDIAN_VAULT_PATH"])
    return vault / relative


def bodies_json_path(env: dict[str, str], company_slug: str) -> Path:
    return data_dir(env) / "bodies" / f"{company_slug}.json"


def load_bodies(env: dict[str, str], company_slug: str) -> dict[str, dict]:
    p = bodies_json_path(env, company_slug)
    if p.exists():
        return json.loads(p.read_text())
    return {}


def save_body(env: dict[str, str], company_slug: str, tid: str, entry: dict) -> None:
    p = bodies_json_path(env, company_slug)
    ensure_parent(p)
    bodies = load_bodies(env, company_slug)
    bodies[tid] = entry
    p.write_text(json.dumps(bodies, ensure_ascii=False, indent=2))


# ---------------- CDP probe ----------------


def cdp_probe(cdp_url: str, timeout: float = 3.0) -> dict[str, str] | None:
    """Return Chrome version info if CDP endpoint is reachable, else None."""
    url = cdp_url.rstrip("/") + "/json/version"
    try:
        with urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


# ---------------- SQLite dedup ----------------


SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_posts (
    company_slug TEXT NOT NULL,
    tid TEXT NOT NULL,
    url TEXT,
    title TEXT,
    fetched_at TEXT NOT NULL,
    locked_by_dami INTEGER NOT NULL DEFAULT 0,
    summary_status TEXT,
    summary_error TEXT,
    PRIMARY KEY (company_slug, tid)
);
CREATE TABLE IF NOT EXISTS run_log (
    started_at TEXT NOT NULL,
    finished_at TEXT,
    companies TEXT,
    new_count INTEGER,
    locked_count INTEGER,
    error_count INTEGER,
    notes TEXT
);
"""


def db_path(env: dict[str, str]) -> Path:
    return data_dir(env) / "seen_posts.sqlite"


def db_connect(env: dict[str, str]) -> sqlite3.Connection:
    p = db_path(env)
    ensure_parent(p)
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    return con


def tid_seen(con: sqlite3.Connection, company_slug: str, tid: str) -> bool:
    cur = con.execute(
        "SELECT 1 FROM seen_posts WHERE company_slug=? AND tid=? LIMIT 1",
        (company_slug, tid),
    )
    return cur.fetchone() is not None


def mark_tid_seen(
    con: sqlite3.Connection,
    company_slug: str,
    tid: str,
    url: str,
    title: str,
    locked: bool,
    summary_status: str,
    summary_error: str | None = None,
) -> None:
    con.execute(
        """INSERT OR REPLACE INTO seen_posts
           (company_slug, tid, url, title, fetched_at, locked_by_dami, summary_status, summary_error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            company_slug,
            tid,
            url,
            title,
            utc_now_iso(),
            1 if locked else 0,
            summary_status,
            summary_error,
        ),
    )
    con.commit()


# ---------------- iCloud file handling ----------------


def read_text_with_icloud_fallback(path: Path) -> str:
    """Read an iCloud-backed text file; forces download if file is a stub/dataless.

    iCloud evicted files (flags: compressed,dataless) raise OSError errno 11
    "Resource deadlock avoided" on read. We force a download first via brctl.
    """
    try:
        return path.read_text()
    except FileNotFoundError:
        return ""
    except (PermissionError, OSError) as exc:
        if "Mobile Documents" not in str(path):
            raise
        # Force iCloud to download the file locally
        _ensure_icloud_downloaded(path)
        try:
            return path.read_text()
        except OSError:
            # Last resort: copy via Finder AppleScript
            tmp_target = Path("/private/tmp") / sanitize_filename(path.name)
            applescript = f'''
set srcFile to POSIX file "{path}" as alias
set dstFolder to POSIX file "/private/tmp/" as alias
tell application "Finder"
  set newFile to duplicate srcFile to dstFolder with replacing
  return POSIX path of (newFile as alias)
end tell
'''
            subprocess.run(["osascript"], input=applescript, text=True, check=True, capture_output=True)
            return tmp_target.read_text()


def _ensure_icloud_downloaded(path: Path, timeout: int = 15) -> None:
    """Force iCloud Drive to download a file locally via brctl.

    Waits until the 'dataless' flag is cleared (file materialized on disk).
    """
    import time

    subprocess.run(["brctl", "download", str(path)], capture_output=True, timeout=10)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            # Check if file is still dataless via stat flags
            result = subprocess.run(
                ["stat", "-f", "%f", str(path)],
                capture_output=True, text=True, timeout=5
            )
            flags = int(result.stdout.strip())
            # UF_COMPRESSED = 0x20, SF_DATALESS = 0x40000000
            if not (flags & 0x40000000):
                return  # File is materialized
        except (ValueError, subprocess.TimeoutExpired):
            pass
        time.sleep(1)
    # Timeout — proceed anyway, write_with_retry will handle remaining issues


def write_text_append(path: Path, text: str) -> None:
    ensure_parent(path)
    # Trigger download first if file is iCloud stub (so append sees the real body)
    if path.exists():
        try:
            existing = read_text_with_icloud_fallback(path)
        except Exception:
            existing = ""
        content = existing + text
    else:
        content = text
    _write_with_retry(path, content)


def _write_with_retry(path: Path, content: str, max_attempts: int = 5, base_delay: float = 1.0) -> None:
    """Write text to an iCloud-backed file with retry on lock contention.

    iCloud evicted files (dataless) raise OSError errno 11 on both read AND write.
    We force a download first, then retry with exponential backoff.
    """
    import time
    import random

    # Ensure file is materialized before writing
    if path.exists() and "Mobile Documents" in str(path):
        _ensure_icloud_downloaded(path)

    for attempt in range(max_attempts):
        try:
            path.write_text(content)
            return
        except OSError as exc:
            if "deadlock" in str(exc).lower() or "resource temporarily unavailable" in str(exc).lower():
                if attempt == max_attempts - 1:
                    raise
                delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                time.sleep(delay)
            else:
                raise


# ---------------- time helpers ----------------


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().strftime("%Y-%m-%d %H:%M:%S UTC")


def local_now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------- HTTP helpers ----------------


def http_post_json(url: str, body: dict[str, Any], headers: dict[str, str], timeout: int = 30) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))
