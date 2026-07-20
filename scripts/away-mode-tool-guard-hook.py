#!/usr/bin/env python
"""PreToolUse guard: deny the built-in AskUserQuestion tool while away mode is on.

AskUserQuestion renders only in the terminal - with John away it blocks the
agent mid-turn and pushes nothing to his phone (the away-mode Stop hook fires
at turn end, not mid-turn, so it never sees the stall). This guard closes the
hole: registered in hooks.json under a "matcher": "AskUserQuestion" PreToolUse
block, it queries the gateway and denies the call with a reason redirecting
the agent to ask_human (option labels become suggestions entries).

The /away-mode query deliberately omits session_id: a session_id query POPS
the session's queued wake notices server-side, and those belong to the
turn-end hook's delivery path - this guard must not steal them.

Fails open: any error (server down, non-200, malformed body, timeout) exits 0
with no output, so the tool call proceeds normally when the gateway cannot be
consulted. The hooks.json timeout must stay >= 10s (Git Bash + Python startup
on Windows; a killed hook's output is silently discarded).
"""
from __future__ import annotations
import json
import sys
import urllib.error
import urllib.request

from _hook_common import auth_headers, base_url, read_stdin_json

AWAY_MODE_PATH = "/away-mode"
TIMEOUT_SECONDS = 0.5

DENY_REASON = (
	"Away mode is ON: John is on his phone and AskUserQuestion renders only in "
	"the terminal he is not watching - it would strand him. This call was "
	"blocked. Re-ask through the switchboard ask_human tool instead: put the "
	"question text in `question` and translate each option label into an entry "
	"in `suggestions` (John taps one on his phone). Ask one ask_human call per "
	"question. For multi-select or option descriptions, fold that context into "
	"the question text - John can always type a free-text reply. Do NOT call "
	"set_away_mode(false); only John's own prompt says when he is back."
)


def _away_mode_active(url: str) -> bool:
	req = urllib.request.Request(url, headers=auth_headers())
	try:
		with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
			if resp.status != 200:
				return False
			data = json.loads(resp.read())
	except (urllib.error.URLError, TimeoutError, ValueError, OSError):
		return False
	return bool(data.get("active", False)) if isinstance(data, dict) else False


def main() -> int:
	payload = read_stdin_json()
	if payload.get("tool_name") != "AskUserQuestion":
		return 0  # belt under the hooks.json matcher
	if not _away_mode_active(base_url() + AWAY_MODE_PATH):
		return 0
	print(json.dumps({
		"hookSpecificOutput": {
			"hookEventName": "PreToolUse",
			"permissionDecision": "deny",
			"permissionDecisionReason": DENY_REASON,
		}
	}))
	return 0


if __name__ == "__main__":
	sys.exit(main())
