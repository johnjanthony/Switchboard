"""Turn-end hook for Claude Code (Stop) and Gemini CLI (AfterAgent).

Reads the working directory from stdin JSON (Claude Code passes it as
`{"cwd": "..."}` in the Stop hook payload) and queries the local Switchboard
gateway at `/away-mode?cwd=<cwd>`. When that cwd's away mode is active, emits
the appropriate block/deny JSON on stdout so the agent is forced to keep the
turn alive and route output through ask_human / notify_human instead of leaking
to the terminal.

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

DEFAULT_URL = "http://127.0.0.1:9876/away-mode"
TIMEOUT_SECONDS = 0.5

REDIRECT_REASON = (
	"You are in away mode. John is not watching the terminal. End this turn "
	"by calling ask_human() to check in, or notify_human() to report status "
	"followed by ask_human(). Do not produce terminal output. If John states "
	"that he has returned, call exit_away_mode() first."
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

	url = os.environ.get("SWITCHBOARD_URL", DEFAULT_URL)
	if not _fetch_active(url, cwd):
		return 0

	if args.cli == "claude":
		_emit_claude()
	else:
		_emit_gemini()
	return 0


if __name__ == "__main__":
	sys.exit(main())
