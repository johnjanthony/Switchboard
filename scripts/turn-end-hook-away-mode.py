"""Turn-end hook for Claude Code (Stop) and Gemini CLI (AfterAgent).

Reads the working directory from stdin JSON (Claude Code passes it as
`{"cwd": "..."}` in the Stop hook payload) and queries the local Switchboard
gateway. When the agent shouldn't be allowed to silently end its turn, emits
the appropriate block/deny JSON on stdout.

Two checks compose:

1. **Away-mode** (existing): `GET /away-mode?cwd=<cwd>` — when active, the
   agent is forced to route output through `ask_human` / `notify_human`
   instead of leaking to the terminal. John is on his phone, not watching.

2. **Collab-partner-blocked** (H9 / Option G): when away-mode is active AND
   the cwd has an active collab session with a blocked partner,
   `GET /collab-partner-state?cwd=<cwd>` returns `state: "blocked"`. The
   block message gains a partner-blocked clause so the agent knows to either
   reply via `message_and_await_agent` or close the session via `end_collab`
   before ending its turn. Gated on away-mode because at-desk dialogs with
   John shouldn't be hindered (he's reading the terminal and can intervene).

Fails open: any error (connection refused, timeout, unknown --cli, malformed
response, missing cwd) results in silent exit 0, so non-Switchboard sessions
are unaffected.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_AWAY_MODE_URL = "http://127.0.0.1:9876/away-mode"
DEFAULT_PARTNER_STATE_URL = "http://127.0.0.1:9876/collab-partner-state"
TIMEOUT_SECONDS = 0.5

REDIRECT_REASON_AWAY_MODE = (
	"You are in away mode. John is not watching the terminal. End this turn "
	"by calling ask_human() to check in, or notify_human() to report status "
	"followed by ask_human(). Do not produce terminal output. If John states "
	"that he has returned, call exit_away_mode() first."
)

REDIRECT_REASON_PARTNER_BLOCKED_SUFFIX = (
	"\n\nAdditionally, your collab partner is blocked awaiting your reply. "
	"Send a message via message_and_await_agent (with a non-empty message arg) "
	"or close the session via end_collab before ending your turn."
)


def _fetch_active(url: str, cwd: str) -> bool:
	from urllib.parse import urlencode
	full_url = f"{url}?{urlencode({'cwd': cwd})}"
	try:
		with urllib.request.urlopen(full_url, timeout=TIMEOUT_SECONDS) as resp:
			if resp.status != 200:
				return False
			body = resp.read()
		data = json.loads(body)
		return bool(data.get("active", False))
	except (urllib.error.URLError, TimeoutError, ValueError, OSError):
		return False


def _fetch_partner_state(url: str, cwd: str) -> str:
	"""Returns 'none', 'live', 'blocked', or 'none' on any error (fail-open)."""
	from urllib.parse import urlencode
	full_url = f"{url}?{urlencode({'cwd': cwd})}"
	try:
		with urllib.request.urlopen(full_url, timeout=TIMEOUT_SECONDS) as resp:
			if resp.status != 200:
				return "none"
			body = resp.read()
		data = json.loads(body)
		return str(data.get("state", "none"))
	except (urllib.error.URLError, TimeoutError, ValueError, OSError):
		return "none"


def _emit_claude(reason: str) -> None:
	json.dump({"decision": "block", "reason": reason}, sys.stdout)


def _emit_gemini(reason: str) -> None:
	json.dump(
		{"decision": "deny", "reason": reason, "continue": True},
		sys.stdout,
	)


def main() -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--cli", required=False)
	args, _unknown = parser.parse_known_args()

	stdin_data = ""
	try:
		stdin_data = sys.stdin.read()
	except Exception:
		pass

	if args.cli not in {"claude", "gemini"}:
		return 0

	cwd = ""
	try:
		payload = json.loads(stdin_data) if stdin_data else {}
		cwd = payload.get("cwd", "") or ""
	except Exception:
		cwd = ""

	if not cwd:
		return 0  # fail-open without cwd

	away_url = os.environ.get("SWITCHBOARD_URL", DEFAULT_AWAY_MODE_URL)
	if not _fetch_active(away_url, cwd):
		return 0  # away-mode inactive — don't block, don't even check partner state

	# Away-mode is active — base block message applies. Augment if partner is blocked.
	reason = REDIRECT_REASON_AWAY_MODE
	partner_url = os.environ.get(
		"SWITCHBOARD_PARTNER_STATE_URL", DEFAULT_PARTNER_STATE_URL
	)
	if _fetch_partner_state(partner_url, cwd) == "blocked":
		reason = reason + REDIRECT_REASON_PARTNER_BLOCKED_SUFFIX

	if args.cli == "claude":
		_emit_claude(reason)
	else:
		_emit_gemini(reason)
	return 0


if __name__ == "__main__":
	sys.exit(main())
