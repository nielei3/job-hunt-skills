#!/usr/bin/env python3
"""Post compact notifications to a Discord channel via the Bot REST API.

Uses `~/.hermes/.env`:
    DISCORD_BOT_TOKEN   (required)
    DISCORD_HOME_CHANNEL    (channel id; may be overridden per-call)
"""
from __future__ import annotations

import json
import ssl
import time
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError

try:
    import certifi

    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()


DISCORD_API = "https://discord.com/api/v10"
MESSAGE_CHAR_LIMIT = 2000


def _post(url: str, body: dict[str, Any], token: str, timeout: int = 15) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bot {token}")
    req.add_header("User-Agent", "interview-question-scout (https://github.com/, v0.1)")
    try:
        with urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Discord API error {e.code}: {body_text}") from e


def send_message(token: str, channel_id: str, content: str, timeout: int = 15) -> dict[str, Any]:
    if len(content) > MESSAGE_CHAR_LIMIT:
        content = content[: MESSAGE_CHAR_LIMIT - 20] + "\n…（截断）"
    return _post(
        f"{DISCORD_API}/channels/{channel_id}/messages",
        {"content": content},
        token,
        timeout=timeout,
    )


def format_thread_message(
    company: str,
    title: str,
    url: str,
    author: str,
    posted_at: str,
    oneliner: str,
    locked: bool,
) -> str:
    lock_badge = " 🔒大米锁" if locked else ""
    author_str = f"作者: {author}" if author else ""
    meta = " · ".join(filter(None, [posted_at, author_str]))
    body = f"🆕 **{company}** · {title}{lock_badge}"
    if meta:
        body += f"\n📅 {meta}"
    if oneliner:
        body += f"\n💡 {oneliner}"
    body += f"\n🔗 {url}"
    return body


def format_run_summary(company: str, new_count: int, locked_count: int, error_count: int) -> str:
    emoji = "✅" if error_count == 0 else "⚠️"
    bits = [f"{emoji} **{company}** interview-question-scout"]
    bits.append(f"new: {new_count}")
    if locked_count:
        bits.append(f"locked: {locked_count}")
    if error_count:
        bits.append(f"errors: {error_count}")
    return " · ".join(bits)


def format_alert(message: str) -> str:
    return f"⚠️ **interview-question-scout**: {message}"


def sleep_for_rate_limit(seconds: float = 1.5) -> None:
    time.sleep(seconds)
