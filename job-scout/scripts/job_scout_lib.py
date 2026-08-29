#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

try:
    import certifi
except Exception:  # pragma: no cover
    certifi = None

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HERMES_ENV = Path.home() / '.hermes' / '.env'
PROJECT_ENV = ROOT / '.env'

BLOCK_TAG_RE = re.compile(r"</?(?:br|p|div|li|ul|ol|tr|td|th|h[1-6]|section|article|header|footer)[^>]*>", re.I)
TAG_RE = re.compile(r"<[^>]+>", re.I | re.S)
ANCHOR_RE = re.compile(
    r'<a\b[^>]*?href=["\'](?P<href>.*?)["\'][^>]*>(?P<text>.*?)</a>',
    re.I | re.S,
)
WS_RE = re.compile(r"\s+")


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(errors='ignore').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        out[key] = value
    return out


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env.update(parse_env_file(DEFAULT_HERMES_ENV))
    env.update(parse_env_file(PROJECT_ENV))
    env.update({k: v for k, v in os.environ.items() if v is not None})
    if 'JOB_SCOUT_ROOT' not in env and env.get('JOB_AGENT_ROOT'):
        env['JOB_SCOUT_ROOT'] = env['JOB_AGENT_ROOT']
    env.setdefault('JOB_SCOUT_ROOT', str(ROOT))
    env.setdefault('OBSIDIAN_VAULT_PATH', str(Path.home() / 'Documents' / 'Obsidian Vault'))
    env.setdefault('JOB_OBSIDIAN_BASE_DIR', 'Career/Jobs')
    return env


def project_root(env: dict[str, str] | None = None) -> Path:
    env = env or load_env()
    return Path(env.get('JOB_SCOUT_ROOT') or env.get('JOB_AGENT_ROOT', str(ROOT))).expanduser()


def project_env_path(env: dict[str, str] | None = None) -> Path:
    return project_root(env) / '.env'


def config_path(env: dict[str, str] | None = None) -> Path:
    env = env or load_env()
    override = env.get('JOB_AGENT_CONFIG', '').strip()
    if override:
        return Path(override).expanduser()
    return project_root(env) / 'config' / 'job-scout.yaml'


def load_project_config(env: dict[str, str] | None = None) -> dict[str, Any]:
    env = env or load_env()
    path = config_path(env)
    if not path.exists() or yaml is None:
        return {}
    try:
        payload = yaml.safe_load(path.read_text())
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def nested_get(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    cur: Any = payload
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def resolve_path_spec(
    env: dict[str, str],
    source: str,
    path_value: str,
) -> Path:
    path_obj = Path(path_value).expanduser()
    if source == 'absolute':
        return path_obj
    if source == 'vault_relative':
        return Path(env.get('OBSIDIAN_VAULT_PATH', str(Path.home() / 'Documents' / 'Obsidian Vault'))).expanduser() / path_value
    return project_root(env) / path_value


def config_resolved_path(
    env: dict[str, str],
    cfg: dict[str, Any],
    section: str,
    *,
    default_source: str = 'project_relative',
    default_path: str = '',
) -> Path:
    source = str(nested_get(cfg, section, 'source', default=default_source) or default_source)
    path_value = str(nested_get(cfg, section, 'path', default=default_path) or default_path)
    return resolve_path_spec(env, source, path_value)


def jobs_inbox_json_path(env: dict[str, str], cfg: dict[str, Any]) -> Path:
    return config_resolved_path(env, cfg, 'jobs_inbox_json', default_path='data/inbox/jobs_today.json')


def jobs_enriched_json_path(env: dict[str, str], cfg: dict[str, Any]) -> Path:
    return config_resolved_path(env, cfg, 'jobs_enriched_json', default_path='data/inbox/jobs_enriched.json')


def seen_jobs_json_path(env: dict[str, str], cfg: dict[str, Any]) -> Path:
    return config_resolved_path(env, cfg, 'seen_jobs_json', default_path='data/seen_jobs.json')


def jobs_filtered_json_path(env: dict[str, str], cfg: dict[str, Any]) -> Path:
    return config_resolved_path(env, cfg, 'jobs_filtered_json', default_path='data/inbox/jobs_filtered.json')


def jobs_to_score_json_path(env: dict[str, str], cfg: dict[str, Any]) -> Path:
    return config_resolved_path(env, cfg, 'jobs_to_score_json', default_path='data/inbox/jobs_to_score.json')


def resume_source_path(env: dict[str, str], cfg: dict[str, Any]) -> Path:
    override = env.get('JOB_RESUME_SOURCE', '').strip()
    if override:
        return Path(override).expanduser()
    return config_resolved_path(env, cfg, 'resume', default_source='vault_relative', default_path='Career/resume/resume.md')


def profile_source_path(env: dict[str, str], cfg: dict[str, Any]) -> Path:
    override = env.get('JOB_PROFILE_SOURCE', '').strip()
    if override:
        return Path(override).expanduser()
    return config_resolved_path(env, cfg, 'profile', default_source='project_relative', default_path='resume/profile.yaml')


def obsidian_output_base_dir(env: dict[str, str], cfg: dict[str, Any]) -> str:
    return str(nested_get(cfg, 'output', 'obsidian_base_dir', default=env.get('JOB_OBSIDIAN_BASE_DIR', 'Career/Jobs')) or 'Career/Jobs')


def pending_inbox_md_path(env: dict[str, str], cfg: dict[str, Any]) -> Path:
    base_dir = obsidian_output_base_dir(env, cfg)
    vault = Path(env.get('OBSIDIAN_VAULT_PATH', str(Path.home() / 'Documents' / 'Obsidian Vault'))).expanduser()
    return vault / base_dir / 'Pending JDs.md'


def external_search_enabled(env: dict[str, str], cfg: dict[str, Any]) -> bool:
    raw = env.get('JOB_EXTERNAL_SEARCH_ENABLED')
    if raw is not None and raw != '':
        return truthy(raw)
    default = nested_get(cfg, 'external_jd', 'search_enabled', default=False)
    return bool(default)


def external_search_site_filters(env: dict[str, str], cfg: dict[str, Any], fallback: list[str]) -> str:
    raw = env.get('JOB_SEARCH_SITE_FILTERS', '').strip()
    if raw:
        return raw
    configured = nested_get(cfg, 'external_jd', 'allowed_domains', default=[])
    if isinstance(configured, list) and configured:
        return ' '.join(f'site:{str(item).strip()}' for item in configured if str(item).strip())
    return ' '.join(fallback)


def collapse_ws(value: str) -> str:
    return WS_RE.sub(' ', value or '').strip()


def html_to_text(value: str) -> str:
    if not value:
        return ''
    text = BLOCK_TAG_RE.sub('\n', value)
    text = TAG_RE.sub(' ', text)
    text = unescape(text)
    lines = [collapse_ws(line) for line in text.splitlines()]
    return '\n'.join(line for line in lines if line)


def html_to_lines(value: str) -> list[str]:
    return [line for line in html_to_text(value).splitlines() if line]


def extract_links(html: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    if not html:
        return links
    for match in ANCHOR_RE.finditer(html):
        href = unwrap_url(match.group('href'))
        text = collapse_ws(html_to_text(match.group('text')))
        if not href:
            continue
        key = (href, text)
        if key in seen:
            continue
        seen.add(key)
        links.append({'href': href, 'text': text})
    return links


def unwrap_url(url: str) -> str:
    if not url:
        return ''
    candidate = unescape(url).strip()
    if candidate.startswith('//'):
        candidate = 'https:' + candidate
    if candidate.startswith(('mailto:', 'javascript:', '#')):
        return ''
    for _ in range(3):
        parsed = urlparse(candidate)
        qs = parse_qs(parsed.query)
        for key in ('url', 'u', 'dest', 'destination', 'redirect', 'redir', 'redirect_url'):
            if qs.get(key):
                nxt = unquote(qs[key][0])
                if nxt and nxt != candidate:
                    candidate = nxt
                    break
        else:
            break
    return candidate


def is_http_url(url: str) -> bool:
    return url.startswith('http://') or url.startswith('https://')


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or '').lower()
    except Exception:
        return ''


def is_linkedin_url(url: str) -> bool:
    host = host_of(url)
    return host == 'linkedin.com' or host.endswith('.linkedin.com') or host == 'lnkd.in'


def looks_like_linkedin_job_url(url: str) -> bool:
    if not is_linkedin_url(url):
        return False
    parsed = urlparse(url)
    path = parsed.path.lower()
    query = parsed.query.lower()
    return '/jobs/' in path or 'currentjobid=' in query or '/job/' in path


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n')


def append_jsonl(path: Path, payload: Any) -> None:
    ensure_parent(path)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + '\n')


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def history_run_parts(now: datetime | None = None) -> tuple[str, str]:
    stamp = now or utc_now()
    return stamp.strftime('%Y-%m-%d'), stamp.strftime('%Y-%m-%dT%H%M%SZ')


def write_json_snapshot_and_history(
    env: dict[str, str],
    snapshot_path: Path,
    payload: Any,
    *,
    history_group: str,
) -> tuple[Path, Path]:
    write_json(snapshot_path, payload)
    report_date, run_slug = history_run_parts()
    history_dir = project_root(env) / 'data' / 'history' / history_group / report_date
    history_path = history_dir / f'{run_slug} - {snapshot_path.name}'
    write_json(history_path, payload)
    history_log_path = project_root(env) / 'data' / 'history' / history_group / f'{history_group}.jsonl'
    payload_summary: dict[str, Any] = {}
    if isinstance(payload, dict):
        payload_summary = {
            key: payload.get(key)
            for key in ('generated_at', 'report_date', 'source', 'message_count', 'job_count', 'resolved_count', 'total_jobs', 'resolved_unique_jobs', 'unresolved_jobs')
            if key in payload
        }
    append_jsonl(history_log_path, {
        'logged_at': utc_now().isoformat(),
        'report_date': report_date,
        'run_slug': run_slug,
        'snapshot_path': str(snapshot_path),
        'history_path': str(history_path),
        'summary': payload_summary,
    })
    return history_path, history_log_path


def truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'y', 'on'}


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', '-', name).strip().strip('.')
    return cleaned or 'untitled'


def default_cafile() -> str | None:
    if certifi is None:
        return None
    try:
        return certifi.where()
    except Exception:
        return None


_US_TOKEN_RE = re.compile(
    r'\b(?:united\s+states|u\.?s\.?a\.?|us)\b',
    re.IGNORECASE,
)


_REMOTE_RE = re.compile(r'\bremote\b', re.I)
_HYBRID_RE = re.compile(r'\bhybrid\b', re.I)
# Vague location strings that should pass (benefit of the doubt)
_VAGUE_LOCATION_RE = re.compile(
    r'^(?:united\s+states|usa?|n/?a|\d+\s+locations?)$', re.I
)
# Common abbreviations for allowed cities, e.g. {'san francisco': ['sf', 'sfo']}.
_CITY_ABBREVIATIONS: dict[str, list[str]] = {}


def location_passes_city_filter(location: str | None, cfg: dict[str, Any] | None = None) -> bool:
    """Check if a job location passes the allowed-cities filter.

    Uses ``scan.location_filter`` from *cfg*.  Rules:
    - No filter configured → passes.
    - Empty / unknown / vague location → passes (benefit of the doubt).
    - Remote / hybrid → passes if ``allow_remote`` is True.
    - Otherwise must mention at least one ``allowed_cities`` entry
      (or a known abbreviation like SFO → san francisco).
    """
    cfg = cfg or {}
    scan_lf = ((cfg.get('scan') or {}).get('location_filter') or {})
    cities = scan_lf.get('allowed_cities') or []
    if not cities:
        return True
    text = (location or '').strip()
    if not text:
        return True
    # Vague / broad US locations pass
    if _VAGUE_LOCATION_RE.search(text):
        return True
    allow_remote = scan_lf.get('allow_remote', True)
    if allow_remote and (_REMOTE_RE.search(text) or _HYBRID_RE.search(text)):
        return True
    text_lower = text.lower()
    # Check full city names
    if any(c.lower() in text_lower for c in cities):
        return True
    # Check abbreviations
    for city in cities:
        for abbr in _CITY_ABBREVIATIONS.get(city.lower(), []):
            if re.search(r'\b' + re.escape(abbr) + r'\b', text_lower):
                return True
    return False


def is_us_location(location: str | None, cfg: dict[str, Any] | None = None) -> tuple[bool, str | None]:
    """Classify a free-text location string as US-eligible or non-US.

    Returns (is_us, reason). is_us=True means the job should NOT be filtered out.

    Logic:
    - Empty/blank location → unknown_policy from cfg (default 'pass' → True).
    - Any explicit US token (united states, usa, us as a whole word) → True.
    - Any configured non-US keyword as a whole word → False with that keyword as reason.
    - Otherwise → unknown_policy.

    Non-US keywords are matched with word boundaries to avoid false positives
    like 'Indianapolis' → 'india' or 'truck' → 'uk'.
    """
    text = (location or '').strip()
    cfg = cfg or {}
    lf = (cfg.get('location_filter') or {}) if isinstance(cfg, dict) else {}
    unknown_policy = (lf.get('unknown_policy') or 'pass').strip().lower()
    unknown_passes = unknown_policy != 'filter'

    if not text:
        return (unknown_passes, 'empty' if unknown_passes else 'empty (filter policy)')

    if _US_TOKEN_RE.search(text):
        return (True, 'us-mentioned')

    raw_keywords = [str(k) for k in (lf.get('non_us_keywords') or []) if str(k).strip()]
    for kw in raw_keywords:
        norm = kw.strip().lower()
        if not norm:
            continue
        pattern = r'\b' + re.escape(norm) + r'\b'
        if re.search(pattern, text, re.IGNORECASE):
            return (False, norm)

    return (unknown_passes, None if unknown_passes else 'unknown (filter policy)')
