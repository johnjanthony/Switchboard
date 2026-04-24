"""Turn-end hook for Claude Code (Stop) and Gemini CLI (AfterAgent).

Queries the local Switchboard gateway; when away mode is active, emits the
appropriate block/deny JSON on stdout so the agent is forced to keep the turn
alive and route output through ask_human / notify_human instead of leaking to
the terminal.

Fails open: any error (connection refused, timeout, unknown --cli, malformed
response) results in silent exit 0, so non-Switchboard sessions are unaffected.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:9876/away-mode"
TIMEOUT_SECONDS = 0.5

REDIRECT_REASON = (
	"You are in away mode. John is not watching the terminal. End this turn "
	"by calling ask_human() to check in, or notify_human() to report status "
	"followed by ask_human(). Do not produce terminal output. If John states "
	"that he has returned, call exit_away_mode() first."
)


def _fetch_active(url: str) -> bool:
	try:
		with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as resp:
			if resp.status != 200:
				return False
			body = resp.read()
		data = json.loads(body)
		return bool(data.get("active", False))
	except (urllib.error.URLError, TimeoutError, ValueError, OSError):
		return False


def _emit_claude() -> None:
	json.dump({"decision": "block", "reason": REDIRECT_REASON}, sys.stdout)


def _emit_gemini() -> None:
	json.dump(
		{"decision": "deny", "reason": REDIRECT_REASON, "continue": True},
		sys.stdout,
	)


def main() -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--cli", required=False)
	args, _unknown = parser.parse_known_args()

	# Drain stdin so the parent does not see a broken pipe. Content is ignored.
	try:
		sys.stdin.read()
	except Exception:
		pass

	if args.cli not in {"claude", "gemini"}:
		return 0

	url = os.environ.get("SWITCHBOARD_URL", DEFAULT_URL)
	if not _fetch_active(url):
		return 0

	if args.cli == "claude":
		_emit_claude()
	else:
		_emit_gemini()
	return 0


if __name__ == "__main__":
	sys.exit(main())
