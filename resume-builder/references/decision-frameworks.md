# Decision Frameworks

Rules for what goes into a resume YAML and how to structure it. These are **fixed principles** — do not override without explicit user confirmation.

---

## Headline

- **Keep the resume headline in exact sync with the user's LinkedIn headline.** Desync signals inconsistency to recruiters who cross-check both.
- Follow the pattern: `<Level/Role Identity> | <Domain Keywords> | ex-<Company, Company, Company>`
- Examples:
  - `AI Infrastructure Tech Lead | LLM Platforms, RAG, Agentic Systems | ex-TikTok, Uber, Amazon`
  - `Staff Platform Engineer | Distributed Systems, API Gateway, Observability | ex-Amazon, Google`
- Keywords inside must **only include technologies the user has hands-on experience with**. Never insert buzzwords the user hasn't confirmed.

---

## Entry Layout (Experience section)

**Override the `engineeringresumes` theme default** to put Company first, Position second. Add this to the base YAML's `design` block:

```yaml
design:
  theme: engineeringresumes
  templates:
    experience_entry:
      main_column: "**COMPANY**, POSITION -- LOCATION\nSUMMARY\nHIGHLIGHTS"
```

Rationale: For Staff+ IC/TL candidates, company brand carries more recognition weight than title. Position-first is the US default but leads with noise for candidates whose companies are more recognizable than their titles.

---

## Summary Section

- 2-3 lines maximum.
- Line 1: role identity + years + domain.
- Line 2-3: specialty, most recent differentiating work.
- Do NOT repeat what the headline already says. Summary is an elaboration, not a restatement.
- Avoid generic phrases ("passionate engineer", "results-driven", "strong team player").

---

## Technical Skills

### Recommended category order (for LLM/AI Infra focus)
1. **AI/LLM** — most differentiating, goes first
2. **Languages**
3. **Cloud & Infra**
4. **Data & Storage**
5. **Platform**

For non-AI focus, reorder so the most role-relevant category leads.

### Content rules
- Every technology listed must be something the user has **actually used in production**. Do not add keywords just for ATS.
- Load-bearing keywords for common target domains:
  - LLM platform roles: `model proxy`, `LLM routing`, `fallback`, `RAG`, `agentic workflows`, `observability`, `multi-tenant isolation`
  - ML platform roles: `feature store`, `online serving`, `model serving`, `A/B experimentation`, `evaluation`
  - Platform/reliability roles: `API gateway`, `distributed systems`, `incident response`, `DDoS mitigation`, `microservices`

---

## Experience Bullets

### Formatting
- 3-5 bullets per recent role (last 10 years).
- 1-2 bullets for mid-career roles (10-15 years ago).
- 0-1 bullet for early career (15+ years ago), or drop entirely.
- Every bullet starts with an action verb.
- Every bullet has a concrete **what + scale + outcome** structure. Avoid "responsible for" language.

### Metrics
- Use real numbers when available (QPS, users, latency, cost savings).
- **Do not fabricate metrics.** If a number is unknown, use qualitative wording ("significantly reduced", "at scale") rather than a made-up figure.
- When a bullet cites a metric, make sure the user can defend it in an interview.

### Scope attribution
- "Led" = primary owner of outcome.
- "Contributed to" = meaningful role but not sole owner.
- "Collaborated on" = supporting role.
- Don't overclaim. A detected overclaim at interview kills trust for the rest of the loop.

---

## Early Experience (>10-12 years old)

- Jobs from 10-15 years ago: one title + one bullet each.
- Jobs from 15+ years ago: consider dropping entirely, or compress into a one-line "Earlier Experience" summary.
- Exception: if an early role contains a **signal that still matters today** (e.g., domain expertise that connects to current target roles), keep it as a bridge to the present.
- Always keep education.

---

## What NOT to Add

These are explicit don'ts for the top-of-resume (headline/summary/skills):

### 1. Domain drift
If the user's target roles are in domain X, don't add domain Y keywords just because the user has that experience. Example: if targeting AI Infra, don't put "paid acquisition" or "growth engineering" in skills — that context belongs in experience bullets, not top-of-resume positioning. Mixing identities at the top signals unfocused positioning, which is a Staff+ negative.

### 2. Unconfirmed technologies
Do not add Kubernetes, vLLM, SGLang, TensorRT, LangChain, or similar specific tools unless the user confirms hands-on production experience. These are verifiable in interviews and an overclaim is career-damaging.

### 3. Fabricated metrics
No invented numbers. If the user doesn't remember or didn't measure, use qualitative language.

### 4. Buzzword stuffing
Anti-patterns: "synergies", "10x engineer", "moved the needle", "innovative", "cutting-edge". These signal weak writing, not strong positioning.

---

## Narrative Bridges (Opt-in)

Sometimes an old role has a surprising connection to target roles. Example: an SDET role working on an advertising platform connects to later Ad Growth and Ads ML Platform roles.

When this applies:
- Surface the bridge **explicitly in the bullet** for that old role.
- Example: "First introduction to ads platform engineering, which became the foundation for later work on [current domain]."
- Use sparingly — only when the bridge materially strengthens the narrative for the specific target role.

---

## Single-Version Discipline

For Staff+ IC/TL candidates:
- **One primary resume version per target domain.** Do not maintain a single resume that tries to cover both AI Infra and Growth Engineering (or any two unrelated domains).
- A resume that looks like it's optimizing for multiple identities signals unfocused positioning to recruiters.
- Per-JD tailoring (variant YAMLs) is a different thing — those are same-domain emphasis shifts, not identity switches.

If the user is genuinely pursuing two domains, create two distinct primary YAMLs (e.g., `<Name>_Resume_AIInfra.yaml` + `<Name>_Resume_Growth.yaml`), each fully self-consistent. Never blend.

---

## Escalation Triggers

If applying these rules would require:
- Removing content the user added
- Adding content the user hasn't confirmed
- Changing a verifiable claim
- Restructuring beyond one section

→ **Pause and ask the user.** These rules guide but don't override user intent.
