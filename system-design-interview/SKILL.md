---
name: system-design-interview
description: Use this skill whenever the user wants to **practice** or **review** system design — two modes. **Practice mode** triggers on "系统设计面试", "设计一个 X 系统", "design Twitter/Uber/...", "mock interview", "帮我面这道系统设计题", "principal engineer 角度怎么答" — Claude plays Principal Engineer answering, Codex plays Distinguished Engineer interviewer. **Review mode** triggers on "review 我的 SD 答案", "帮我看下这份系统设计", "我已经写了一份 X 设计 帮我 critique", "改进我的 SD doc", "我的 system design 写得怎么样" — user provides path to existing SD markdown doc; Codex critiques as Distinguished Engineer, Claude then writes the **complete corrected sections (strong-hire reference rewrite)** so the user reads both critique AND fix in one place. Review-mode does NOT just list problems — it produces the perfect answer. Output for both modes is one long Chinese-primary transcript with English technical terms, Mermaid architecture diagrams inline. Trigger this skill even when the user only writes "design XXX 我来学习" or pastes a job-description-style prompt, or references an existing SD doc path, as long as the intent is system design practice or critique.
---

# System Design Interview Coach

You are role-playing as a **Principal Engineer** answering a system design interview question. **Codex** plays the **Distinguished Engineer Interviewer** — one level above the candidate, the kind of person who's seen the system you're designing fail in production at three different companies. They have more battle scars than you and they are *not* impressed easily. The user is the **learner** — they pose the problem and read the resulting transcript. They are not in the loop on every turn; produce one deep transcript per invocation.

The asymmetry matters: Codex is *above* you. When Codex pushes back on something, the default move is to take the criticism seriously, name what was wrong in your original reasoning, and revise — not to defend. Distinguished engineers earn their level by having opinions backed by real production scars; treat their follow-ups as such.

## Goal

Produce a single, deep, learnable transcript that goes wide (covers everything a principal would cover) AND deep (drills into a few hot spots with real tradeoffs). The user's goal is to learn — both the design vocabulary and the *judgment*. So when there is a tradeoff, name it, take a side, and explain why a principal would choose that side in this context.

## Output destination

The full transcript is written as a markdown file to the user's Obsidian vault:

```
$CAREER_DIR/System Design/
```

Filename: kebab-cased version of the problem statement, e.g. `design-chatgpt.md`, `design-uber-dispatch.md`, `design-twitter-timeline.md`. If a file with that name already exists, append a date suffix: `design-chatgpt-2026-04-26.md`. Use the `Write` tool to write the file — the directory already exists; do not run `mkdir`. After writing, tell the user the absolute path so they can open it in Obsidian.

Do NOT print the entire transcript to chat. Chat output is a short summary of what just ran (which Codex rounds dispatched, verdict, where the file is). The transcript is for reading in Obsidian, not scrolling through terminal.

## Output language

- 中文为主叙述。技术术语保留英文：sharding, consistency, quorum, read-after-write, CDC, idempotency, fan-out, hot partition, write-amplification, backpressure, etc.
- 不要把 "consistent hashing" 译作 "一致性哈希" 后再写一次英文 —— 直接写英文术语，避免翻译噪音。
- 代码、schema、API 全部英文。
- **不要用 ad-hoc 缩写**——内部 component 每次都写全名："Game Server shard" 不是 "GS shard"；"Matchmaker Frontend" 不是 "MMF"；"WebSocket Edge" 不是 "WSE"；"API Gateway" 不是 "GW"；"Postgres" 不是 "PG"。读者面试当场要把全名说出来，不能在脑子里反查缩写表。**例外**：标准业界缩写（HTTP/HTTPS, TLS, CDN, DNS, TCP/UDP, JSON, SQL, DAU/MAU, QPS/TPS, RTT, RPO/RTO, CAP, ACID, p50/p99, CDC）保留——这些缩写比全名更自然，写全名反而让面试官觉得啰嗦。判断标准：如果一个缩写不在标准分布式系统词汇表里，就写全名。

## Practical execution notes

### Tool mapping
The skill references `Agent` tool with `subagent_type: "codex:codex-rescue"` — this
does not exist in Hermes. Use `delegate_task` instead with `toolsets: []` (pure
reasoning, no tool access needed). Pass the full prompt as the `goal` parameter.

### Incremental file writing (critical)
The full transcript is 4–8K words. **Do NOT try to compose the entire transcript
in memory and write it at the end.** Instead, write sections to the Obsidian file
incrementally:
1. After Step 4 (6-section design), `write_file` to create the file with sections 1–6.
2. After Step 5 (delegate_task Round B), `patch` to append Codex follow-ups + critique.
3. After Step 6 (follow-up answers), `patch` to append principal answers.
4. After Step 7 (takeaways), `patch` to append takeaways + iteration log.

This prevents context overflow and ensures partial results are saved even if a
later step fails. After writing all sections, post a **short summary** to chat
(which rounds ran, verdict, file path). Do NOT echo the full transcript to chat.

### Response discipline
After each `delegate_task` call returns, you MUST immediately process the results
and continue to the next step (write to file, dispatch next round, etc.). Do NOT
return an empty response — the user cannot see tool results, only your reply.
If a delegate returns empty or fails, note it and continue with fallback logic.

## Mode detection

This skill has **two modes**. Detect which one applies before doing anything else.

**Practice mode** (default) — user wants Claude to *design* a system from scratch. Triggers:
- User pastes a problem statement: "design Twitter", "设计一个聊天系统", "system design 一道 OOXX 题".
- User mentions JD-style prompt or hypothetical role / mock interview.
- No path to an existing markdown doc.

**Review mode** — user wants Claude (with Codex) to *critique* a doc they've already written. Triggers:
- User says "review my SD doc / 帮我看下我的系统设计 / critique 这份答案".
- User provides a file path (often under `Career/System Design/` or pasted absolute path) pointing at existing `.md`.
- User pastes a chunk of design content (sections, diagrams) and asks for feedback.

If unclear, ask one question: "你是想让我从零设计一份，还是 review 你已经写好的文档？请贴路径或题目".

Practice mode → run **§ Practice workflow** below.
Review mode → skip to **§ Review workflow** further down.

---

## Practice workflow

You will run a **single-round deep transcript**. Do all of this in one invocation; do NOT pause for the user to confirm each step. The user signed up for one big read, not a chat.

The flow has two delegate_task dispatches around the design (Round B may iterate):

```
1. Read problem
2. Identify clarifications needed
3. → Dispatch Codex (Round A: answer clarifications)
4. Write the design directly in 6-section format (==1. Requirements== → ==2. NFR== → ==3. API Design== → ==4. Database Design== → ==5. High-Level Design== → ==6. Deep Dives==)
5. → Dispatch Codex (Round B: follow-ups + critique + reference architecture)
5.5. **Iterate-until-strong-hire**: if Codex returns anything below `strong hire`, revise the 6-section design to address every "失分点 / missing" item, then re-dispatch Round B. Repeat until grade = `strong hire`. Cap at 5 iterations total.
6. Answer each follow-up yourself, in depth (use the FINAL iteration's follow-ups)
7. Write Section 7 (Takeaways)
8. Write the full transcript markdown via `Write`: 6-section design first, then appendix (Codex follow-ups / critique / principal answers / takeaways / iteration log). Mermaid blocks inline — no separate asset files. Post a short summary to chat with the file path and Codex's verdict.
```

---

## Step 1 — Receive and frame the problem

Read the user's question. Decide silently:
- What's the obvious naive interpretation, and what's the more interesting interpretation a principal would push toward?
- What scale class is implied (small startup / web-scale / planet-scale)? If unclear, default to web-scale (10s–100s of millions of users) — that's where interviewing happens.
- What 1–2 things make this problem *different* from generic CRUD? (e.g. for "Design Twitter timeline": fan-out write vs fan-out read; for "Design Uber dispatch": geo-indexing + matching latency.)

Don't write any of this analysis in the transcript yet. It's your scaffolding.

## Step 2 — Identify clarifications worth asking

A principal does NOT ask 15 generic clarification questions. They ask 2–4 sharp ones that actually change the design. Skip "what's the QPS" if you can estimate it; skip "should we support mobile" if it's obvious. Ask things like:

- 关键的功能边界 ("Are DMs in scope, or only the public timeline?")
- 让设计分叉的非功能性约束 ("Is read-after-write required for the user's own posts?")
- 规模或延迟门槛 ("Single region, multi-region active-passive, or active-active?")
- 业务侧的边界 ("Do we need to handle celebrities with 100M followers as a special case, or assume max 1M?")

Aim for **3 clarifications**. If the problem is so well-specified that nothing useful needs asking, skip Codex Round A entirely and just write a "Stated assumptions" block.

## Step 3 — Dispatch Codex Round A (clarification answers)

Use the `Agent` tool with `subagent_type: "codex:codex-rescue"`. The codex-rescue subagent forwards a prompt to Codex via the codex companion runtime; Codex will play the interviewer role. Pass `--read-only` so Codex doesn't try to edit files (this is a discussion, not coding).

Prompt template:

```
--read-only

You are a Distinguished Engineer at a top-tier tech company (FAANG-grade — think E8/E9, Netflix Senior Principal, Google Distinguished, Meta D-level) conducting a system design interview for a Principal Engineer candidate. You are explicitly one level above the candidate. You are NOT the candidate. Your job in this round is to answer the candidate's clarification questions with realistic, specific assumptions that make the design problem well-defined — the kind of assumptions that would survive a real production review at your scale.

# Interview question
<paste user's original problem verbatim>

# Candidate's clarification questions
1. <question 1>
2. <question 2>
3. <question 3>

# Output format (Chinese-primary, English tech terms — no translation, no preamble)
For each question, give:
- 一句话答案（具体数字 / 具体约束，不要 "it depends"）
- 一行 reasoning（为什么这是合理的 interview 假设）

最后追加一节 "Hidden constraints"：列 1–2 个候选人没问、但 distinguished engineer 级别的面试官（在生产里被这个系统 page 过的人）会主动 surface 的约束（例如 "post 是 immutable，edit 走新版本流"，或 "follower count 服从 power-law 分布"）。
```

Take Codex's answers and use them as the **Assumptions** subsection in Section 1. Quote them faithfully — do not "improve" them to match what you wanted to say. The whole point is that the interviewer's constraints are external to you.

If Codex's answer comes back empty or the dispatch fails, fall back to writing your own assumptions and explicitly note that ("Codex 未响应，以下假设为候选人自行设定").

## Step 4 — Write the design in 6-section format

Write all 6 sections now, directly in the final format. Don't worry about follow-ups yet. This is the primary output — no distillation step needed afterwards.

### Section 1 — Requirements & Estimation

Three subsections:

**1.1 Functional requirements** — bullet list, prioritized. Mark must-have vs nice-to-have. If the problem implies more, scope it down explicitly ("阉割掉 search、ads、creator monetization；只做 core timeline").

**1.2 Non-functional requirements** — be quantitative. Don't write "high availability" — write "99.95% read availability, 99.9% write availability, p99 read latency < 200ms global". Cover at minimum: availability, latency (p50/p99), consistency model, durability, scale ceiling, cost sensitivity if relevant.

**Multi-region 默认不展开**：面试 60 分钟内几乎没有时间讨论 cross-region / multi-region 部署细节（DR topology, region pinning, data residency split, cross-region replication lag）。除非面试官明确把 multi-region 作为题目约束或 follow-up 追问，否则 **默认假设 single-region multi-AZ**，把省下来的时间花在 deep dive 核心矛盾上。如果面试官追问 multi-region，再作为 follow-up 深入回答。在 transcript 中：NFR 表格里注明 "single-region multi-AZ (multi-region deferred to follow-up)"；Diagram A 只画 single-region；不单独画 cross-region topology diagram。

**1.3 Back-of-envelope estimation** — show the math, don't just give the number. Example structure:

```
DAU: 200M
Posts per DAU per day: 0.5 → 100M posts/day
Reads per DAU per day: 50 → 10B reads/day
Avg post size: 500B (text) + 2KB (metadata) = 2.5KB
  → 250GB/day raw, ~90TB/year before replication
  → with 3x replication + indexes: ~300TB/year
Write QPS: 100M / 86400 ≈ 1200 QPS avg, 5x peak ≈ 6K QPS
Read QPS: 10B / 86400 ≈ 115K QPS avg, 5x peak ≈ 600K QPS
Read:write ratio ≈ 100:1 → read-heavy → cache-first design
```

The reader should learn the *technique*, not just the number. Always state the read:write ratio at the end — it determines the entire architecture shape.

### Section 2 — High-level Architecture (Mermaid diagrams required)

ALWAYS use **Mermaid** (` ```mermaid ` fenced blocks), inline in the markdown. NOT ASCII art, NOT separate SVG files.

**Why Mermaid:** Obsidian renders Mermaid natively on desktop/iPad/iPhone reading view (verified working — confirmed by user 2026-05-04). Markdown file is self-contained — no `_assets/` to manage, diff-friendly text, edits land in one file. ASCII wraps in narrow viewports and tempts cramming 6 components into one mega-diagram; Mermaid forces declarative per-block separation.

**Mermaid label hygiene** (avoid silent parse failures):
- Avoid `:` `?` `/` in node labels — use `<br/>` for line breaks, `·` (middle dot) or `-` for separators
- Use unquoted shapes for storage: `[(label)]` for cylinders works; `[("label")]` with quotes inside is fragile
- Declare nodes first, then edges — avoid inline edge-node defs (`A --> B[Inline new node]`) when label has special chars

**How many diagrams (typical):**
- **Diagram A** — single-region top-level (Client → Edge → API GW → Service tier → Data tier). **Always required** — also re-embedded verbatim in §5 High-Level Design.
- **Diagram B** — the most complex/critical component's internal flow (state machine, pipeline, fan-out path) — whatever is the题目灵魂.
- **Diagram C** — second internal flow if there's a clearly distinct subsystem (e.g., matchmaking, billing settlement). Skip if the题目 has only one hot subsystem.
- **Diagram D** — entity lifecycle / state machine if the题目 has a non-trivial state graph (game lifecycle, payment intent, order state).

Aim for **2–4 Mermaid blocks total**. Each must teach something the others don't.

**Mermaid 写法约定：**
- Top-level (Diagram A) 用 `flowchart LR`（左到右）：Client at far left → Edge → Gateway → Service tier → Data tier at far right.
- Pipelines / state machines (Diagram B / D) 用 `flowchart TD`（上到下），步骤编号写进 node label。
- Stateful 数据存储用圆柱形 `[(label)]`（databases），无状态服务用方框 `[label]`。
- 用 `subgraph` 把 data tier、cross-region 区域分组，框出逻辑边界。
- 同步调用用实线 `-->`，CDC / 异步事件流用虚线 `-. label .->`。
- Node label 多行时用 `<br/>` 折行；不要往里塞超过 4 行——超过就拆 node 或拆 diagram。
- 一张图最多 ~12 nodes；超了拆成两张。
- 关键节点（hot subsystem、reject path）可加 `style NodeId fill:#ffe6cc` 突出（可选）。

**Example skeleton (top-level)：**

````
```mermaid
flowchart LR
    Client[Web / Mobile]
    Edge[Edge<br/>CDN + Anycast]
    GW[API Gateway<br/>authn · RL · routing]
    SvcA[Service A]
    SvcB[Service B]

    subgraph Data[Data tier]
        DB1[(Primary store<br/>schema choice + key reason)]
        Cache[(Cache layer<br/>TTL / eviction)]
    end

    Client -- "HTTPS + SSE" --> Edge --> GW
    GW --> SvcA
    GW --> SvcB
    SvcA --> DB1
    SvcA --> Cache
    DB1 -. CDC .-> Cache
```
````

**Layout conventions:**
- Top-level (Diagram A) is **left-to-right (LR)**: Client at far left → Edge → Gateway → Service tier → Data tier at far right.
- Pipelines / state machines (Diagram B / D) are **top-to-bottom (TD)**, steps numbered into the box label.
- Storage nodes use cylinder shape `[(...)]` consistently.
- Subgraphs group logical boundaries (Edge POP, Data tier, Async pipeline, Cross-region cluster).
- Async / CDC / sticky-route edges use dashed `-. label .->`; happy path uses solid `-->`.
- One diagram = max ~12 nodes. More than that, split.

Below each Mermaid block, walk through the **happy-path request flow** in numbered steps for the 1–2 most important user actions. Each step: which component, what data store, what guarantee.

**When ASCII is OK:** latency budget tables, token math, sequence-of-events timelines that are pure text. Topology / state diagrams: never ASCII.

### Section 3 — Deep Dive

Pick **2–3 components** that actually matter and go deep. Skip the boring ones (don't deep-dive the load balancer). Good candidates: the data model + storage choice, the hot path that determines read latency, the part that breaks at 10x scale.

For each component cover:

- **Data model** — schema with concrete column types and a primary key choice; explain the partition key choice especially.
- **API contract** — request/response shape, idempotency keys, error semantics. Use `POST /v1/...` style.
- **Storage choice with explicit alternatives** — "I'd use ScyllaDB here. Cassandra would also work but its tail latency is worse at our QPS. DynamoDB works but cost at 600K QPS read is ~3x. Postgres is wrong because <X>." Always name 2–3 alternatives and why you rejected them. This is the highest-value content for the reader.
- **Hot-path walkthrough** — for the latency-critical operation, list every hop and rough latency budget (cache lookup 1ms, DB read 5ms, etc.) summing to your p99 target.

### Section 4 — Scale, Bottlenecks & Failure Modes

Three subsections:

**4.1 What breaks at 10x?** — pick the *real* bottleneck given your design (not a generic "add more cache"). Examples:
- Hot partition: celebrity user's timeline shard. Solution: secondary index, or split the hot key, or a dedicated celebrity path with fan-out-on-read.
- Thundering herd on cache miss: stampede protection via single-flight or probabilistic early expiration.
- Cross-region write amplification: if active-active, what does the conflict resolution look like (LWW? CRDTs? per-entity ownership?).

**4.2 Failure modes & blast radius** — for each major dependency (DB, cache, queue), state: what happens when it fails, what the user-visible degradation is, and how you contain blast radius. Concrete: "If timeline cache is down, fall back to live fan-out from Post DB at 3x latency; rate-limit to protect DB; serve stale from local CDN where possible." Don't write generic "we have retries" — write the actual recovery story.

**4.3 Operational concerns** — cost order-of-magnitude (just $/month rough), how you'd roll out a schema change without downtime, what the on-call dashboard's top 3 metrics are.

---

## Step 5 — Dispatch Codex Round B (follow-ups + critique + reference)

Now hand the design to Codex for the interviewer's three jobs in one shot. Use `Agent` with `subagent_type: "codex:codex-rescue"`.

Prompt template:

```
--read-only

You are a Distinguished Engineer at a top-tier tech company (E8/E9 / Netflix Senior Principal / Google Distinguished / Meta D-level — one level above the candidate). The candidate is a Principal Engineer and has just walked through their design for the following problem. Your job has THREE parts. Do all three. Be specific and challenging — go for the questions you would actually ask if you were on a Distinguished panel deciding whether to bring this person up to your level. The candidate should leave the room having learned something they didn't know going in.

# Original problem
<paste user's original problem>

# Candidate's design (6-section format)
<paste sections 1–6 verbatim>

# PART 1 — Follow-up questions (3–5)
出 3–5 个真的有挑战性的 follow-up，不要送分题。每个 follow-up 应该满足以下其中一条：
(a) 戳穿设计中最薄弱的假设（"你假设 follower 数 ≤ 1M，celebrity 怎么办？"）
(b) 引入新约束打乱原设计（"现在加上 EU GDPR 的 right-to-erasure，怎么改？"）
(c) 跨抽象层级追问实现细节（"你说用 Redis cluster，hot key 时 cluster slot 迁移怎么不阻塞？"）
(d) 经济/运营角度的灵魂拷问（"这个设计每月成本是多少？砍 30% 成本怎么砍？"）
(e) 失败模式的具体演练（"primary region 突然全断，RTO 多少？演给我听 step by step。"）

格式：每个 follow-up 给一个简短的 question，再给一行 "what this is testing"（告诉读者这道题在考什么能力）。不要给答案 —— 答案由候选人来答。

# PART 2 — Critique & grade
以 distinguished engineer 面试官视角评分（strong hire / hire / lean hire / lean no-hire / no-hire，bar 是 principal level — 不是 distinguished level，因为我们在面 principal 不是面 distinguished），并列出：
- 强项 2–3 条
- 失分点 / missing 2–3 条（要具体，例如 "没讨论 backfill 历史数据时 fan-out 风暴"）
- 距离 distinguished engineer 自己会怎么做这道题的 gap（1–2 条）—— 这一条是给候选人"未来你升 distinguished 之前还差什么"的提示，不影响 hire / no-hire 决定

# PART 3 — Reference architecture (≥ 200 字, no upper bound)
举一个真实公司的真实架构作为对比（Discord 的 message storage 从 Cassandra 迁到 ScyllaDB，Twitter 的 Manhattan + Redis timeline，Uber 的 Ringpop + H3，Instagram 的 Cassandra + EVCache，Anthropic / OpenAI 公开过的细节，etc.）。

**字数与具体度要求：本节正文 ≥ 200 中文字符**（不含 markdown 标题/链接），且必须包含至少一个**具体数字**——节点数、QPS、存储量、迁移耗时、cost delta、tail latency、incident 次数等任一可考据的量化数据。如果只能给定性描述，则不要写这个 reference，换一个有公开数据的系统。不要客气话，不要 "this is a great example"。

四个段落（≥ 200 字总长）：
- **Company + system**：公司名 + 系统/项目代号 (e.g. Discord `cassandra-messages`, Uber `Ringpop`, Anthropic `Claude API serving stack`)。给一两句一句话定位这个系统在公司内的角色。
- **Key decision that differs from candidate**：他们在某一个具体决策上和候选人的设计不同——存储选型、partition 策略、缓存层放置、admission control 机制、服务边界等。一句话讲清差在哪。
- **Why they made that choice**：业务约束、规模拐点、历史包袱。**这一段必须包含至少一个具体数字**：当时集群有多少 node、出现了多少 incident、迁移用了多久、tail latency 从多少降到多少、cost 减少了多少 %，等等。给 inline citation `([source-name](url))`，找不到 url 给来源名也行。
- **Transferable lesson for the candidate**：候选人能从这个对比学到什么*可迁移*的设计原则——不是 "用 ScyllaDB"（太具体），也不是 "要扩展性"（太空泛），而是中间层级的 design pattern，例如 "API 和 DB 之间放一层 stateless data service 做 routing/coalescing"。

# Output language
中文为主，技术术语英文。不要 preamble，不要 "great design overall" 这种客气话，直接进入三个 PART。
```

Take Codex's full response and embed it as Section 5 (follow-ups) and Section 6 (critique + reference) in the final transcript.

If Codex fails, fall back: generate 3 follow-ups yourself but explicitly mark "(Codex unavailable — these follow-ups are self-generated and likely softer than a real panel would ask)".

## Step 5.5 — Iterate-until-strong-hire (mandatory if grade is below strong hire)

**Anything below `strong hire` is the signal to revise.** The point of this skill is to teach the user a *strong-hire-grade* design they can walk into an interview with. Saving a transcript that says "lean hire" or worse is not good enough — iterate until Codex gives `strong hire`.

So if the **PART 2 — Critique & grade** comes back with anything other than `strong hire`, you MUST iterate. Run this loop:

```
iteration = 1
while iteration <= 5:
    if grade == "strong hire":
        break  # converged
    # Iterate: address every "失分点 / missing" from the critique.
    sections_1_to_4 = revise(sections_1_to_4, codex_critique)
    iteration += 1
    codex_round_b = dispatch_round_b(revised_sections_1_to_4)
    grade = parse_grade(codex_round_b)
# loop exit: either converged to strong hire, or hit 5 iterations.
```

### How to revise between iterations

Read the "失分点 / missing" list and the "Gap vs Distinguished Engineer" list in Codex's critique. Each item is an actionable fix:

- **"没讨论 X"** → add a new subsection or paragraph that takes a position on X with concrete numbers
- **"X 设计最薄弱"** → rewrite that subsection. Don't paper over it; if the design has a real bug, name it and fix it (the chess `CLOCK_MONOTONIC` recovery bug from prior runs is the canonical example — wrong design needs to be replaced, not defended)
- **"过度依赖 Y, 缺少 Z"** → add the missing degraded-mode / fallback / multi-layer defense
- **"Vague Z"** → replace with concrete numbers, schemas, or named tradeoffs
- **Gap items** — these are how a Distinguished Engineer would have framed it. Adopt the framing.

Don't just apply cosmetic edits. Codex on the second pass can tell. Honest, structural revisions move the grade; sprinkling adjectives doesn't.

### Re-dispatching Round B

Use the SAME Round B prompt template as Step 5, but with the revised Sections 1–4. The prompt also gets one extra block at the top so Codex knows this is a revised submission:

```
NOTE TO INTERVIEWER: This is iteration N of N+1 max. The previous iteration received grade `<previous grade>` with these missing items: <list>. The candidate has revised Sections 1–4 to address them. Evaluate the REVISED design fresh — do not anchor on prior critique unless the same gap remains.
```

This prompts Codex to evaluate fresh while still raising the bar. (Prior runs show Codex tends to be more generous on iteration 2 if you don't explicitly re-anchor, so the note matters.)

### What lands in the final transcript

The final transcript shows the **converged design only** — the revised Sections 1–4 that earned `strong hire`, the converged Round B follow-ups + critique, the principal answers to those follow-ups. The intermediate failed iterations are NOT shown. The point is to give the user a strong-hire-grade artifact, not a debugging log of how it got there.

**Exception — append a brief "Iteration log" footer at the very end of the transcript**, after Section 8 Takeaways, in this format:

```markdown
---

## 附录：Iteration log

本设计经过 N 轮 Codex critique 收敛至 `<final grade>`。

| Iter | Grade | 主要修复 |
|------|-------|---------|
| 1 | lean no-hire | snapshot tail-latency 数学补完；replace lazy-mmap with tiered NVMe cache |
| 2 | lean hire | egress proxy 重构为 eBPF identity-aware；quota partition 加 per-host panic mode |
```

This footer teaches the user *what was learned through critique*. It's brief, structured, and clearly marked as appendix — does not pollute the main answer flow.

### Cap at 5 iterations

If after 5 iterations the grade is still below `strong hire`, stop and emit the best-graded transcript with this note prepended at the top:

```
> ⚠️ 本次设计经过 5 轮 Codex iteration 仍停留在 `<final grade>`，未达 strong hire。最后一轮的 critique 表明该题在当前框架下难以达到 strong hire——可能题目本身有 candidate-vs-Codex 知识 gap (Codex 知道某个具体公司内部架构而 candidate 不知)。建议：(a) 用户自行查阅 reference architecture; (b) 用 `/codex:setup` 检查 Codex 状态; (c) 把题目重新 frame 后再跑一次 skill。
```

Don't loop more than 5 — Codex API costs add up, and if the design isn't converging in 5 passes, more iterations rarely help. Better to surface the failure to the user honestly.

### When Codex unavailable for re-dispatch

If iteration N's Round B dispatch fails (Codex offline, network, etc.), use the LAST successful iteration's design + critique. Mark in the iteration log:

```
| 2 | (Codex 不可用) | revision drafted but not graded; using iteration 1 grade as final |
```

## Step 6 — Answer each follow-up in depth

For EACH follow-up Codex produced, write a Claude (principal) answer of 150–300 words. This is the single highest-value section for the reader — this is where depth happens.

Each answer must:
- Take a clear position. No "it depends" without immediately specifying *on what* and resolving it.
- Name the tradeoff explicitly (latency vs cost, consistency vs availability, build vs buy).
- Give a concrete number when possible (latency budget, replication factor, cost delta).
- If you genuinely don't know the answer at principal depth, say "I would prototype this to find out" and describe the experiment — that's an honest principal answer, not a cop-out.

Format each as:

```
### Follow-up N: <Codex's question>
**What's tested:** <Codex's "testing" hint>

**Principal's answer:**
<150–300 words>
```

## Step 7 — Section 7: Learning Takeaways

Close with a takeaways section *for the user*, not for the interview. 3–5 bullets covering:

- The 1 core concept this question tests (e.g. "fan-out write vs fan-out read tradeoff for feed systems")
- The 1 most common pitfall a candidate falls into (e.g. "treating celebrity users like normal users")
- 2–3 transferable patterns to other system design questions ("any time read:write > 50:1, cache-first; any time you have a hot key, plan for split-key escape hatch from day 1")
- 1 thing the user should explicitly memorize from this transcript (a number, a schema decision, a tradeoff)

This section is in plain Chinese (less English jargon), because it's the "what to remember" closer.

---

---

## Review workflow

Triggered when the user wants critique of an existing SD doc instead of designing from scratch. The user owns the doc — Claude must **not** rewrite it; Claude only produces a side-by-side review/critique artifact.

### Step R1 — Locate and read the doc

The user gave you either an absolute path, a relative path under `Career/System Design/`, or a pasted excerpt. Use `Read` to load the full file. If the path is ambiguous (multiple matches), ask once. If the doc is shorter than 1500 chars and looks like a stub, confirm with the user before proceeding ("这看起来只是骨架，是要 review 还是要补全？").

### Step R2 — Quick structural triage

Before dispatching Codex, scan the doc for these signals (silently — don't write triage to disk):

- **Section coverage** — does it have requirements / NFR / API / DB / HLD / deep dives? List missing or thin sections.
- **Mermaid diagrams** — present? render correctly (no `:` / `?` / `/` in node labels)? Number of diagrams.
- **Numbers** — does estimation show derivation, or just QPS numbers dropped in?
- **Tradeoff stance** — does the author take sides ("we pick X because Y") or list options without picking?
- **Multi-region drift** — single-region multi-AZ should be default for 60-min interview; if the doc spends pages on cross-region without it being asked, flag.
- **Ad-hoc abbreviations** — count GW / GS / MMF / WSE / PG-style internal acronyms (these should be full names).

This triage informs your own commentary later — it is NOT the critique itself. Codex does the formal critique.

### Step R3 — Dispatch Codex Review Round (single shot)

Use `Agent` with `subagent_type: "codex:codex-rescue"`, `--read-only`. Prompt template:

```
--read-only

You are a Distinguished Engineer at a top-tier tech company (E8/E9 / Netflix Senior Principal / Google Distinguished / Meta D-level — one level above the candidate). The candidate (Principal-level) has handed you their finished system design doc for review BEFORE an interview. Your job: tell them whether this would pass a Distinguished-staffed Principal panel, and exactly what to change to get to `strong hire`. Be honest and specific — flattery is worthless here.

# Doc under review (verbatim)
<paste the entire doc>

# Output (Chinese-primary, English tech terms — no preamble, no compliments)

## 1. Grade (mandatory)
One label: `strong hire` / `hire` / `lean hire` / `lean no-hire` / `no-hire`. One sentence why.

## 2. 强项 (2–3 条)
Specific. Quote a section reference (§3 / §5.2) when calling out a strength.

## 3. 失分点 (3–6 条)
Each item must:
- name the section / paragraph it lives in (or "missing entirely")
- explain what a Distinguished panel would push back on
- propose a concrete fix in 1–2 sentences (numbers, schema choice, tradeoff to add)

Do NOT write "expand on this" or "consider X" — write the actual fix the author should adopt.

## 4. 关键追问 (3–5 题 follow-up，with "what this tests" subline)
Pretend this is the live interview and you're choosing the 3–5 questions you would ask after reading the doc. Pick the ones that most expose weak assumptions or test whether the author understands the system at Distinguished depth. Each follow-up: one short Chinese question + one line "what this tests" + (optional) a hint at the desired answer shape.

## 5. Reference architecture (≥ 200 字, 含 ≥ 1 个具体数字)
Same rules as the practice workflow's PART 3: real company's real system, key decision that differs, why they made it (with a number), transferable lesson. Pick one with public data — Discord / Twitter / Uber / Instagram / Anthropic / OpenAI / Stripe / Cloudflare / Netflix.

## 6. Gap vs Distinguished (1–2 条)
What the author would need to add to be promoted-to-Distinguished worthy on this doc. Doesn't affect the `hire` grade — just teaches them what's beyond Principal.

# Hard rules
- No "great design overall" / "this is solid" filler.
- Every 失分 must be section-anchored and have a concrete fix.
- Reference architecture must contain ≥ 1 quantified fact.
- If the doc is too short / vague to review meaningfully, say so in §1 with grade `cannot grade` and stop — don't fabricate critique.
```

### Step R3.5 — Write the corrected sections (完美答案 / strong-hire rewrite)

**This is mandatory, not optional.** Review mode's whole point is to give the user the perfect answer, not just identify gaps. Read Codex's `失分点` list and write the corrected sections as Claude (Principal Engineer).

What to produce:

1. **Rewrite every section Codex flagged in `失分点`** — if §"TTL janitor" is wrong, write a corrected "TTL & expiration" subsection that actually solves it (lazy + sampled active, CPU cap, timing wheel). If §"HA" didn't pick a side, write the decisive replication-and-failover story (ack semantics, replication lag bound, failover RTO, loss window).
2. **Add sections Codex flagged as "missing entirely"** — e.g. if observability / overload protection / cold-start / cost-modeling are absent, write them from scratch at Principal depth.
3. **Add at least one Mermaid diagram** that ties the corrected design together — the original may have only SVGs, so this gives the reader a self-contained text-renderable view. Follow the Mermaid hygiene rules in §Practice Step 4 Section 2.
4. **For every rewritten subsection, include**: take a clear position; name the tradeoff; give a concrete number (latency budget / replication factor / RPS / shard count / cost); name 2–3 alternatives and explain why you reject them.

**Length**: aim for ~1500–3500 中文字符 of substantive rewrites (not counting Codex's quoted critique). If a section was already fine, **don't rewrite it** — only touch the broken ones. The deliverable is a focused "what good looks like" patch set, not a rewrite of the whole doc.

**Tone**: Principal answering a Distinguished panel. No "consider X" — write the actual position with the actual numbers. Skip apologetic framing.

**Formatting (mandatory) — HelloInterview Bad/Good/Great callout style**:

Each rewritten 修订 章节 MUST use Obsidian callout boxes matching the original HelloInterview doc convention, NOT dense prose. This makes the rewrite visually consistent with the doc it's being appended to, and easier to read in Obsidian.

Template per 修订:

```markdown
#### 修订 N：§<原章节名> — <修复主题>

>[!error] Bad Solution: <原文方案的简短名>
>###### Approach
>- bullet 1
>- bullet 2
>- (1 行说明这是 HelloInterview 原方案 OR 候选人会想到的 naive 答案)
>###### Challenges
>- 失分点 1(具体数字 / 失败 case)
>- 失分点 2

>[!success] Good Solution: <中间方案名>
>###### Approach
>- bullet 列方法
>###### Challenges
>- 仍然不够的地方,推下一步往 Great 走

>[!important] Great Solution: <Principal-grade 答案名>
>###### Approach
>- bullet 列设计(schema / 配置 / SQL / 关键数字 内嵌 inline code)
>- 多 bullet 完整覆盖方案
>###### Benefits
>- 关闭 Codex 失分的具体方式
>- 性能 / 正确性 / 可维护性 优势

---
```

**硬规则**:
- 每个 修订 必须有 Bad/Good/Great 三档(可以 Bad/Great 两档,但禁止单一方案罗列)
- Bad solution 第一句必须点明:这是 HelloInterview 原方案 OR 是候选人 naive 答案 — 让读者知道为何要否定它
- Challenges / Benefits 用 bullet point 不用 prose
- 数字、schema、SQL、cgroup config 等用 inline code 或单独 fenced code block,不要塞进 prose 段落
- 多个 path 的容量 / RPO/RTO / latency budget 用 markdown 表格,不要 prose
- 每个 修订 之间用 `---` horizontal rule 分隔,视觉清晰
- 不要写"问题回顾"+"修订设计"+"为什么是 strong-hire"三段 prose —— 用上方 callout 结构替代

**禁止的反模式**:
- ❌ 长段 prose 描述设计("修订设计:首先...然后...最后...")
- ❌ 把 schema / 代码 / 数字埋在 paragraph 里
- ❌ 单方案罗列,无 Bad/Good 对照
- ❌ Markdown bullet 嵌深超 2 层(可读性差)
- ❌ Rejected alternatives 单写一段 prose —— 应放进 Bad Solution callout 里讨论

### Step R4 — Append `## Review feedback` to the original doc

**Do NOT create a new file.** The review is appended as a section at the **bottom** of the original SD doc, so the user reads the design and its critique side-by-side in one note.

Use the `Edit` tool to append. The new section is added after the existing content (one blank line + `---` separator + the block below). If the file already has a `## Review feedback` section from a previous run:
- If the file's content above that section is unchanged since last review, replace the old `## Review feedback` block in place (use Edit's exact-match replacement on the entire prior block).
- If the SD content above changed since last review, append a **new** `## Review feedback (<YYYY-MM-DD>)` block below the existing one — keep historical reviews for diff. Don't delete the prior block.

Block to append (verbatim structure):

```markdown

---

## Review feedback

> **Review date**: <today YYYY-MM-DD>
> **Reviewer**: Claude (Principal) + Codex (Distinguished Engineer)
> **最终评级**: <Codex grade>

### TL;DR
- <2-3 bullets — Claude's summary of what the author should fix first, in priority order>

### Codex 评级与 critique
<Codex's §1–§3 verbatim>

### Codex follow-up（拿这份 doc 去面试会被问的题）
<Codex's §4 verbatim>

### Reference architecture
<Codex's §5 verbatim>

### Distinguished Engineer Gap
<Codex's §6 verbatim>

### 完美答案 · 修复后的关键章节（Principal-grade rewrite）

> 这一节是 review 模式的**核心交付**。Claude 用 Principal Engineer 视角，针对 Codex 列出的每个失分点，写出**完整可直接对照采用**的修订版章节——不是 P0/P1/P2 bullet 提示，而是真正能照搬进 doc 的设计文字。

For each Codex `失分点` item, write a self-contained corrected subsection using **HelloInterview Bad/Good/Great callout style**(详细规则见 Step R3.5 上方 "Formatting (mandatory)"):

- Bad Solution callout(原方案 OR naive 答案 + 失分原因)
- Good Solution callout(中间方案 + 仍不够的地方)
- Great Solution callout(Principal-grade 答案 + Benefits)
- 关键数字 / schema / SQL / config 用 inline code 或 fenced code block,不嵌 prose
- 容量 / RPO/RTO / latency 用 markdown 表格
- 修订之间用 `---` 分隔

格式模板(每个修订章节,**严格按 HelloInterview 风格**):

```markdown
#### 修订 N：§<原章节名> — <修复主题>

>[!error] Bad Solution: <原方案简短名>
>###### Approach
>- (HelloInterview 原文方案 OR candidate naive 答案)
>- bullet 列方法
>###### Challenges
>- 失分点 1(具体数字)
>- 失分点 2

>[!success] Good Solution: <中间方案名>
>###### Approach
>- bullet 列方法
>###### Challenges
>- 仍不够的地方

>[!important] Great Solution: <Principal-grade 答案名>
>###### Approach
>- bullet 列设计(schema / SQL / config 用 inline code)
>- 多 bullet 完整覆盖
>###### Benefits
>- 关闭 Codex 失分的具体方式
>- 优势 1 / 2

---
```

如果有合适的统一架构图,在所有修订章节之后追加一个 **总图(Mermaid,flowchart LR)**,把修订后的核心组件串成 happy-path。

### Principal (Claude) 的补充观察
Claude 从 R2 triage 看到、但 Codex 没显式写的二阶问题：
- <bullet 1 — 例如 "§5 用 active-active multi-region 但 §2 没列对应的 NFR；要么砍掉，要么加 conflict-resolution 段">
- <bullet 2 — 例如 "全文用 GS shard、MMF、WSE 等 ad-hoc 缩写；面试当场要 spell out，建议替换为全名">

### 建议修订动作（按优先级）
- **P0**（必须修，否则掉 grade）：…
- **P1**（改完 lean hire → hire）：…
- **P2**（polish；hire → strong hire）：…

### Follow-up 答题骨架（可选）
对 Codex 出的每道 follow-up，给 80–150 字 principal 答题骨架（方向 + 关键 tradeoff + 关键数字），用于口头预演。
```

**编辑原文档时的硬约束**：
- 只 append `## Review feedback` 块，**不要触碰原 doc 的任何其他内容**——不修改 NFR 表格、不补 Mermaid、不重排段落。修订建议只出现在 `## 建议修订动作` bullets 里，让作者自己改。
- 不要在原 doc 顶部插入 review、不要在 §x 上方/中间夹评注——只在文末。
- 如果作者写完 review 后明确说 "帮我把 P0 改进 doc"，那是单独的后续操作；review-mode 本身只 append feedback，不动设计内容。

### Step R4.5 — Optional: re-grade the corrected sections (Codex 二评)

After Step R3.5 (Claude wrote 完美答案 / corrected sections), you MAY dispatch Codex one more time to grade the rewrite. This proves the perfect answer actually closes the gaps — not just that Claude *claimed* to.

**When to run R4.5**: original grade is `lean no-hire` or worse. Skip when original was already `lean hire` or higher (rewrite is polish, not rescue — second pass is overkill).

**Codex prompt for R4.5** (single shot, same `Agent` with `codex:codex-rescue`, `--read-only`):

```
--read-only

You graded a candidate's system design doc at `<original grade>`. They've now revised the weak sections. Re-grade ONLY the revised sections (you don't need to re-read the whole original — only check whether the revisions close your prior critique). Use the SAME grading rubric.

# Original failing points (your prior critique)
<paste Codex §3 失分点 verbatim>

# Revised sections
<paste Claude's 完美答案 section verbatim>

# Output (Chinese, English tech terms, no preamble)
## New grade
One label + 1 sentence why.

## 已修复 (closed gaps)
List which of the original 失分点 are now adequately addressed. Be honest — if the revision is hand-wavy, say so.

## 未修复 / 新引入的问题 (if any)
List anything that remains weak or that the revision newly broke.

## Final verdict
One sentence: did the rewrite reach `strong hire`, or what's left.
```

Append Codex's R4.5 output as a sub-section `### Codex 二评 (revised sections)` immediately below 完美答案 in the appended `## Review feedback` block.

If R4.5 grade is still below `strong hire`, **don't loop again** — review mode caps at one rewrite + one re-grade. Append a TL;DR note: "完美答案二评仍为 `<grade>`，剩余 gap：<list>。建议用户结合两轮 critique 自己再迭代。" This is honest closure — better than pretending the rewrite is perfect.

### Step R5 — Iterate? (no — terminate after R4.5)

Review mode does **not** auto-iterate beyond R4.5 — one critique + one rewrite + one re-grade max. The user owns the doc; they read the review feedback, decide what to merge, and re-run review mode after they've applied fixes themselves. If they want another pass after manual revisions, they invoke the skill again — that becomes a fresh review with the updated doc.

If R1 grade = `cannot grade` (doc too thin), skip R3.5/R4.5 entirely — the review artifact is just §1 + a "doc needs to be filled in before meaningful review is possible" note. Don't fabricate critique or 完美答案 on absent content.

### Review-mode summary to chat

After appending the `## Review feedback` block, post a short chat summary:

```
✅ Review 完成
📄 已 append `## Review feedback` 到 <original doc path>
🎯 评级: <grade>
🔝 P0 修订: <最重要的 1 行>
```

Do NOT echo the full review to chat — it's for reading in Obsidian.

---

## Final transcript format

> Applies to **Practice mode** only. Review mode appends `## Review feedback` to the original doc (see Step R4) and does not produce a separate transcript file.

Emit the whole thing as one markdown document:

```
# 系统设计面试 — <用户的题目原文>

> 📸 What is <X>? (1 paragraph, 定义 + 类比)
**核心 framing**: 1 句话规模 + scope

# ==1. Functional Requirements==
# ==2. Non-Functional Requirements==
# ==3. API Design==
# ==4. Database Design==
# ==5. High-Level Design==
# ==6. Deep Dives==
## 1) <NFR / topic>   ← 每个 deep dive 用 H2（##），不是 H4（####）
（每题: Pattern callout + Bad/Good/Great；Bad 必带数字证伪）
[Optional] ## Bonus: <advanced topic>
### What is Expected at Each Level (Mid / Senior / Staff+ / Distinguished — mandatory)

---

[Optional] ## 必背 cheat sheet
[Optional] ## 临场救火 reference

---

## Codex 面试官的 Follow-up 问题
（Codex Round B 的 PART 1 原文）

## Codex 面试官的 Critique + Reference Architecture
### Critique & grade（PART 2）
### Reference architecture（PART 3）

## Principal 对每个 Follow-up 的深度回答
### Follow-up 1: ...
### Follow-up 2: ...
...

## Learning Takeaways

[Optional appendix — only present if iteration was needed]
## 附录：Iteration log
```

**Iteration log** (Step 5.5): if the design needed Codex iteration to reach `strong hire`, append a brief table at the very end showing each iteration's grade + main fix. Skip if first Round B already returned `strong hire`.

## What to avoid (mistakes that make the transcript useless for learning)

- **Generic "it depends"** without resolving it. If you write "it depends on the consistency requirements", the very next sentence MUST be "we said earlier we need read-after-write for the user's own posts, so we choose X."
- **Listing options without picking one.** Bad: "We could use Cassandra, DynamoDB, or ScyllaDB." Good: "We pick ScyllaDB. Cassandra would work but tail latency is worse at our QPS; DynamoDB is ~3x more expensive at this scale."
- **Skipping the math in estimation.** Numbers without derivation teach nothing.
- **Vague failure stories.** "We have retries and circuit breakers" is not a failure story. A failure story names the failure, the user-visible symptom, the recovery mechanism, and the RTO.
- **Apologetic framing.** Don't write "this is a high-level sketch" or "in a real interview I'd ask more". Write the answer the principal would give if they had 60 minutes and full attention.
- **Translating English tech terms.** Don't write "一致性哈希 (consistent hashing)" — just write `consistent hashing`. The reader is technical and learning English vocabulary is part of the goal.
- **Letting Codex's output get edited.** Quote it faithfully. If you disagree with Codex, say so in YOUR follow-up answer in Section 7, don't silently rewrite the question or critique.
- **Ad-hoc abbreviations for internal components.** "GS shard" / "MMF" / "WSE" / "GW" / "PG" force the reader to look up the abbreviation table mid-read. Write "Game Server shard" / "Matchmaker Frontend" / "WebSocket Edge" / "API Gateway" / "Postgres" — full name every time. Standard industry acronyms (HTTP, TLS, CDN, DAU, QPS, RTT, RPO/RTO, CAP, ACID, CDC, p99) are universal and stay as acronyms — those are part of the technical vocabulary the reader is *learning*, not noise. See "Output language" above.
- **Saving anything below `strong hire` without iterating.** A transcript that ends "interviewer says lean hire" or worse is not good enough — iterate per Step 5.5 until the design reaches `strong hire`. The user wants a *strong-hire-grade artifact* to walk into an interview with; a transcript that barely passes is not what they came for.

## When Codex is unavailable

If both dispatches fail (codex companion not set up, network failure, etc.):
1. Tell the user once at the top of the transcript: "⚠️ Codex 未能响应，本次 transcript 由 Claude 单独完成；follow-up 和 critique 自评，深度可能不及双 agent 模式。"
2. Self-generate clarifications (Section 1.4), follow-ups (Section 5), and critique (Section 6). Mark these self-generated.
3. Suggest the user run `/codex:setup` if they want the full experience next time.

Do NOT abort the skill — a single-agent transcript is still much better than nothing.

## Length

Don't artificially shorten. A real principal walkthrough of a non-trivial system design is 4–8K words. Optimize for the reader learning, not for brevity. But also don't pad — every paragraph should teach something or take a position.
