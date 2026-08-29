# resume-builder

An agent skill for tailoring resumes to specific job descriptions, rendering ATS-friendly PDFs via RenderCV, and maintaining role-specific variants.

Compatible with [agentskills.io](https://agentskills.io) — works in Claude Code, Hermes, Cursor, Gemini CLI, OpenHands, and other compliant agents.

---

## What this skill does

When invoked in a supported agent, this skill:

1. **Analyzes a job description** and produces a match assessment (overall %, strong matches, gaps, narrative risks).
2. **Updates the user's resume YAML** — either the base version or a role-specific variant.
3. **Renders an ATS-friendly PDF** via RenderCV with the `engineeringresumes` theme and a company-first layout override.
4. **Persists per-company notes** for later reference.

The skill **contains the workflow and decision rules**. The user's actual resume data stays outside the skill (in iCloud / Dropbox / local data folder) and is never committed.

---

## Runtime split

- **Skill source** lives here in this repo (workflow, references, templates, design rules).
- **User data** — the actual resume YAML, rendered PDFs, per-company analyses — lives **outside this repo** in the Obsidian vault under `Career/`. Personal data is never committed.
- **RenderCV tool** is machine-local, installed via `uv tool install "rendercv[full]"`.

Path resolution: data path is read from `~/.hermes/.env` (`CAREER_DIR` or equivalent) or defaults to `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Career`.

Expected layout under `$CAREER_DIR/`:
- `Resume/rendercv/<Name>_Resume.yaml` — source of truth
- `Resume/<Name>_Resume.{pdf,typ,html,md,png}` — generated artifacts
- `Company/` — per-company analyses
- `Jobs/` — optional raw JD storage

---

## Structure

```
resume-builder/
├── SKILL.md                                  # Main skill definition (agent reads this)
├── README.md                                 # This file (humans read this)
├── references/
│   ├── decision-frameworks.md               # Rules: headline, layout, skills order, what NOT to add
│   ├── role-matching-framework.md           # How to evaluate a JD vs resume
│   └── common-pitfalls.md                   # Known technical issues and fixes
└── templates/
    └── cv_base.yaml.template                # Blank-slate YAML scaffold with design overrides
```

---

## Prerequisites

### 1. RenderCV

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv tool install "rendercv[full]"
```

Persist PATH in your shell rc:
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

### 2. Data directory

Pick a location for your resume YAML and per-company notes. The skill defaults to:
```
~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Career/
```

Inside it expects:
```
Career/
├── Resume/
│   ├── <Your_Name>_Resume.pdf      # generated artifact (created by rendercv)
│   └── rendercv/
│       └── <Your_Name>_Resume.yaml # your filled-in copy of cv_base.yaml.template (source of truth)
├── Company/                        # per-company analyses (optional, auto-created)
└── Jobs/                           # raw JD storage (optional)
```

### 3. First-time setup

Copy the template into your data directory and fill it in:

```bash
CAREER="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Career"
mkdir -p "$CAREER/Resume/rendercv"
cp templates/cv_base.yaml.template "$CAREER/Resume/rendercv/Your_Name_Resume.yaml"
# Edit the copy with your real content
```

Render your first PDF:
```bash
cd "$CAREER/Resume/rendercv"
rendercv render Your_Name_Resume.yaml
open ../Your_Name_Resume.pdf
```

---

## How to use

### In Claude Code / Hermes / etc.

Once the skill is installed in your agent's skill directory (see installation below), it auto-triggers when you say things like:

- "Does this JD match my resume?" + paste/link
- "Tailor my resume for <company> <role>"
- "Create a variant for the TikTok AIGC role"
- "Re-render my resume"
- "Find gaps in my resume for this job"

The skill will:
1. Run a match analysis (output: assessment markdown)
2. Propose YAML edits (ask before touching)
3. Render PDF on confirmation
4. Archive a note under `Career/Company/<Company>-<Role>.md`

### Manually

You can always run the render step yourself:
```bash
cd "$CAREER/Resume/rendercv"
rendercv render <Your_Name>_Resume.yaml
```

---

## Installation into agent skill directories

Once this skill is in a git repo (e.g., your personal skills collection), symlink or copy it into each agent's skill path.

### Claude Code
```bash
ln -sfn "$(pwd)" "$HOME/.claude/skills/resume-builder"
```

### Hermes
```bash
ln -sfn "$(pwd)" "<hermes-skill-dir>/resume-builder"
```

(Replace `<hermes-skill-dir>` with your actual Hermes skills path.)

### Cursor / Gemini CLI / OpenHands
See each client's docs at [agentskills.io/clients](https://agentskills.io/clients).

---

## Cross-machine workflow

If you run an autonomous agent (Hermes) on one machine and an interactive agent (Claude Code) on another:

- Keep user data in a synced folder (iCloud / Dropbox / Syncthing) — **not in git**.
- Install `rendercv` on every machine that might render a PDF.
- The autonomous agent handles overnight work (JD scanning, company research, draft YAML edits).
- The interactive agent handles final review and rendering.
- Hand-off is via the synced data folder.

Avoid concurrent edits to the same YAML — iCloud sync has a ~30-60 second lag.

---

## Complement to job-scout

`job-scout` handles the **discovery** and **scoring** side (LinkedIn Job Alert ingestion, JD resolution, 0-100 scoring, initial tailored-resume draft to Obsidian).

`resume-builder` handles the **polish** and **submission** side (final YAML editing, PDF rendering, per-JD variant management for high-priority roles).

Future integration ideas:
- `job-scout` high-scoring jobs (>80) trigger a `resume-builder` PDF render
- Shared match-analysis output format

`resume-builder` is **not a daily-pipeline skill** — it's invoked interactively when the user brings a specific JD or asks to update resume content.

---

## Design decisions baked into this skill

A few things are opinionated by default. See `references/decision-frameworks.md` for full rationale.

| Decision | Reason |
|---|---|
| Theme: `engineeringresumes` | ATS-friendly, LaTeX-quality, senior-appropriate |
| Layout: Company before Position | Company brand carries more recognition weight than title at Staff+ |
| Single-domain positioning | One resume per domain. Don't mix AI Infra and Growth Eng identities. |
| No unconfirmed tech in skills | Every keyword must be defensible in an interview |
| Early experience compression | Roles 10+ years old compressed to 1-2 bullets or dropped |

You can override any of these per-variant in specific YAML files.

---

## Contributing / iterating

Since this skill grows with experience, expect to update:

- `references/decision-frameworks.md` — new rules you discover from interview feedback
- `references/common-pitfalls.md` — new technical issues you hit
- `references/role-matching-framework.md` — refined scoring heuristics
- `SKILL.md` description/triggers — as your target roles evolve

Commit messages style:
```
feat(skill): add Kubernetes-gap handling rule
fix(pitfalls): correct rendercv locale field name
docs: clarify cross-machine sync notes
```

---

## License

Choose whatever fits your repo. MIT is a common choice for open-source skills. If the skill contains private/opinionated content, keep it in a private repo.
