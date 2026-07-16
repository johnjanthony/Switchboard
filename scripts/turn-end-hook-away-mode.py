"""Turn-end hook for Claude Code (Stop), Gemini CLI (AfterAgent), and Antigravity CLI (Stop).

Queries the local Switchboard gateway at turn end and, when the agent must
not silently end its turn, emits the appropriate block/deny JSON on stdout.

Single check: `GET /away-mode`. The away-mode flag is global, so enforcement
does not depend on any stdin payload field. When the payload carries a
session_id it is forwarded so the server can deliver (and clear) that
session's queued wake notices in the same response.

Antigravity payloads are camelCase; the session key is conversationId (the
agy conversation UUID that serves as cli_session_id everywhere). The block
analog is {"decision": "continue", "reason": ...} per agy's Stop hook
contract. The antigravity mode also POSTs the idle agent status before
deciding (a single Stop entry does both jobs: agy's merge semantics for
multiple Stop handlers are unverified). The idle POST carries no working
directory; the identity hook's status POSTs own the registry record's
working directory.

When away mode is active the agent is forced to route output through the
switchboard MCP tools instead of leaking to the terminal - John is on his
phone, not watching.

Fails open only on genuine errors (connection refused, timeout, non-200,
malformed response, unknown --cli): silent exit 0, so non-Switchboard
sessions are unaffected.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from _hook_common import auth_headers, base_url, read_stdin_json

AWAY_MODE_PATH = "/away-mode"
TIMEOUT_SECONDS = 0.5

REDIRECT_REASON_AWAY_MODE = (
	"You are in away mode. John is on his phone, not watching the terminal.\n"
	"To hand your turn back, call ask_human() and wait for his reply. This is "
	"the correct and ONLY way to end your turn while away.\n"
	"Do NOT end your turn with notify_human(): it is non-blocking, so it will not "
	"end the turn and you will loop straight back to this message. (You may call "
	"notify_human() to push a status update, but you must still end on ask_human().)\n"
	"Only call set_away_mode(False) if John has explicitly told you he is back."
)


def _fetch_state(url: str, session_id: str) -> tuple[bool, list]:
	from urllib.parse import urlencode
	full_url = f"{url}?{urlencode({'session_id': session_id})}" if session_id else url
	req = urllib.request.Request(full_url, headers=auth_headers())
	try:
		with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
			if resp.status != 200:
				return False, []
			data = json.loads(resp.read())
		notices = data.get("notices")
		return bool(data.get("active", False)), list(notices) if isinstance(notices, list) else []
	except (urllib.error.URLError, TimeoutError, ValueError, OSError):
		return False, []


def _emit_claude(reason: str) -> None:
	json.dump({"decision": "block", "reason": reason}, sys.stdout)


def _emit_gemini(reason: str) -> None:
	json.dump(
		{"decision": "deny", "reason": reason, "continue": True},
		sys.stdout,
	)


def _emit_antigravity(reason: str) -> None:
	json.dump({"decision": "continue", "reason": reason}, sys.stdout)


def _post_idle_status(session_id: str) -> None:
	body = {"session_id": session_id, "state": "clear", "event": "Stop", "cli": "antigravity"}
	data = json.dumps(body).encode("utf-8")
	headers = {"Content-Type": "application/json", **auth_headers()}
	req = urllib.request.Request(base_url() + "/agent_status", data=data, headers=headers, method="POST")
	try:
		with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS):
			pass
	except Exception:
		pass


def main() -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--cli", required=False)
	args, _unknown = parser.parse_known_args()

	payload = read_stdin_json()

	if args.cli not in {"claude", "gemini", "antigravity"}:
		return 0

	if args.cli == "antigravity":
		session_id = payload.get("conversationId", "") or ""
	else:
		session_id = payload.get("session_id", "") or ""

	if args.cli == "antigravity" and session_id:
		_post_idle_status(session_id)

	away_url = base_url() + AWAY_MODE_PATH
	active, notices = _fetch_state(away_url, session_id)
	if not active and not notices:
		return 0
	reason_parts = []
	if notices:
		reason_parts.append("\n\n".join(notices))
	if active:
		reason_parts.append(REDIRECT_REASON_AWAY_MODE)
	reason = "\n\n".join(reason_parts)
	if args.cli == "claude":
		_emit_claude(reason)
	elif args.cli == "gemini":
		_emit_gemini(reason)
	else:
		_emit_antigravity(reason)
	return 0


if __name__ == "__main__":
	sys.exit(main())
