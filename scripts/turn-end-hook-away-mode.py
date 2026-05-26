"""Turn-end hook for Claude Code (Stop) and Gemini CLI (AfterAgent).

Reads the working directory from stdin JSON (Claude Code passes it as
`{"cwd": "..."}` in the Stop hook payload) and queries the local Switchboard
gateway. When the agent shouldn't be allowed to silently end its turn, emits
the appropriate block/deny JSON on stdout.

Single check: `GET /away-mode?cwd=<cwd>` — when active, the agent is forced
to route output through the switchboard MCP tools instead of leaking to the
terminal. John is on his phone, not watching.

The old /collab-partner-state endpoint was deleted in the v2 conversations
redesign. Partner state is no longer tracked per-cwd; the talking-stick FIFO
inside Conversation.wait_queue is the source of truth and is not exposed via HTTP.

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
TIMEOUT_SECONDS = 0.5

REDIRECT_REASON_AWAY_MODE = (
	"You are in away mode. John is not watching the terminal. Do not produce "
	"terminal output. Instead:\n"
	"- Use ask_human() to check in or ask a question.\n"
	"- Use notify_human() to send a non-blocking status update.\n"
	"- Use message_and_await_agent() to speak to collab partners and wait for their reply.\n"
	"- Use leave_conversation() to step out of a conversation when done.\n"
	"- Use set_away_mode(False) to turn away mode off if John has returned.\n"
	"End this turn only after routing output through one of the above tools."
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
		return 0  # away-mode inactive — don't block

	if args.cli == "claude":
		_emit_claude(REDIRECT_REASON_AWAY_MODE)
	else:
		_emit_gemini(REDIRECT_REASON_AWAY_MODE)
	return 0


if __name__ == "__main__":
	sys.exit(main())
