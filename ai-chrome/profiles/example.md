# ai-chrome Project profile — example

Template Chrome profile. Copy this to `profiles/<name>.md` and point
`AI_CHROME_PROFILE=<name>` at it in `ai-chrome/.env` (see `SKILL.md` →
"Project profile").

This file is the **single source of truth** for these values — the caller reads
it and passes them to `scripts/*` as env vars. The scripts hold no Chrome values
and parse no config, so this file is the only copy. All five keys are
**required**; a missing value is exit 5, not a silent fallback.

`~` at the start of a value expands to `$HOME`.

```yaml
# CDP debug port.
port: 9222

# Chrome user-data-dir. Use a DEDICATED dir for this automated identity so it
# never collides with your everyday Chrome profile.
user_data_dir: ~/.config/ai-chrome-example

# Named explicitly so Chrome does not reopen every previously-active profile.
profile_directory: Default

chrome_bin: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome

# tmux session that carries Chrome's stdout/stderr.
tmux_session: ai-chrome
```

## Notes

- One machine runs one profile at a time; `AI_CHROME_PROFILE` picks which file is read.
- Keep the automated identity's `user_data_dir` separate from your daily browser.
