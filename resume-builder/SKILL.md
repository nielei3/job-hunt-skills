---
name: resume-builder
description: Use this skill when the user wants to tailor their resume for a specific job description, analyze job-description match, update their resume content, regenerate their resume PDF, or create a role-specific resume variant. Covers a full RenderCV-based workflow — evaluating JDs, updating a YAML source, rendering ATS-friendly PDFs, and maintaining role-specific variants. Invoke when the user mentions: a JD URL, a company name + "match" or "fit", "update resume", "render resume", "resume variant", "tailor resume", or career prep that requires touching resume content. Works in any agentskills.io-compliant agent (Claude Code, Hermes, Cursor, Gemini CLI, OpenHands, etc.).
---

# Resume Builder Skill

Turns a structured YAML into an ATS-friendly PDF resume via RenderCV, with a decision framework for tailoring to specific JDs.

---

## Data Boundary

**This skill is the workflow. The user's resume data is separate.**

The skill itself contains only:
- Instructions (this file)
- Reference docs (`references/`)
- Templates (`templates/`)

The user's actual resume YAML, rendered PDFs, and per-company notes live **outside the skill** — typically in a synced folder (iCloud, Dropbox, or a dedicated data directory). Never commit personal data into the skill repo.

By default, look for user data at:
```
~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Career/
```

If the user has chosen a different data location, ask them once and persist it (e.g., in `~/.config/resume-builder/config` or by noting it in `Career/Resume/rendercv/.data-path`).

---

## Expected User Data Layout

```
<user-career-dir>/
├── Resume/
│   ├── <Name>_Resume.pdf                 # Master PDF — generated artifact, DO NOT EDIT
│   ├── <Name>_Resume_<VARIANT>.pdf       # Per-variant PDFs (one per company / JD)
│   └── rendercv/
│       ├── <Name>_Resume.yaml            # MASTER — source of truth
│       ├── <Name>_Resume_<VARIANT>.yaml  # Per-variant YAMLs
│       └── render.sh                     # Wrapper: renders YAML(s), cleans intermediate .typ
├── Company/                              # Per-company notes & analyses
└── Jobs/                                 # Optional raw JD storage
```

The user's setup is **PDF-only**: HTML/Markdown/PNG/Typst outputs are disabled in each YAML's `settings.render_command` (`dont_generate_markdown: true`, etc.). Only the `.pdf` is kept. The `.typ` intermediate is required during render but is cleaned up immediately by `render.sh`.

---

## Prerequisites

RenderCV must be installed. Check with `which rendercv`. If missing:

```bash
# Install uv if not present
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install rendercv with full extras (requires Python ≥ 3.10 — uv handles this)
export PATH="$HOME/.local/bin:$PATH"
uv tool install "rendercv[full]"
```

Add to shell rc on any new machine:
```bash
export PATH="$HOME/.local/bin:$PATH"
```

---

## Core Commands

**Always use the `render.sh` wrapper, not `rendercv` directly.** The wrapper renders + cleans intermediate `.typ` files in one step, keeping the rendercv/ folder tidy.

```bash
cd "<user-career-dir>/Resume/rendercv"
./render.sh                              # render every YAML in this directory
./render.sh <Name>_Resume                # render only the master (omit .yaml suffix or include — both accepted)
./render.sh <Name>_Resume_<Variant>      # render only a named variant
open ../<Name>_Resume.pdf                # preview on macOS
```

Rendering produces only the `.pdf` directly in `<user-career-dir>/Resume/`. No `.md` / `.html` / `.png` artifacts — those are disabled per-YAML to keep the deliverables folder clean.

**Why this works**: each YAML carries a `settings.render_command` block (see `templates/cv_base.yaml.template`) that pins `output_folder: ..`, forces `pdf_path: OUTPUT_FOLDER/<Name>_Resume.pdf`, sets `typst_path: <Name>_Resume.typ` (relative — falls inside `rendercv/` not `Resume/`), and disables non-PDF outputs via `dont_generate_markdown / dont_generate_html / dont_generate_png: true`. Without that block, rendercv would write `./rendercv_output/<cv.name>_CV.*` (3-deep, wrong name, all formats). New YAMLs derived from the template inherit these settings; legacy YAMLs need either the settings block added once or `-o ..` on the command line.

---

## Mandatory rule: every YAML edit ends with a render

**After any edit to a `*.yaml` under `Resume/rendercv/` — master or variant — the corresponding PDF MUST be regenerated in the same turn, before declaring the change "done".**

This is non-negotiable for two reasons:

1. **The YAML is the source; the PDF is the deliverable.** A user clicking "send my resume to the recruiter" reaches for the PDF, not the YAML. If the PDF lags the YAML, the resume the user submits is the *previous* version of their work — silent data loss.
2. **iCloud / cross-machine sync** propagates the YAML and PDF independently. If only the YAML is committed, another machine pulling the change will see stale PDF until that machine also runs render. Producing the PDF on the editing machine eliminates this drift.

### Required sequence

For master:
```bash
# 1. edit master YAML (only with explicit user permission per feedback memory)
$EDITOR <user-career-dir>/Resume/rendercv/<Name>_Resume.yaml

# 2. immediately render
cd <user-career-dir>/Resume/rendercv
./render.sh <Name>_Resume

# 3. confirm fresh PDF
ls -la ../<Name>_Resume.pdf            # mtime should be just now
```

For variants — same pattern, swap in the variant filename. Editing master without a follow-on render is a bug; flag and fix immediately if observed.

### What counts as an "edit"

- Any change to a bullet, summary, headline, technical skills line, headline keyword
- Adding/removing/reordering experience entries or bullets
- Changing `cv.name`, contact info, dates, or social links
- Changing `settings.render_command` itself (test the change with a render)

What does **not** require a render:

- Comment-only changes (`# ...` lines) that don't affect rendered output
- YAML formatting / whitespace / quoting changes when the rendered output is byte-identical (verify by diffing two renders if uncertain)

When in doubt: render. It costs <1 second and removes ambiguity.

---

## Workflow: Tailor Resume for a JD

### Step 1 — Acquire the JD
- If given a URL, fetch it. Platform quirks:
  - TikTok `lifeattiktok.com/referral/...` requires session auth — retry with `lifeattiktok.com/search/<ID>` (public form).
  - LinkedIn `linkedin.com/jobs/...` blocks WebFetch — ask the user to paste JD text.
- Extract: title, location, level, required vs preferred qualifications, tech stack, team mission, salary if shown.

### Step 2 — Match analysis (before editing anything)
Produce an assessment covering:
- **Overall match %** with reasoning
- **Strong matches** (map user's bullets to JD requirements, 1:1)
- **Gaps** — split into "hard minimums" (blocking) vs "preferred" (soft)
- **Preferred qual hit rate** (e.g., 4/7)
- **Narrative risks** (short tenure, title mismatch, location, org-fit)

Persist this analysis at `<user-career-dir>/Company/<Company>-<Role>.md`.

### Step 3 — Decide on action
Ask or infer:
- **Tailored variant** if target role is strong and bullets need reordering/emphasis
- **Update base YAML** if the change is a genuine content addition applicable to future applications
- **Skip** if match < 50% and no narrative bridge — don't waste effort

### Step 4 — Edit the YAML
- Start from the base YAML (the one without a `_<Variant>` suffix).
- For a variant: `cp <Name>_Resume.yaml <Name>_Resume_<ShortName>.yaml` then edit.
- Keep the schema comment intact at line 1: `# yaml-language-server: $schema=...`
- See `references/decision-frameworks.md` for content rules.

### Step 5 — Render and verify
```bash
./render.sh <Name>_Resume_<ShortName>
```
(Variant YAMLs copied from the base inherit the base's `settings.render_command` block, so the wrapper handles everything in one call.)

- Per the mandatory rule above, **this step is not optional** — every YAML edit ends with a render.
- Confirm the PDF fits in **2 pages** (not 1, not 2.5).
- Open and verify first-screen content (header + summary + first experience).
- Report success with the output path.

### Step 6 — Archive
Add a one-liner to `<user-career-dir>/Company/<Company>-<Role>.md` noting which variant file was generated, when, and for which JD.

---

## References

Load these when needed:
- `references/decision-frameworks.md` — content rules (headline, entry layout, skills order, what NOT to add)
- `references/role-matching-framework.md` — how to evaluate a JD vs a resume
- `references/common-pitfalls.md` — known technical pitfalls (schema errors, python version, iCloud path escaping, LinkedIn scraping)

## Templates

- `templates/cv_base.yaml.template` — blank-slate YAML with recommended design overrides

---

## When to Escalate to the User

Ask before acting when:
- The change modifies a verifiable claim (numbers, company names, dates, promotion levels)
- A bullet suggests a technology the user hasn't confirmed using (especially Kubernetes, vLLM, LangChain, RAG)
- You're considering removing content the user explicitly added
- A variant would drop more than one section

Proceed without asking when:
- Reordering existing bullets for emphasis
- Tightening prose (reducing word count while preserving meaning)
- Fixing typos or formatting
- Adding keywords already present elsewhere in the YAML

---

## Cross-Platform Sync Notes

**Multiple machines** (e.g., primary laptop + always-on mini for autonomous agents):
- Put user data in a synced folder (iCloud / Dropbox / Syncthing). Data is identical on both.
- Install `rendercv` on every machine that might render. YAML can be edited from any machine; PDFs are generated wherever rendercv exists.
- Do NOT concurrently edit the same YAML on two machines — wait for sync to settle (~30-60s on iCloud).

**Autonomous agent on one machine + interactive on another**:
- Autonomous agent (e.g., Hermes on a mini) can: analyze JDs, update `Company/` notes, draft YAML edits.
- Interactive agent (e.g., Claude Code on primary) does: final review + render + submit.
- Hand-off via the synced data folder.
