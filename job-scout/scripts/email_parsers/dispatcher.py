"""Email message dispatcher — routes messages to the correct parser by sender."""
from __future__ import annotations

from email.message import EmailMessage
from typing import Any

from job_scout_lib import collapse_ws

from .linkedin_parser import build_jobs_from_linkedin_message
from .jobright_parser import build_jobs_from_jobright_message


def identify_source(msg: EmailMessage) -> str:
    """Return source key: 'linkedin', 'jobright', or 'unknown'."""
    sender = collapse_ws(msg.get('from', '')).lower()
    if 'linkedin' in sender:
        return 'linkedin'
    if 'jobright' in sender:
        return 'jobright'
    return 'unknown'


def dispatch_message(msg: EmailMessage, *, cfg: dict[str, Any] | None = None) -> list[dict]:
    """Dispatch a message to the appropriate parser and return job dicts."""
    source = identify_source(msg)
    if source == 'linkedin':
        return build_jobs_from_linkedin_message(msg, cfg=cfg)
    elif source == 'jobright':
        return build_jobs_from_jobright_message(msg)
    else:
        return []  # unknown sources silently skipped for now
