# job-scout

Hermes workflow for:
- ingesting LinkedIn Job Alert emails
- resolving **external-only** JD pages
- scoring roles against a master resume
- writing outputs to Obsidian under `Career/Jobs`

## Structure
- `config/` — portable project config (relative/vault-relative paths and thresholds)
- `scripts/` — ingestion / enrichment / cron context scripts
- `resume/` — master resume, profile, templates
- `skills/job-scout/` — Hermes skill source
- `data/` — inbox, cache, dedupe state
- `docs/` — workflow notes

## Runtime split
- Source code lives here.
- Portable project config lives in `config/job-scout.yaml`.
- Hermes runtime config lives in `~/.hermes` (machine-specific secrets and paths).
- Final outputs go to your Obsidian vault.

## Portability model
- Keep machine-specific values in `~/.hermes/.env`:
  - `JOB_SCOUT_ROOT`
  - `OBSIDIAN_VAULT_PATH`
  - IMAP credentials
- Keep portable workflow logic in `config/job-scout.yaml`:
  - resume path (vault-relative)
  - profile path (project-relative)
  - data file paths
  - output base dir
  - thresholds / allowed domains

## Important guardrail
This workflow is intentionally designed to:
- use LinkedIn Job Alert emails as the discovery layer
- avoid logging into LinkedIn automatically
- avoid scraping linkedin.com
- only enrich JD content from external company / ATS pages

See `docs/SETUP.md`.

## Daily run

One-shot daily pipeline:

```bash
cd "$JOB_SCOUT_ROOT"
python3 scripts/run_daily_job_scout.py
```

Hermes-controlled entrypoint:

```bash
cd "$JOB_SCOUT_ROOT"
python3 scripts/run_daily_via_hermes.py
```

Useful variants:

```bash
# include already-seen Gmail messages in fetch
python3 scripts/run_daily_job_scout.py --include-seen

# rerun scoring / Obsidian writing only
python3 scripts/run_daily_job_scout.py --skip-fetch --skip-resolve
```

## macOS scheduling

Generate a `launchd` job:

```bash
python3 scripts/install_launchd.py --hour 8 --minute 30 --print-only
```

Then remove `--print-only` when you want it loaded.

By default, the generated LaunchAgent now calls the Hermes-controlled entrypoint.
