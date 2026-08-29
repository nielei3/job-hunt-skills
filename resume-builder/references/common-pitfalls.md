# Common Pitfalls

Known technical issues with the RenderCV + macOS + iCloud + cross-platform setup. Check this list when something breaks.

---

## Python Version

### Symptom
```
ERROR: Python 3.10+ is required, but system python is 3.9
```

### Cause
macOS ships Python 3.9 at `/usr/bin/python3`. RenderCV needs ≥ 3.10.

### Fix
Use `uv` which manages its own Python runtime:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install "rendercv[full]"
```
Never `pip install rendercv` against system Python.

---

## RenderCV "full" Extras

### Symptom
```
It looks like you installed RenderCV with:
    pip install rendercv
But RenderCV needs to be installed with:
    pip install "rendercv[full]"
```

### Cause
Default install lacks Typst bindings.

### Fix
```bash
uv tool uninstall rendercv
uv tool install "rendercv[full]"
```

---

## Schema Validation Errors

### Symptom
```
validation errors!
Location: design.entries.experience_entry  Input Value: ...  Explanation: This field is unknown
```

### Cause
Using wrong field path. Common mistakes:
- `design.entries.experience_entry` → should be `design.templates.experience_entry`
- `main_column_first_row_template` → should be `main_column`
- `locale.language: en` → should be `english` (or omit entirely)
- `rendercv_settings` at top level → not a valid v2.x field

### Fix
Check the actual schema at:
```
https://raw.githubusercontent.com/rendercv/rendercv/refs/tags/v2.8/schema.json
```

Or use an editor with YAML schema support (VS Code + YAML extension picks up the `# yaml-language-server: $schema=...` line).

---

## iCloud Path Escaping

### Symptom
```
bash: cd: too many arguments
```
or
```
file not found: Library/Mobile
```

### Cause
iCloud paths contain spaces and a tilde (`iCloud~md~obsidian`). Without quoting, bash splits on spaces.

### Fix
Always quote the path:
```bash
cd "$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Career/Resume/rendercv"
```

Or set an environment variable once:
```bash
export CAREER_DIR="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Career"
cd "$CAREER_DIR/Resume/rendercv"
```

---

## LinkedIn WebFetch Failure

### Symptom
WebFetch returns status 999 on any `linkedin.com/...` URL.

### Cause
LinkedIn actively blocks automated fetchers. Requires session cookie + JS rendering. Status 999 is their intentional block code.

### Fix
Ask the user to **paste the content**. Never try to bypass with alternate URLs, cached versions, or scraping proxies — LinkedIn will flag the user's account. Official data export is the only legit route:
```
Settings → Data Privacy → Get a copy of your data → Profile + Positions
```
Result arrives in ~24h as a zip.

---

## TikTok Referral URL Auth

### Symptom
WebFetch on `lifeattiktok.com/referral/tiktok/position/<ID>/detail?token=...` returns empty.

### Cause
Referral URLs require the user's session token embedded in the request. Public fetcher can't reproduce that.

### Fix
Strip to the public form:
```
https://lifeattiktok.com/search/<ID>
```
This bypasses the session requirement while exposing the same JD.

---

## Typst Output Edited Manually

### Symptom
Changes to `Resume/<Name>_Resume.typ` disappear after the next render.

### Cause
Typst file is regenerated from YAML on every render.

### Fix
Never edit the generated files. All customization goes in the YAML via `design.templates.*` overrides.

---

## 2-Page Overflow

### Symptom
Resume renders to 2.5 or 3 pages.

### Cause
Too much content. Common culprits:
- 5+ bullets on every experience entry
- Early experience kept when it should be dropped
- Summary is 4+ lines
- Skills section has 6+ categories

### Fix (priority order)
1. Drop or compress pre-2013 experience (see decision-frameworks.md)
2. Trim bullets on older roles to 1-2 each
3. Compress Summary to 2 lines
4. Merge Skills categories (e.g., "Data & Storage" into "Cloud & Infra")
5. Last resort: reduce font size via `design.typography.font_size`

---

## Single-Page Under-Fill

### Symptom
Resume renders as 1.3 pages — looks awkward, half of page 2 empty.

### Cause
Not enough content to justify page 2, too much for page 1.

### Fix
- Expand bullets on 2-3 recent roles with more detail
- Add an optional section: "Selected Projects" or "Public Writing" or "Speaking"
- Or shrink to fit one page (reduce margins, font size slightly)

---

## Cross-Machine iCloud Sync Lag

### Symptom
Edit YAML on Mac A, render on Mac B immediately, changes don't appear.

### Cause
iCloud sync delay (~30-60s typical, can be longer for large files).

### Fix
- Wait 60s before rendering on second machine
- Force sync: `touch <file>` on Mac A
- Check status: `brctl status <file>` (macOS)
- Never concurrent-edit the same file from two machines

---

## Obsidian Doesn't Show YAML

### Symptom
`.yaml` files don't appear in Obsidian file tree.

### Cause
Obsidian hides non-markdown files by default.

### Fix
```
Settings (⌘,) → Files and Links → Detect all file extensions: ON
```

For editable YAML inside Obsidian, install the **"Custom File Extensions Plugin"** and add `yaml,yml` to markdown-handled extensions.

Better option: edit YAML in VS Code / Cursor (gets YAML schema validation from the `$schema` line).

---

## Font Missing in PDF

### Symptom
PDF renders with fallback font (looks wrong), Typst warning about missing font.

### Cause
Theme references a font not installed on the machine. `engineeringresumes` uses Libertinus Serif by default (usually OK on macOS, missing on fresh Linux).

### Fix
- Install the font system-wide, or
- Override in YAML:
  ```yaml
  design:
    theme: engineeringresumes
    typography:
      font_family: "New Computer Modern"  # ships with Typst
  ```

---

## PATH Not Set (rendercv Not Found)

### Symptom
```
zsh: command not found: rendercv
```

### Cause
uv installs tools to `~/.local/bin` which isn't in default macOS PATH.

### Fix
Add once to shell rc:
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

For ad-hoc use in one session:
```bash
export PATH="$HOME/.local/bin:$PATH"
```
