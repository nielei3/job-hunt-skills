#!/usr/bin/env bash
# ensure_chrome.sh — make sure a debug-mode Chrome is running on the CDP port.
#
# Idempotent: if the port already responds, exit 0 immediately. Otherwise spawn
# Google Chrome with --remote-debugging-port / --user-data-dir / --profile-directory
# inside a detached tmux session, and poll until it answers.
#
# Why tmux: Chrome's stdout/stderr (renderer crashes, GPU warnings, console.log
# from devtools) are otherwise lost. With tmux, you (or Claude) can read recent
# output anytime via `tmux capture-pane -p -t <session>`, or attach interactively.
#
# WHERE THE VALUES COME FROM — see SKILL.md "Project profile". This script holds
# NO Chrome values and does NO config parsing. The caller resolves the company
# profile and passes the values in:
#
#   ai-chrome/.env  →  AI_CHROME_PROFILE=<company>
#   profiles/<company>.md  →  read by the model  →  passed here as env vars
#
# That keeps exactly one copy of every value. Two ai-chrome Chromes used to come
# up because this script and the skill doc each carried their own USER_DATA_DIR
# default and disagreed; now neither carries one.
#
# Required (no defaults — a missing one is exit 5, never a guess, because guessing
# means driving the wrong browser identity):
#   PORT  USER_DATA_DIR  PROFILE_DIRECTORY  CHROME_BIN  TMUX_SESSION
#
# Optional:
#   TIMEOUT             default 15 (seconds) — not a Chrome value, just patience
#
# Exit codes:
#   0  Chrome is up and reachable on port $PORT
#   2  Chrome.app not found at the expected path
#   3  Started Chrome but it never became reachable within the timeout
#   4  tmux is not installed (this skill needs tmux for debug-friendly launch)
#   5  A required value was not passed in
set -euo pipefail

# Every value must arrive from the caller. Named one by one so the error says which.
missing=()
for v in PORT USER_DATA_DIR PROFILE_DIRECTORY CHROME_BIN TMUX_SESSION; do
  [ -n "${!v:-}" ] || missing+=("$v")
done
if [ ${#missing[@]} -gt 0 ]; then
  skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  echo "ERROR: not set: ${missing[*]}" >&2
  echo "  These come from the company profile — resolve it first:" >&2
  echo "    1. read $skill_dir/.env          → AI_CHROME_PROFILE=<company>" >&2
  echo "    2. read $skill_dir/profiles/<company>.md → the five values" >&2
  echo "    3. pass them as env vars to this script" >&2
  echo "  See SKILL.md → \"Project profile\". Available: $(ls "$skill_dir/profiles" 2>/dev/null | sed 's/\.md$//' | tr '\n' ' ')" >&2
  exit 5
fi

# Tilde only survives unquoted assignment, so a caller writing USER_DATA_DIR="~/x"
# would otherwise have mkdir create a literal ~ directory. Normalizing is not
# parsing config — it's guarding one footgun.
case "$USER_DATA_DIR" in "~"/*) USER_DATA_DIR="$HOME${USER_DATA_DIR#\~}" ;; esac

TIMEOUT="${TIMEOUT:-15}"

# --print-config: resolve everything, print it, launch nothing. This is how you
# check which profile is in force without starting a browser.
# --print-config: echo back what was passed, launch nothing. Use it to confirm the
# caller resolved the profile correctly before starting a browser.
if [ "${1:-}" = "--print-config" ]; then
  echo "port:              $PORT"
  echo "user_data_dir:     $USER_DATA_DIR"
  echo "profile_directory: $PROFILE_DIRECTORY"
  echo "chrome_bin:        $CHROME_BIN"
  echo "tmux_session:      $TMUX_SESSION"
  exit 0
fi

probe() { curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; }
session_exists() { tmux has-session -t "=${TMUX_SESSION}" 2>/dev/null; }

# Already up? /json/version is the cheapest CDP probe.
if probe; then
  if command -v tmux >/dev/null 2>&1 && session_exists; then
    echo "chrome already running on port ${PORT} (tmux session: ${TMUX_SESSION})"
  else
    echo "chrome already running on port ${PORT} (NOT in tmux — was launched outside this skill; logs unavailable)"
  fi
  exit 0
fi

if ! command -v tmux >/dev/null 2>&1; then
  echo "ERROR: tmux not installed. This skill launches Chrome inside tmux so output is debuggable." >&2
  echo "Install: brew install tmux" >&2
  exit 4
fi

if [ ! -x "$CHROME_BIN" ]; then
  echo "ERROR: Chrome binary not found or not executable: $CHROME_BIN" >&2
  echo "Fix chrome_bin in the company profile, or pass CHROME_BIN for a one-off." >&2
  exit 2
fi

mkdir -p "$USER_DATA_DIR"

# A stale tmux session whose Chrome already died lingers visibly until we kill
# it; reuse-and-respawn via that session is more trouble than it's worth, so
# nuke the empty session and start clean.
if session_exists; then
  tmux kill-session -t "=${TMUX_SESSION}" || true
fi

# tmux runs the command-string through /bin/sh, so we shell-escape the binary
# path (which contains spaces) and the args. printf %q gives us safe quoting.
chrome_cmd=$(printf '%q --remote-debugging-port=%q --user-data-dir=%q --profile-directory=%q' \
  "$CHROME_BIN" "$PORT" "$USER_DATA_DIR" "$PROFILE_DIRECTORY")

tmux new-session -d -s "$TMUX_SESSION" -n chrome "$chrome_cmd"
# remain-on-exit keeps the pane visible after Chrome exits, so a crash leaves a
# corpse you can inspect instead of an empty session.
tmux set-window-option -t "${TMUX_SESSION}:chrome" remain-on-exit on >/dev/null

# Poll until /json/version responds or we hit the timeout.
deadline=$(( $(date +%s) + TIMEOUT ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if probe; then
    echo "chrome started on port ${PORT} (tmux session: ${TMUX_SESSION}; attach: tmux attach -t ${TMUX_SESSION})"
    exit 0
  fi
  sleep 0.3
done

# Timeout. Chrome may have crashed at startup (locked profile, missing libs,
# revoked binary signature). Show the tail of its tmux pane so the caller has
# something to work with instead of an opaque "didn't come up".
echo "ERROR: started Chrome but port ${PORT} never became reachable within ${TIMEOUT}s" >&2
echo "--- last 50 lines from tmux session ${TMUX_SESSION} ---" >&2
tmux capture-pane -p -t "${TMUX_SESSION}:chrome" -S -50 >&2 || true
exit 3
