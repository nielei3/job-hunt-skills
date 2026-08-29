#!/usr/bin/env python3
"""Title-level pre-filter. Rules + (optional) LLM tiebreaker for ambiguous titles."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from job_scout_lib import (  # noqa: E402
    is_us_location,
    jobs_filtered_json_path,
    jobs_inbox_json_path,
    load_env,
    load_project_config,
    nested_get,
    read_json,
    write_json_snapshot_and_history,
)
from llm_runner import call_json as llm_call_json

__all__ = ['classify_one', 'classify_all', 'parse_args', 'main']

_NEEDS_LLM = '__needs_llm__'


def _norm(s: str) -> str:
    return (s or '').strip().lower()


def _matches_any(title: str, keywords: list[str]) -> str | None:
    t = _norm(title)
    for kw in keywords:
        k = _norm(kw)
        if k and k in t:
            return kw
    return None


def _override_company(company: str, overrides: list[str]) -> str | None:
    c = _norm(company)
    for entry in overrides:
        if c == _norm(entry):
            return entry
    return None


def classify_one(job: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Apply rules to a single job. Returns a dict with filter_status, filter_reason, ambiguous_resolved_by."""
    f = cfg.get('filter', {}) or {}
    title = job.get('title') or ''
    company = job.get('company') or ''
    location = job.get('location') or ''
    out = dict(job)
    out['ambiguous_resolved_by'] = None

    lf = cfg.get('location_filter') or {}
    if lf.get('enabled', True):
        is_us, reason = is_us_location(location, cfg)
        if not is_us:
            out['filter_status'] = 'filtered_out'
            out['filter_reason'] = f'non-us location: {reason}'
            return out

    # City-level filter: onsite jobs outside allowed cities are filtered out.
    # Remote/hybrid jobs pass. This reuses the scan.location_filter config.
    scan_lf = (cfg.get('scan') or {}).get('location_filter') or {}
    allowed_cities = scan_lf.get('allowed_cities') or []
    allow_remote = scan_lf.get('allow_remote', True)
    if allowed_cities and location:
        loc_lower = location.lower()
        is_remote = 'remote' in loc_lower or 'hybrid' in loc_lower
        if not (allow_remote and is_remote):
            if not any(c.lower() in loc_lower for c in allowed_cities):
                out['filter_status'] = 'filtered_out'
                out['filter_reason'] = f'onsite outside allowed cities: {location}'
                return out

    override = _override_company(company, f.get('allow_override_companies') or [])
    if override:
        out['filter_status'] = 'passes_rules'
        out['filter_reason'] = f'override: {override}'
        return out

    excl = _matches_any(title, f.get('exclude_keywords') or [])
    if excl:
        out['filter_status'] = 'filtered_out'
        out['filter_reason'] = f'matched exclude: {excl}'
        return out

    incl = _matches_any(title, f.get('include_keywords') or [])
    if incl:
        out['filter_status'] = 'passes_rules'
        out['filter_reason'] = f'matched include: {incl}'
        return out

    policy = (f.get('ambiguous_policy') or 'pending').lower()
    if policy == 'pending':
        out['filter_status'] = 'passes_rules'
        out['filter_reason'] = 'ambiguous; pending-policy passes through'
        return out

    # 'llm' policy is implemented in classify_all (needs the LLM model spec).
    out['filter_status'] = _NEEDS_LLM
    out['filter_reason'] = 'rules ambiguous'
    return out


def _build_llm_prompt(job: dict[str, Any], profile: dict[str, Any]) -> str:
    role_desc = (profile.get('role_type_description') or '').strip()
    exclusions = profile.get('role_type_exclusions') or []
    exclusions_block = '\n'.join(f'- {e}' for e in exclusions)
    return f"""You classify whether a job title fits a target role type. Output JSON ONLY.

Target role type: {role_desc}

Excluded role types (any of these → exclude):
{exclusions_block}

Job title: {job.get('title', '')}
Company: {job.get('company', '')}
Snippet: {job.get('snippet', '')}

Return EXACTLY this JSON, no prose:
{{"role_type_match": "pass"}}
or
{{"role_type_match": "exclude"}}
""".strip()


def _llm_decide(job: dict[str, Any], cfg: dict[str, Any], profile: dict[str, Any] | None) -> dict[str, Any]:
    """Returns a dict with filter_status, filter_reason, ambiguous_resolved_by."""
    f = cfg.get('filter', {}) or {}
    model = f.get('llm_model') or None
    if not profile or not (profile.get('role_type_description') or '').strip():
        return {
            'filter_status': 'filtered_out',
            'filter_reason': 'llm error: no profile available',
            'ambiguous_resolved_by': None,
        }
    prompt = _build_llm_prompt(job, profile)
    try:
        result = llm_call_json(prompt, model=model)
    except Exception as exc:
        return {
            'filter_status': 'filtered_out',
            'filter_reason': f'llm error: {exc.__class__.__name__}',
            'ambiguous_resolved_by': model,
        }
    decision = (result.get('role_type_match') or '').strip().lower()
    if decision == 'pass':
        return {
            'filter_status': 'passes_rules',
            'filter_reason': 'rules ambiguous; llm: pass',
            'ambiguous_resolved_by': model,
        }
    if decision == 'exclude':
        return {
            'filter_status': 'filtered_out',
            'filter_reason': 'rules ambiguous; llm: exclude',
            'ambiguous_resolved_by': model,
        }
    return {
        'filter_status': 'filtered_out',
        'filter_reason': 'llm output invalid',
        'ambiguous_resolved_by': model,
    }


def classify_all(jobs: list[dict[str, Any]], cfg: dict[str, Any], *, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify a full batch. Dedupes by (company, title) within the batch."""
    seen_keys: set[tuple[str, str]] = set()
    output: list[dict[str, Any]] = []
    passes = 0
    filtered = 0

    for job in jobs:
        key = (_norm(job.get('company', '')), _norm(job.get('title', '')))
        if key in seen_keys and key != ('', ''):
            entry = dict(job)
            entry['filter_status'] = 'duplicate'
            entry['filter_reason'] = 'dedup within run'
            entry['ambiguous_resolved_by'] = None
            output.append(entry)
            continue
        seen_keys.add(key)

        result = classify_one(job, cfg)
        if result.get('filter_status') == _NEEDS_LLM:
            verdict = _llm_decide(job, cfg, profile)
            result.update(verdict)

        output.append(result)
        if result['filter_status'] == 'passes_rules':
            passes += 1
        elif result['filter_status'] == 'filtered_out':
            filtered += 1

    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'input_count': len(jobs),
        'passes_count': passes,
        'filtered_out_count': filtered,
        'jobs': output,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Apply title-level pre-filter to jobs_today.json.')
    p.add_argument('--input', type=str, default='', help='Override jobs_today.json path')
    p.add_argument('--output', type=str, default='', help='Override jobs_filtered.json path')
    return p.parse_args()


def _load_profile(env: dict[str, str], cfg: dict[str, Any]) -> dict[str, Any]:
    from job_scout_lib import profile_source_path
    try:
        import yaml
    except Exception:
        return {}
    path = profile_source_path(env, cfg)
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def main() -> None:
    args = parse_args()
    env = load_env()
    cfg = load_project_config(env)
    in_path = Path(args.input) if args.input else jobs_inbox_json_path(env, cfg)
    out_path = Path(args.output) if args.output else jobs_filtered_json_path(env, cfg)
    payload = read_json(in_path, {'jobs': []})
    jobs = payload.get('jobs', [])
    profile = _load_profile(env, cfg)
    result = classify_all(jobs, cfg, profile=profile)
    history_path, history_log = write_json_snapshot_and_history(env, out_path, result, history_group='jobs_filtered')
    print(f'wrote {out_path} (passes={result["passes_count"]} / filtered={result["filtered_out_count"]} / input={result["input_count"]})')
    print(f'archived snapshot: {history_path}')
    print(f'appended history log: {history_log}')


if __name__ == '__main__':
    main()
