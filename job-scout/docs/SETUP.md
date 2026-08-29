# job-scout setup

## 1. Machine-specific env (`~/.hermes/.env`)

Required machine-specific values:

```bash
JOB_SCOUT_ROOT=/path/to/job-hunt-skills/job-scout
OBSIDIAN_VAULT_PATH=$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents
```

Optional backward-compatible overrides:

```bash
JOB_RESUME_SOURCE=$OBSIDIAN_VAULT_PATH/Career/resume/resume.md
JOB_PROFILE_SOURCE=$JOB_SCOUT_ROOT/resume/profile.yaml
```

## 2. Project config (`config/job-scout.yaml`)

Portable project-relative / vault-relative paths live in:

```text
config/job-scout.yaml
```

Default example:

```yaml
resume:
  source: vault_relative
  path: Career/resume/resume.md
profile:
  source: project_relative
  path: resume/profile.yaml
output:
  obsidian_base_dir: Career/Jobs
```

## 3. Add email config to `~/.hermes/.env`

Example:

```bash
JOB_ALERT_IMAP_HOST=imap.gmail.com
JOB_ALERT_IMAP_PORT=993
JOB_ALERT_IMAP_USER=you@example.com
JOB_ALERT_IMAP_PASSWORD=app-password-or-imap-password
JOB_ALERT_IMAP_FOLDER=INBOX
JOB_ALERT_LOOKBACK_DAYS=3
JOB_ALERT_UNREAD_ONLY=true
JOB_ALERT_MAX_MESSAGES=25
```

## 4. Test ingestion

```bash
cd "$JOB_SCOUT_ROOT"
python3 scripts/fetch_job_alert_emails.py
python3 scripts/resolve_external_jd.py
python3 scripts/job_workflow_context.py
```

## 4.5 Run the full daily pipeline

```bash
cd "$JOB_SCOUT_ROOT"
python3 scripts/run_daily_job_scout.py
```

If you want Hermes to be the top-level controller:

```bash
python3 scripts/run_daily_via_hermes.py
```

Examples:

```bash
# also consider seen emails
python3 scripts/run_daily_job_scout.py --include-seen

# rerun only triage + Obsidian write
python3 scripts/run_daily_job_scout.py --skip-fetch --skip-resolve
```

## 5. Sync the skill into Hermes

```bash
mkdir -p ~/.hermes/skills/job-scout
cp -R . ~/.hermes/skills/job-scout/
```

## 6. Run Hermes manually with the skill

```bash
cd "$JOB_SCOUT_ROOT"
python3 scripts/job_workflow_context.py
hermes --skills job-scout
```

Or run a one-shot prompt:

```bash
hermes chat --skills job-scout -q "Use the generated job workflow context and process today's resolved jobs."
```

## 7. macOS launchd scheduling

Write a LaunchAgent plist without loading it:

```bash
python3 scripts/install_launchd.py --hour 8 --minute 30 --print-only
```

Actually install / load it:

```bash
python3 scripts/install_launchd.py --hour 8 --minute 30
```

This LaunchAgent uses the Hermes-controlled entrypoint by default.

This creates:

```text
~/Library/LaunchAgents/com.example.job-scout.daily.plist
```

Logs go to:

```text
data/logs/launchd.stdout.log
data/logs/launchd.stderr.log
```
