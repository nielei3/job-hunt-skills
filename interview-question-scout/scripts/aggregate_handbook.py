#!/usr/bin/env python3
"""Aggregate collected face-jing posts into a study handbook.

Two-stage LLM `claude-opus-4.6` pipeline:

  Stage 1 (Map):    For each post, extract structured questions.
  Stage 2 (Reduce): Cluster questions across posts; merge descriptions/solutions.
  Stage 3 (Render): Write a Markdown handbook with frequency-ranked question bank.

Inputs (one of):
  --bodies-json PATH   JSON dict {thread_id: {title, op_body, meta_line, page, href, ...}}
  --listings-json PATH listing-only fallback (no body — extracts from titles only; weaker)

Outputs:
  --out PATH               Final Markdown handbook
  --stage1-cache PATH      Per-post extraction cache (default: alongside bodies-json)
  --canonical-json PATH    Final canonical questions JSON (default: next to --out)

Usage:
  python3 aggregate_handbook.py \
    --company OpenAI \
    --bodies-json /tmp/oai_bodies.json \
    --out "$OBSIDIAN_VAULT_PATH/Career/Company/OpenAI/OpenAI-Interviews.md"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from openai import OpenAI

OPENAI_BASE_URL = "https://api.openai.com/v1"
MODEL = "claude-opus-4.6"
BATCH_SIZE = 8
MAX_BODY_CHARS = 10000
RETRIES = 2

STAGE1_PROMPT = """你在帮我把面经帖整理成「面试题数据库」。下面是 {n} 条来自一亩三分地 {company} 面试经验板块的帖子，每条带 thread_id / title / 标签 / 正文。

请对每个帖子抽取它**实际描述的面试题**（一个帖子可能 0、1 或多道题）。**只抽真实出现过的题目**——如果楼主只是在求助、闲聊、问流程，questions 数组就留空。

输出严格 JSON，schema：

{{
  "posts": [
    {{
      "thread_id": "string",
      "questions": [
        {{
          "name": "题目名（短标签，如 'GPU Credit'、'autograd 反向传播'、'Design AI Chatbot'）",
          "type": "coding | sd | ml-coding | prompt | math | behavioral",
          "what": "题面（1-3 句中文，包含具体函数签名/约束/输入输出，越具体越好）",
          "solution_hint": "楼主提到的解法或思路；没提就写 ''",
          "follow_ups": "follow-up 问题；没提就写 ''",
          "examination_points": ["楼主提到或可推断的考查点，如 'BFS/多源BFS'、'FIFO数据结构'、'lazy evaluation'。每个考查点一个字符串。没有就给空数组 []"],
          "round": "面试场次（如 '60min 店面'、'75min ML coding'、'onsite SD'），不明写 '未注明'",
          "role": "岗位（如 'MLE'、'SWE General'、'Researcher'），不明写 '未注明'"
        }}
      ]
    }}
  ]
}}

规则：
- name 用最简短可识别的标签，方便跨帖去重。"GPU credit II" 和 "credit II" 都写 "GPU Credit"。
- 不要瞎编。`what` 必须有正文支持，没具体题面就别捏造。
- 楼主在引用 HR 给的 prompt 描述（如 "Coding exercises will ask you to implement components..."），那是宣传文不是题，questions 数组就留空。
- 一个帖子可能既有 coding 又有 SD —— 都要抽。
- type 字段：纯算法/数据结构 → coding；系统设计 → sd；ML/PyTorch/autograd → ml-coding；写 prompt 让模型完成任务 → prompt；数学/推导 → math；BQ/HR → behavioral。

帖子：
---
{posts_block}
---

只输出 JSON，不要 markdown 代码块、不要解释。"""


STAGE2_PROMPT = """你拿到一份从 {n_posts} 条 {company} 面经里抽出的所有题目（共 {n_qs} 条记录），现在要把它们**聚合成「题库」**：合并近似题、统计频次、合写题面/解法。

输入是数组，每条形如：
{{thread_id, name, type, what, solution_hint, follow_ups, round, role}}

输出严格 JSON：

{{
  "canonical_questions": [
    {{
      "id": "q01",
      "name": "标准题名（取最常见且具体的写法，如 'GPU Credit'）",
      "aliases": ["合并进来的其它写法"],
      "type": "coding | sd | ml-coding | prompt | math | behavioral",
      "frequency": 9,
      "thread_ids": ["1174310", "1171972", ...],
      "description": "综合所有帖子的题面，写一段 100-200 字的中文描述。",
      "common_solutions": "综合所有帖子的解法/思路，~80-150 字。",
      "common_pitfalls": "踩坑点 / 常见误区，~50 字；没有就写 ''。",
      "follow_ups": "follow-up 题 / 进阶要求，~50-100 字；没有就写 ''。",
      "examination_points": ["合并所有帖子提到的考查点（去重），如 'BFS/多源BFS'、'时间复杂度分析'、'边界条件处理'。每个考查点一个字符串。"],
      "round": "最常见的场次（如 '60min 店面'）",
      "difficulty": "中等 | 偏难 | 简单 | 未注明",
      "source_quality": "high | medium | low（见下方规则）"
    }}
  ]
}}

【聚合规则】
- 相似题合并为一条：'GPU credit' / 'GPU credit II' / 'credit II' → 同一题；'传染病模拟' / 'infection spread' / 'Cellular Automata 疫情' → 同一题。
- 频次 = thread_ids 列表长度。求助帖只提了题名但没考过的，**不算**。
- 按 frequency 降序排序。frequency = 1 的也要列出。

【**严禁瞎编**——这条最重要】
- description / common_solutions 必须**有 thread_ids 中至少一个帖子的输入支持**。允许做语言润色和概括，但**不能从无生有**。
- 如果输入对这道题只有题名、没有具体题面/解法，**就直说**：description 写 "楼主仅提及题名，未公开具体题面/约束。建议直接查阅 thread_ids 中的原帖与楼下回复。" common_solutions 写 ''。
- 这种情况下，`source_quality: "low"`。
- 如果输入有题名 + 简短描述但没解法，description 综合输入即可，common_solutions 写 ''；`source_quality: "medium"`。
- 如果输入有完整题面 + 解法/follow-up，正常合写；`source_quality: "high"`。
- **不要为了让答案显得"完整"而补全实际不存在的细节**——读者看了会被误导，反而比"未注明"更糟。

【其它】
- 如果同一题既出现在 coding 又出现在 sd，归到出现更多的那一类。
- 如果同一题被楼主同时引用为多个 type（例如把 "Persistent KV Store" 标为 coding，但另一帖标 sd），按多数原则归类。

输入题目列表：
---
{questions_json}
---

只输出 JSON，不要 markdown 代码块、不要解释。"""


def build_client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        env_file = Path.home() / ".hermes" / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("OPENAI_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break
    if not api_key:
        # Fallback: use OPENAI_API_KEY with the configured base URL
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            env_file = Path.home() / ".hermes" / ".env"
            if env_file.exists():
                for line in env_file.read_text().splitlines():
                    if line.startswith("OPENAI_API_KEY="):
                        api_key = line.split("=", 1)[1].strip()
                        break
    if not api_key:
        sys.exit("OPENAI_API_KEY missing (env or ~/.hermes/.env)")
    return OpenAI(api_key=api_key, base_url=OPENAI_BASE_URL)


def trim(text: str, n: int = MAX_BODY_CHARS) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[:n] + " …(truncated)"


def call_llm(client: OpenAI, prompt: str, label: str, timeout: int = 900) -> str:
    """Stream the response. Streaming keeps the HTTP connection active, which
    avoids the API gateway's nginx 502 Bad Gateway when generation runs > ~300s. Without
    streaming, large outputs (>20KB) reliably trip the gateway timeout."""
    last_err = None
    for attempt in range(RETRIES + 1):
        try:
            stream = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                timeout=timeout,
                stream=True,
            )
            chunks = []
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    chunks.append(delta.content)
            return "".join(chunks)
        except Exception as e:  # noqa: BLE001
            last_err = e
            err_summary = str(e)[:200].replace("\n", " ")
            print(f"  [{label}] retry {attempt + 1}: {err_summary}", file=sys.stderr)
            time.sleep(2 + attempt * 2)
    raise RuntimeError(f"LLM call failed after {RETRIES + 1} tries: {last_err}")


def parse_json(text: str) -> Any:
    """Robust JSON parsing for LLM output.

    Handles: code-fence wrapping, leading/trailing prose, trailing commas,
    and (via json_repair) unescaped quotes inside string values.
    """
    raw = text
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    first = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if first > 0:
        text = text[first:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Trailing-comma repair
    repaired = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    # Heavy repair via json_repair (handles unescaped inner quotes, newlines, etc.)
    try:
        import json_repair  # type: ignore
        return json_repair.loads(text)
    except Exception:
        pass
    # Last resort: save raw and raise
    debug_path = Path("/tmp/aggregate_handbook_bad_json.txt")
    debug_path.write_text(raw, encoding="utf-8")
    raise json.JSONDecodeError(
        f"all parsers failed (raw saved to {debug_path})", text, 0
    )


def stage1_extract(client: OpenAI, posts: list[dict], company: str, cache_path: Path) -> dict:
    """Run per-post extraction, batched. Cache progressive results."""
    cache: dict = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text())
        print(f"Stage1 resume: {len(cache)} posts already extracted", file=sys.stderr)

    pending = [p for p in posts if p["thread_id"] not in cache]
    print(f"Stage1: {len(pending)} posts to process in batches of {BATCH_SIZE}", file=sys.stderr)

    for i in range(0, len(pending), BATCH_SIZE):
        batch = pending[i : i + BATCH_SIZE]
        block_lines = []
        for p in batch:
            block_lines.append(f"\n## thread_id: {p['thread_id']}")
            block_lines.append(f"title: {p.get('title', '')}")
            block_lines.append(f"标签: {p.get('tag_line', '') or p.get('meta_line', '')}")
            block_lines.append(f"正文: {trim(p.get('op_body', ''))}")
        prompt = STAGE1_PROMPT.format(
            n=len(batch),
            company=company,
            posts_block="\n".join(block_lines),
        )
        label = f"stage1 batch {i // BATCH_SIZE + 1}/{(len(pending) + BATCH_SIZE - 1) // BATCH_SIZE}"
        try:
            raw = call_llm(client, prompt, label)
            parsed = parse_json(raw)
            for entry in parsed.get("posts", []):
                cache[entry["thread_id"]] = entry
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
            print(f"  {label}: {len(parsed.get('posts', []))} posts saved (total cached {len(cache)})", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"  {label} FAILED: {e}", file=sys.stderr)
            for p in batch:
                cache[p["thread_id"]] = {"thread_id": p["thread_id"], "questions": [], "error": str(e)[:200]}
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2))

    return cache


def stage2_canonicalize(client: OpenAI, stage1: dict, company: str, n_posts: int) -> dict:
    """Cluster + synthesize per type to keep each LLM call under the API gateway's limit.

    A single 272-question call returns 30+KB and triggers 502 Bad Gateway from
    the API gateway's nginx. Splitting by type yields 30-100 questions per call (~5-10KB
    output) which stays under the gateway timeout reliably.
    """
    flat = []
    for tid, entry in stage1.items():
        for q in entry.get("questions", []):
            q2 = dict(q)
            q2["thread_id"] = tid
            flat.append(q2)
    print(f"Stage2: {len(flat)} raw questions -> per-type canonical clusters", file=sys.stderr)
    if not flat:
        return {"canonical_questions": []}

    # Bucket by type
    by_type: dict[str, list[dict]] = defaultdict(list)
    for q in flat:
        t = q.get("type", "other") or "other"
        by_type[t].append(q)

    all_canon: list[dict] = []
    next_id = 1
    for typ in sorted(by_type, key=lambda x: -len(by_type[x])):
        bucket = by_type[typ]
        print(f"  stage2 type={typ}: {len(bucket)} raw questions", file=sys.stderr)
        prompt = STAGE2_PROMPT.format(
            n_posts=n_posts,
            n_qs=len(bucket),
            company=company,
            questions_json=json.dumps(bucket, ensure_ascii=False, indent=1),
        )
        # LLM output is non-deterministic: a batch can come back with unescaped
        # quotes/newlines that only json_repair can salvage, and json_repair may
        # silently truncate to a couple of items. Retry the call and keep the
        # parse that yields the most questions; require a plausible count before
        # accepting early so a truncated repair doesn't quietly drop data.
        STAGE2_TRIES = 4
        best: list[dict] = []
        for attempt in range(STAGE2_TRIES):
            raw = call_llm(client, prompt, f"stage2 {typ} try{attempt + 1}")
            try:
                parsed = parse_json(raw)
            except Exception as e:  # noqa: BLE001
                print(f"    parse failed (try {attempt + 1}): {str(e)[:120]}", file=sys.stderr)
                continue
            if isinstance(parsed, list):
                parsed = {"canonical_questions": parsed}
            got = parsed.get("canonical_questions", []) or []
            # Rough lower bound on how many clusters the raw text actually held.
            expected = raw.count('"name"')
            if len(got) > len(best):
                best = got
            if got and (expected == 0 or len(got) >= max(1, int(expected * 0.7))):
                break
            print(
                f"    try {attempt + 1}: parsed {len(got)} but raw suggests ~{expected}; retrying",
                file=sys.stderr,
            )
        for q in best:
            q["id"] = f"q{next_id:03d}"
            q["type"] = q.get("type") or typ
            next_id += 1
            all_canon.append(q)
        print(f"    -> {len(best)} canonical questions", file=sys.stderr)

    return {"canonical_questions": all_canon}


TYPE_LABELS = {
    "coding": "Coding",
    "sd": "System Design",
    "ml-coding": "ML Coding",
    "prompt": "Prompt Coding",
    "math": "Math / Reasoning",
    "behavioral": "Behavioral / 流程",
}


def render_handbook(canon: dict, listings: list[dict], stage1: dict, company: str, n_posts: int, leaderboard_min_freq: int = 2) -> str:
    qs = canon.get("canonical_questions", [])
    qs.sort(key=lambda x: (-int(x.get("frequency", 0)), x.get("name", "")))

    today = date.today().isoformat()
    out = []
    out.append(f"# {company} 面试复习手册\n\n")
    out.append(f"> **来源**：[1point3acres {company} 面试经验](https://jobs.1point3acres.com/companies/{company.lower()}/interview)\n")
    out.append(f"> **数据范围**：{n_posts} 帖（前 20 页，约 2025–2026）\n")
    out.append(f"> **抓取日期**：{today}\n")
    out.append(f"> **聚合后题目数**：{len(qs)}\n\n")
    out.append("---\n\n")

    # Frequency leaderboard
    top = [q for q in qs if int(q.get("frequency", 0)) >= leaderboard_min_freq][:50]
    if leaderboard_min_freq <= 1:
        out.append(f"## 速览：题目总览（全部 {len(top)} 题，按频次降序）\n\n")
    else:
        out.append(f"## 速览：高频题榜（≥{leaderboard_min_freq} 次）\n\n")
    if top:
        out.append("| # | 题目 | 类型 | 频次 | 难度 | 主要场次 |\n|---|---|---|---|---|---|\n")
        for i, q in enumerate(top, 1):
            t = TYPE_LABELS.get(q.get("type", ""), q.get("type", "?"))
            out.append(f"| {i} | **{q['name']}** | {t} | {q.get('frequency')} | {q.get('difficulty', '未注明')} | {q.get('round', '未注明')} |\n")
        out.append("\n")
    else:
        out.append(f"（暂无频次 ≥{leaderboard_min_freq} 的题——数据可能太少）\n\n")
    out.append("---\n\n")

    # Flat question list: each question is H2, grouped by type then frequency desc
    grouped: dict[str, list[dict]] = defaultdict(list)
    for q in qs:
        grouped[q.get("type", "other")].append(q)

    section_order = ["coding", "sd", "ml-coding", "prompt", "math", "behavioral"]
    for typ in section_order:
        items = grouped.get(typ, [])
        if not items:
            continue
        for q in items:
            freq = int(q.get("frequency", 0))
            type_label = TYPE_LABELS.get(q.get("type", ""), q.get("type", "?"))
            heading = f"## {q['name']} _(频次 {freq} · {type_label}"
            if q.get("round") and q["round"] != "未注明":
                heading += f" · {q['round']}"
            heading += ")_\n\n"
            out.append(heading)
            if q.get("aliases"):
                aliases = [a for a in q["aliases"] if a != q["name"]]
                if aliases:
                    out.append(f"**别名**：{', '.join(aliases)}\n\n")
            # 考查点 (examination points) as bullet list
            exam_pts = q.get("examination_points") or []
            if exam_pts:
                out.append("**考查点**：\n")
                for pt in exam_pts:
                    out.append(f"- {pt}\n")
                out.append("\n")
            sq = q.get("source_quality", "")
            sq_badge = {"high": "", "medium": " _(题面综合自有限描述)_", "low": " ⚠️ _(楼主未给题面，仅题名记录)_"}.get(sq, "")
            if q.get("description"):
                out.append(f"**题面**{sq_badge}：{q['description']}\n\n")
            if q.get("common_solutions"):
                out.append(f"**主要解法**：{q['common_solutions']}\n\n")
            if q.get("common_pitfalls"):
                out.append(f"**踩坑**：{q['common_pitfalls']}\n\n")
            if q.get("follow_ups"):
                out.append(f"**Follow-up**：{q['follow_ups']}\n\n")
            tids = q.get("thread_ids", []) or []
            if tids:
                lookup = {p["thread_id"]: p for p in listings}
                links = []
                for tid in tids[:8]:
                    pl = lookup.get(tid)
                    if pl:
                        links.append(f"[#{tid}]({pl['href']})")
                    else:
                        links.append(f"[#{tid}](https://www.1point3acres.com/bbs/thread-{tid}-1-1.html)")
                more = f" + {len(tids) - 8} 帖" if len(tids) > 8 else ""
                out.append(f"**来源**：{' · '.join(links)}{more}\n\n")
            out.append("---\n\n")

    # Single-occurrence appendix (skip when leaderboard already includes freq=1)
    rare = [q for q in qs if int(q.get("frequency", 0)) == 1] if leaderboard_min_freq > 1 else []
    if rare:
        out.append(f"## 罕见题（频次 = 1，共 {len(rare)} 道）\n\n")
        out.append("_仅出现过一次，复习时可略过；准备充分后再扫一遍。_\n\n")
        for q in rare:
            t = TYPE_LABELS.get(q.get("type", ""), "?")
            tid = (q.get("thread_ids") or [""])[0]
            lookup = {p["thread_id"]: p for p in listings}
            link_url = lookup.get(tid, {}).get("href", f"https://www.1point3acres.com/bbs/thread-{tid}-1-1.html")
            desc = (q.get("description") or "").strip()
            if len(desc) > 120:
                desc = desc[:120] + "…"
            out.append(f"- **[{q['name']}]({link_url})** _{t}_ — {desc}\n")
        out.append("\n")

    # Meta
    out.append("---\n\n")
    out.append("## 元信息\n\n")
    err_count = sum(1 for v in stage1.values() if v.get("error"))
    out.append(f"- 抓取脚本：`career/interview-question-scout/scripts/aggregate_handbook.py`\n")
    out.append(f"- 模型：`{MODEL}`（两阶段：map + reduce）\n")
    out.append(f"- 帖子总数：{n_posts}\n")
    out.append(f"- 抽出原始题数：{sum(len(v.get('questions', [])) for v in stage1.values())}\n")
    out.append(f"- 聚合后不同题数：{len(qs)}\n")
    if err_count:
        out.append(f"- ⚠️ Stage1 LLM 失败：{err_count} 帖\n")
    return "".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", required=True, help="Display name, e.g. OpenAI")
    ap.add_argument("--slug", default=None, help="Company slug (defaults to lowercase --company)")
    ap.add_argument("--bodies-json", default=None, help="Path to bodies JSON (auto-discovered from scout data if omitted)")
    ap.add_argument("--listings-json", default=None, help="Optional listings JSON for richer source links")
    ap.add_argument("--out", default=None, help="Output Markdown path (default: Obsidian Company/<Company>-Interviews.md)")
    ap.add_argument("--stage1-cache", default=None, help="Stage1 extraction cache JSON")
    ap.add_argument("--canonical-json", default=None, help="Canonical questions JSON output")
    ap.add_argument("--skip-stage1", action="store_true", help="Reuse cached stage1, no LLM calls for extraction")
    ap.add_argument("--skip-stage2", action="store_true", help="Reuse cached canonical, only re-render")
    ap.add_argument("--leaderboard-min-freq", type=int, default=2, help="Minimum frequency for leaderboard inclusion (default 2). Set to 1 for small datasets — also drops the rare-questions appendix.")
    args = ap.parse_args()

    slug = args.slug or args.company.lower()

    if args.bodies_json:
        bodies_path = Path(args.bodies_json)
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from interview_question_scout_lib import load_env, bodies_json_path
        env = load_env()
        bodies_path = bodies_json_path(env, slug)
        if not args.out:
            vault = env["OBSIDIAN_VAULT_PATH"]
            company_dir = Path(vault) / "Career" / "Company" / args.company
            company_dir.mkdir(parents=True, exist_ok=True)
            args.out = str(company_dir / f"{args.company}-Interviews.md")
    if not bodies_path.exists():
        sys.exit(f"bodies JSON not found: {bodies_path}")
    bodies = json.loads(bodies_path.read_text())

    if args.listings_json:
        listings = json.loads(Path(args.listings_json).read_text())
    else:
        listings = [{"thread_id": tid, "href": v.get("href") or f"https://www.1point3acres.com/bbs/thread-{tid}-1-1.html", "page": v.get("page"), "title": v.get("title", "")} for tid, v in bodies.items()]

    # Build the input list for stage 1
    posts = []
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

    out_path = Path(args.out)
    cache_path = Path(args.stage1_cache or out_path.with_suffix(".stage1.json"))
    canon_path = Path(args.canonical_json or out_path.with_suffix(".canonical.json"))

    client = build_client()

    if args.skip_stage1:
        stage1 = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    else:
        stage1 = stage1_extract(client, posts, args.company, cache_path)

    if args.skip_stage2 and canon_path.exists():
        canon = json.loads(canon_path.read_text())
    else:
        canon = stage2_canonicalize(client, stage1, args.company, len(posts))
        canon_path.write_text(json.dumps(canon, ensure_ascii=False, indent=2))
        print(f"Wrote {canon_path}", file=sys.stderr)

    handbook = render_handbook(canon, listings, stage1, args.company, len(posts), leaderboard_min_freq=args.leaderboard_min_freq)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(handbook, encoding="utf-8")
    print(f"Wrote {out_path} ({len(handbook)} chars)")


if __name__ == "__main__":
    main()
