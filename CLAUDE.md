# job-hunt-skills repo conventions

## Default file output path for skills in this repo

Skills in this repo (`job-scout/`, `interview-question-scout/`,
`system-design-interview/`, `resume-builder/`, etc.) write their human-facing
output (transcripts, reports, notes) under a single career root, resolved from
the `CAREER_DIR` environment variable:

```
$CAREER_DIR
```

If `CAREER_DIR` is unset, it defaults to:

```
$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Career
```

Pick a sensible subdirectory under that root based on the skill's domain (e.g.
`System Design/`, `Interview Prep/`, `Job Search/`). If the subdirectory doesn't
exist yet, create it.

A skill may override this default in its own `SKILL.md` if it has a more
specific destination — that override wins.

Filename convention: kebab-case + descriptive (`design-chatgpt.md`, not
`output.md`). On collision, append a date suffix `-YYYY-MM-DD.md`.
