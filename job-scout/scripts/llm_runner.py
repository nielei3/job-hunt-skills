#!/usr/bin/env python3
"""Thin wrapper around `hermes chat -Q` so tests can monkey-patch the runner."""
from __future__ import annotations

import json
import subprocess
from typing import Any


def call_json(prompt: str, *, model: str | None = None, timeout: int = 120) -> dict[str, Any]:
    """Run `hermes chat -Q -q <prompt>` and return the first JSON object found in stdout."""
    cmd = ['hermes', 'chat', '-Q', '-q', prompt]
    if model:
        cmd.extend(['-m', model])
    proc = subprocess.run(cmd, text=True, capture_output=True, check=True, timeout=timeout)
    text = (proc.stdout or '').strip()
    start = text.find('{')
    end = text.rfind('}')
    if start < 0 or end < start:
        raise ValueError(f'No JSON object in LLM output: {text[:500]!r}')
    raw = text[start:end + 1]
    # Handle case where LLM returns multiple JSON objects — parse only the first one.
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to find just the first complete JSON object via decoder
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(raw)
        return obj


def call_text(prompt: str, *, model: str | None = None, timeout: int = 180) -> str:
    """Run `hermes chat -Q -q <prompt>` and return raw stdout."""
    cmd = ['hermes', 'chat', '-Q', '-q', prompt]
    if model:
        cmd.extend(['-m', model])
    proc = subprocess.run(cmd, text=True, capture_output=True, check=True, timeout=timeout)
    return (proc.stdout or '').strip()
