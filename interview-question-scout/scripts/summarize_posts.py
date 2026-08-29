#!/usr/bin/env python3
"""Summarize a 1point3acres interview thread via an LLM (claude-opus-4.6).

Output is a structured dict suitable for writing to Obsidian and for a
compact one-liner used in Discord notifications.
"""
from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI


SUMMARIZE_PROMPT = """你是帮我整理技术面经的助手。下面是一条从一亩三分地面经板块抓到的帖子，对应公司：{company}。

把它整理成结构化 JSON（UTF-8，键值都用中文），按以下 schema：

{{
  "role_level": "岗位级别（如 SWE / NG / MLE / Research Engineer / Infra / 未注明）",
  "rounds": ["面试流程步骤的有序列表（如 OA → 店面 → VO 4轮）"],
  "questions": ["题目一句话概述的数组（coding/system design/BQ 都算）；若无可写空数组"],
  "difficulty": "一到两个词形容难度（如 中等、偏难、简单）；不确定写 未注明",
  "result": "结果（过 / 挂 / 待定 / 未注明）",
  "key_takeaways": "2~3 句话总结有用信息（给自己准备面试的人看），中文",
  "discord_oneliner": "1 句话的超短摘要，用于 Discord 通知，不要超过 80 个字；中文",
  "tags": ["从正文中抽取的关键词标签，如 coding, system-design, BQ, onsite, 北美 等"]
}}

严格只输出 JSON，不要任何解释、前后缀或 markdown 代码块。若正文信息不足，对应字段写 "未注明" 或空数组。

---
标题：{title}
作者：{author}
发帖时间：{posted_at}
原文列表标签：{listing_tags}

正文：
{body}
---
"""


def build_client(api_key: str, base_url: str) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=base_url)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[…正文超长，已截断…]"


def summarize_thread(
    client: OpenAI,
    model: str,
    company: str,
    title: str,
    author: str,
    posted_at: str,
    listing_tags: list[str],
    body: str,
    max_body_chars: int = 8000,
    timeout: int = 120,
) -> dict[str, Any]:
    prompt = SUMMARIZE_PROMPT.format(
        company=company,
        title=title or "未注明",
        author=author or "未注明",
        posted_at=posted_at or "未注明",
        listing_tags=", ".join(listing_tags) if listing_tags else "（无）",
        body=_truncate(body or "（正文为空）", max_body_chars),
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        timeout=timeout,
    )
    text = (resp.choices[0].message.content or "").strip()
    text = _strip_code_fence(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Last-ditch: salvage the outer {...}
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _strip_code_fence(text: str) -> str:
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def summary_to_markdown_block(
    summary: dict[str, Any],
    title: str,
    url: str,
    author: str,
    posted_at: str,
    listing_tags: list[str],
    fetched_at: str,
    locked_by_dami: bool,
    body_excerpt: str,
) -> str:
    """Render a single thread summary as an appendable markdown section."""
    rounds = summary.get("rounds") or []
    questions = summary.get("questions") or []
    tags = summary.get("tags") or []
    role_level = summary.get("role_level", "未注明")
    difficulty = summary.get("difficulty", "未注明")
    result = summary.get("result", "未注明")
    takeaways = summary.get("key_takeaways", "")

    combined_tags = listing_tags + [t for t in tags if t not in listing_tags]
    tag_str = " ".join(f"`{t}`" for t in combined_tags) if combined_tags else ""

    lines: list[str] = []
    lines.append(f"## {posted_at or '日期未注明'} · [{title}]({url})")
    lines.append(
        f"**抓取**: {fetched_at} · **作者**: {author or '匿名'} · **岗位**: {role_level} · **难度**: {difficulty} · **结果**: {result}"
    )
    if tag_str:
        lines.append(f"**标签**: {tag_str}")
    if locked_by_dami:
        lines.append("> ⚠️ 正文受大米锁保护，下列总结基于可见片段。")
    lines.append("")
    lines.append("### 流程")
    if rounds:
        for r in rounds:
            lines.append(f"- {r}")
    else:
        lines.append("- 未注明")
    lines.append("")
    lines.append("### 题目")
    if questions:
        for i, q in enumerate(questions, 1):
            lines.append(f"{i}. {q}")
    else:
        lines.append("- 未注明")
    lines.append("")
    lines.append("### 要点")
    lines.append(takeaways or "未注明")
    if body_excerpt:
        lines.append("")
        lines.append("### 原文摘录")
        for ln in body_excerpt.splitlines():
            lines.append(f"> {ln}" if ln else ">")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def locked_only_markdown_block(
    title: str,
    url: str,
    author: str,
    posted_at: str,
    listing_tags: list[str],
    fetched_at: str,
) -> str:
    """Minimal block for a 大米-locked thread where we skip spending and skip LLM."""
    tag_str = " ".join(f"`{t}`" for t in listing_tags) if listing_tags else ""
    lines = [
        f"## {posted_at or '日期未注明'} · [{title}]({url})",
        f"**抓取**: {fetched_at} · **作者**: {author or '匿名'}",
    ]
    if tag_str:
        lines.append(f"**标签**: {tag_str}")
    lines.append("> ⚠️ 该帖正文被大米锁保护。按配置策略不消耗大米，仅记录元信息。")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)
