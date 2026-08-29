# job-hunt-skills

A set of [Claude Code](https://claude.com/claude-code) Skills covering the full
job-search and interview-prep workflow. Each top-level directory is a
self-contained skill.

## Skills

### Job search & resume

**`job-scout`** — Job posting scan + scoring.
Ingests job-alert emails (LinkedIn / Jobright / …) and target-company career
pages, scores each posting against your `profile.yaml` / resume (0–100 with a
verdict), filters by location, role type, and seniority, then writes structured
Markdown into your notes vault (daily reports + per-opportunity notes). Can run
on a daily schedule via launchd.

**`resume-builder`** — Resume tailoring & rendering.
A full RenderCV-based workflow: analyze how a given JD matches you, update the
resume YAML, render an ATS-friendly PDF, and maintain role-specific resume
variants. Your real resume data lives outside the repo — the skill holds only
the workflow and decision rules.

### Interview preparation

**`system-design-interview`** — System-design interview, two modes.
*Practice mode*: Claude answers as a Principal Engineer while Codex interviews
as a Distinguished Engineer. *Review mode*: you supply an existing design doc,
Codex critiques it, and Claude then writes the strong-hire reference rewrite.
Output is a long Chinese-primary transcript with inline Mermaid architecture
diagrams.

**`interview-question-scout`** — Company 面经 aggregated into a study handbook.
Pulls interview-experience posts from multiple sources (1point3acres, LeetCode
Discuss, Reddit, Glassdoor), merges and de-duplicates them, and ranks questions
by observed frequency. The deliverable is a frequency-ranked question bank with
merged descriptions, solutions, and source links — not a chronological post list.

### Content capture (tooling)

**`course-archiver`** — Save an online course / question bank to local Markdown.
For paid, login-gated content (极客时间, Coursera, Udemy, hack2hire, …), drives
your live Chrome to walk the table of contents and pull each section's text,
headings, code blocks, and images into a clean Markdown directory tree. Depends
on `ai-chrome`.

**`ai-chrome`** — Drive your live Chrome (shared dependency).
Reads and controls tabs in your already-logged-in Chrome over CDP
(`localhost:9222`). Use it when real session state is required — logins,
cookies, JS-rendered content — rather than an anonymous fetch. It is the
foundation the browser-driven skills build on.

Dependency: `course-archiver` → `ai-chrome` (shipped in this repo; resolved via
`AI_CHROME_ROOT`, defaulting to the sibling directory). The other four skills
are independent.

## Setup

Nothing personal is committed. Skills read machine-specific paths and secrets
from environment variables (typically `~/.hermes/.env`) or from a local `.env`
you create by copying the `*.env.example` files. Key variables:

| Variable | Used by | Meaning |
|----------|---------|---------|
| `CAREER_DIR` | resume-builder, system-design-interview | Root folder where human-facing output is written. Defaults to `$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Career`. |
| `JOB_SCOUT_ROOT` | job-scout | Absolute path to the `job-scout/` directory. |
| `OBSIDIAN_VAULT_PATH` | job-scout | Root of your notes vault. |
| `AI_CHROME_ROOT` | course-archiver | Path to the `ai-chrome/` skill. Defaults to the copy shipped in this repo. |
| `AI_CHROME_PROFILE` | ai-chrome | Which `ai-chrome/profiles/<name>.md` to use. |

Per-skill setup lives in each skill's `SETUP.md` / `README.md` / `SKILL.md`.
Copy `job-scout/resume/profile.yaml` and the various `*.env.example` files and
replace the placeholder values with your own before running anything.
