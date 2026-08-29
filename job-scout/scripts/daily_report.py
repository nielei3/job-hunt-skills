#!/usr/bin/env python3
"""Render the daily job-scout report."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DailyReportInput:
    report_date: str
    raw_count: int
    unique_count: int
    filtered_out: list[dict[str, Any]] = field(default_factory=list)
    passes_count: int = 0
    resolved_count: int = 0
    awaiting_jd: list[dict[str, Any]] = field(default_factory=list)
    scored_today: list[dict[str, Any]] = field(default_factory=list)


def render(p: DailyReportInput) -> str:
    awaiting_lines = '\n'.join(
        f'- [[Pending JDs#{j.get("company","")} — {j.get("title","")}]]'
        for j in p.awaiting_jd
    ) or '_No new jobs awaiting JD this run._'
    scored_lines = '\n'.join(
        f'- [[Opportunities/{j["opportunity_filename"][:-3]}]] — {j.get("score","?")}, {j.get("verdict","?")}'
        for j in p.scored_today
    ) or '_No jobs scored this run._'
    filtered_lines = '\n'.join(
        f'- {j.get("company","")} — {j.get("title","")} ({j.get("filter_reason","")})'
        for j in p.filtered_out
    ) or '_None._'
    return f"""# Job Report — {p.report_date}

## Pipeline Summary

| Metric | Count |
|---|---|
| Raw jobs from LinkedIn alerts | {p.raw_count} |
| After dedupe (unique roles) | {p.unique_count} |
| Filtered out (rules + LLM) | {len(p.filtered_out)} |
| Passing → Pending Inbox | {p.passes_count} |
| Resolved by ATS resolver | {p.resolved_count} |
| Awaiting JD (in Pending JDs.md) | {len(p.awaiting_jd)} |
| Scored today (incl. manual JD fills) | {len(p.scored_today)} |

## Awaiting your JD ({len(p.awaiting_jd)})

{awaiting_lines}

## Scored today ({len(p.scored_today)})

{scored_lines}

## Filtered out ({len(p.filtered_out)})

<details>
<summary>Click to expand</summary>

{filtered_lines}

</details>
"""


def write(path: Path, payload: DailyReportInput) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(payload))
