---
name: ai-chrome
description: Use whenever Claude needs to see, read, or pick a tab from the user's *live* Chrome browser — anything that requires real session state (logins, cookies, dynamic JS-rendered content) instead of a fresh fetch. Triggers when the user says "look at the current page", "what does this page say", "read my browser", "what tab am I on", "scrape what's open", "summarize this article I'm reading", "switch to my Outlier tab", or shares a screenshot/URL of something currently open in their browser. Also trigger when another skill (e.g. outlier-rubrics, oa-assistant) needs CDP access to localhost:9222 — this skill is the foundation that launches and manages the debug Chrome those skills depend on. Don't trigger for plain web-fetching tasks where a fresh anonymous request is fine; use WebFetch for that.
---

# ai-chrome

A foundation skill that gives Claude live access to the user's Chrome browser via the Chrome DevTools Protocol (CDP). The user runs Chrome with `--remote-debugging-port` (their `ai-chrome` alias setup); this skill launches it on demand and provides scripts to enumerate tabs, read page content, and switch the active tab. Which port and which browser identity it drives comes from the company profile this machine selects — see **"Project profile"**.

## When to use this skill

Use it whenever the answer depends on *what the user has open right now* — logged-in sessions, JS-rendered single-page apps, in-flight forms, ongoing chat conversations, anything you can't replicate by a clean `WebFetch` from a different IP. Concretely:

- The user says "look at the current page" / "what does this say" while pointing at their browser.
- The user is mid-task on a webapp (Outlier, Linear, GitHub, Gmail, ChatGPT, claude.ai…) and wants you to read or summarize what's there.
- A higher-level skill needs CDP access (e.g. `outlier-rubrics` reads the Outlier task page).

Don't use it when:

- The page is a public URL with no login/state — `WebFetch` is faster and cleaner.
- The user wants you to *interact* with the page (click buttons, fill forms). This skill intentionally only reads. Adding interaction is risky and easy to get wrong; defer to the user to click things themselves and re-run `read_page.py`.

## Project profile

This skill runs on several machines, each working a different company, and each may want a different browser identity. Those values live **inside the skill** (never in the target repo) as `profiles/<company>.md`, one file per company. Same convention as `team`.

```
ai-chrome/.env  →  AI_CHROME_PROFILE=<company>  →  profiles/<company>.md  →  the values
```

Nothing is derived from cwd, so this resolves the same whether you're deep in a project or sitting in `~`. `.env` is committed, so the profile in force is visible rather than hidden; its value is machine-specific and each machine keeps its own.

**You do the resolving — the scripts don't.** They hold no Chrome values and parse no config, so every value has exactly one home: the profile file. Read the two files, then pass the values in. Do this **once** at the start and reuse the same exports for every primitive below:

```bash
# 1. Read ai-chrome/.env    → which company
# 2. Read profiles/<company>.md → the five values
# 3. Export them once, then run any of the scripts:
export PORT=<port> USER_DATA_DIR=<user_data_dir> PROFILE_DIRECTORY=<profile_directory> \
       CHROME_BIN=<chrome_bin> TMUX_SESSION=<tmux_session>

bash <skill-dir>/scripts/ensure_chrome.sh
python3 <skill-dir>/scripts/list_tabs.py
```

`ensure_chrome.sh` needs all five. The three Python primitives need only `PORT` (or `--port`). **Every script exits 5 if what it needs wasn't passed** — loud, never a guess, because guessing means driving the wrong browser identity into someone else's logged-in session.

The profile file is the **single source of truth** for the Chrome parameters. All five keys are **required**:

| key | meaning |
|---|---|
| `port` | CDP debug port |
| `user_data_dir` | Chrome `--user-data-dir` (`~` expands) |
| `profile_directory` | Chrome `--profile-directory` |
| `chrome_bin` | Chrome executable |
| `tmux_session` | tmux session carrying Chrome's output |

**Nothing falls back silently.** Unlike `team`, which can take sane defaults when no profile resolves, there is no safe default here: a baked-in one is precisely the drift that produced the two-windows bug, and a wrong one drives the wrong browser identity. So the scripts carry no Chrome values at all and refuse to run without them.

Keys are flat (`key: value`, one line). That used to be a hard requirement because the script parsed the file with `sed`; now that you read it and the script parses nothing, it's simply that none of these five values has anything nested to express. Keep it that way and the file stays scannable.

**To add a company:** copy an existing `profiles/<company>.md`, change the values, and point `ai-chrome/.env` at it. Give a genuinely different browser identity its own `user_data_dir` **and** `tmux_session` — they travel together, or two identities fight over the same profile dir and tmux pane.

## The workflow

There are three primitives. Run them in order: **ensure → list → read** (and **switch** when you need to pivot tabs).

The `tmux capture-pane` / `tmux attach` examples below use `ai-chrome`, which is the `tmux_session` both current profiles happen to name. Under a profile that sets a different one, substitute its value.

### 1. Make sure Chrome is up: `ensure_chrome.sh`

```bash
bash <skill-dir>/scripts/ensure_chrome.sh
```

Idempotent. If the CDP port already responds, exits 0 immediately. Otherwise spawns `Google Chrome` with `--remote-debugging-port` / `--user-data-dir` / `--profile-directory` *inside a detached tmux session*, and polls until it's reachable. Always run this *first* — every other script will fail with exit code 3 if Chrome isn't running, and re-running it when Chrome already exists is free.

**It needs all five values passed in — see "Project profile" above.** This doc deliberately states no concrete values: two ai-chrome windows used to appear because this file and the script each carried their own copy of the defaults and drifted apart. There is now exactly one copy, in `profiles/<company>.md`.

To confirm the values arrived before starting a browser:

```bash
bash <skill-dir>/scripts/ensure_chrome.sh --print-config
```

Why tmux: Chrome's stdout/stderr (renderer crashes, GPU warnings, certificate errors, devtools `console.log`) would otherwise vanish. With tmux, the output is captured and inspectable.

The five values are **required**, not overrides — see **"Project profile"** for where they come from. `TIMEOUT` is the one genuine option (default 15 seconds); it isn't a Chrome value, just how long to wait for the debug port.

For a one-off experiment you can of course pass a value that differs from the profile — but change the profile instead if you mean it to stick. An ad-hoc export is invisible to the next invocation, which is how the two-windows bug happened in the first place.

#### Inspecting Chrome's output

Once Chrome is running in the tmux session, you (or the user) can read its output:

```bash
# Last ~200 lines of stdout/stderr (non-interactive — good for AI use)
tmux capture-pane -p -t ai-chrome -S -200

# Just the most recent screenful
tmux capture-pane -p -t ai-chrome

# Live attach (user-facing; press Ctrl+b d to detach)
tmux attach -t ai-chrome
```

When `ensure_chrome.sh` itself fails with exit code 3 (Chrome started but never opened the debug port), it auto-prints the last 50 lines of the pane to stderr — that's almost always enough to see what went wrong (locked profile, missing dylib, revoked signature, etc.).

If `remain-on-exit` is set on the window (we set it by default), a crashed Chrome leaves the pane open showing its exit status instead of the session disappearing.

#### Known harmless noise in the log

Don't raise an alarm about these — they appear in every Chrome on every machine and don't affect what the user is doing:

- `ssl_client_socket_impl.cc:924 ... net_error -107` — Chrome's background services (Safe Browsing, GCM push, optimization-guide ML) failing IPv6 SSL handshake to Google endpoints. Chrome falls back to IPv4 silently. Spammy on T-Mobile and other ISPs with flaky IPv6 routing to Google.
- `gcm/registration_request ... DEPRECATED_ENDPOINT` — Google Cloud Messaging endpoint is deprecated; Chrome hasn't updated. Cosmetic.
- `process_mac.cc ... task_policy_set ... invalid argument` — macOS rejects Chrome's QoS hints. Cosmetic, long-standing Chrome bug.
- `crashpad ... RLZ/Crashpad/settings.dat: No such file` — first-run crash-reporter setup looking for a path outside our user-data-dir. Cosmetic.

What *is* worth raising: `Renderer crash`, `Fatal error`, `SIGSEGV`, `Abort trap`, `Failed to bind to remote-debugging-port`, `Profile is locked`, anything starting with `net::ERR_` near a URL the user is actually trying to load.

### 2. See what's open: `list_tabs.py`

```bash
python3 <skill-dir>/scripts/list_tabs.py
```

Prints a JSON array of every real tab (filters out `chrome://`, `devtools://`, extensions, background pages by default). Each entry has `id`, `title`, `url`, `type`. Use this to find the right tab before reading or switching to it. Stdlib only — no Playwright needed. Pass `--include-internal` if you really want to see everything.

### 3. Read a page: `read_page.py`

```bash
python3 <skill-dir>/scripts/read_page.py --url-substring outlier.ai
```

Returns `url`, `title`, `inner_text`, a truncated `html_preview`, and the path to a full-page PNG screenshot at `/tmp/ai_chrome_snap.png`. Read that screenshot back with the `Read` tool when text extraction isn't enough (canvas renders, shadow DOM, visual layout questions).

Selection options (pick one):
- `--url-substring <text>` — case-insensitive match. Most common.
- `--tab-id <id>` — exact match against a CDP target id from `list_tabs.py`. Rare.
- omit both → first non-internal tab across all contexts (good when only one user-facing tab exists).

Other flags:
- `--full-html` — return the full DOM instead of the 20k-char preview. Use sparingly; modern SPAs can be megabytes.
- `--no-screenshot` — skip the PNG when you only need text.
- `--screenshot <path>` — save somewhere other than `/tmp/ai_chrome_snap.png`.

Requires `playwright` (`pip install playwright` — *no* `playwright install` needed since we connect over CDP to existing Chrome rather than launching one).

### 4. Switch tabs: `switch_tab.py`

```bash
python3 <skill-dir>/scripts/switch_tab.py --url-substring outlier.ai
```

Brings a tab to the foreground in the user's window. Useful when the user asks "switch to my X tab" or when you want to make sure the user is looking at the same tab you're reading. Selection works the same as `read_page.py`. Stdlib only.

## How to combine these

The natural chain when the user says "look at what I'm working on":

1. `ensure_chrome.sh` — make sure CDP is reachable.
2. `list_tabs.py` — see what's open. Tell the user a one-line summary of each candidate if there's ambiguity.
3. `read_page.py --url-substring <best-match>` — pull text + screenshot.
4. If `inner_text` is empty or shorter than expected (shadow-DOM apps, canvas renders, lazy-loaded content), fall back to reading the screenshot via the `Read` tool.

When the user says "switch to my X tab and tell me what's there":

1. `ensure_chrome.sh`
2. `switch_tab.py --url-substring X`
3. `read_page.py --url-substring X`

## Failure modes and what to tell the user

- **`ensure_chrome.sh` exits 2** — Chrome.app isn't where expected. Ask the user where Chrome is installed or have them set `CHROME_BIN`.
- **`ensure_chrome.sh` exits 3** — Chrome was launched but never responded on the port. The script auto-prints the last 50 lines of the tmux pane to stderr; read that first. Common causes: a different process already holds the port (`lsof -ti:<port>`), the profile dir is locked by a still-running Chrome instance, or the binary's signature was revoked.
- **`ensure_chrome.sh` exits 4** — `tmux` isn't installed. Suggest `brew install tmux`.
- **Any script exits 5** — a required value wasn't passed in. `ensure_chrome.sh` names which of the five are missing and lists the available profiles; the Python primitives say the CDP port is missing. Resolve the profile (§**"Project profile"**) and pass the values. This is deliberately loud rather than falling back to a baked-in default — a silent fallback both caused the two-windows bug and would risk driving the wrong browser identity. `ensure_chrome.sh --print-config` echoes back what it received.
- **`ensure_chrome.sh` says "NOT in tmux"** — the running Chrome was started outside this skill (e.g. by the `ai-chrome` zsh alias directly, or by a previous command). It still works for CDP; you just can't read its logs. If the user wants tmux logging, they need to quit that Chrome first and re-run `ensure_chrome.sh`.
- **Any script exits 3 (`cannot reach Chrome`)** — should be fixed by re-running `ensure_chrome.sh`. If `ensure_chrome.sh` itself succeeded but the next script can't connect, something exited Chrome between calls — `tmux capture-pane -p -t ai-chrome -S -100` will usually show why.
- **`read_page.py` returns empty `inner_text`** — the page renders into shadow DOM or canvas (common in design tools, some webapps). Fall back to the screenshot path with the `Read` tool.
- **`list_tabs.py` shows nothing** — Chrome is running but has no real tabs open. Tell the user to navigate to the page they want you to see.

## What this skill intentionally doesn't do

- **No interaction** — no clicking, typing, scrolling, or form submission. The user's logged-in session is sensitive and a wrong click is hard to undo. Read-only on purpose.
- **No background polling** — each script invocation is one-shot. If you need to watch for changes, that's a separate skill (`oa-assistant` does this for OA-specific monitoring).
- **No headless launch** — this skill assumes Chrome runs *visibly* on the user's desktop because the whole point is to see what they see. For headless scraping, use Playwright directly.
