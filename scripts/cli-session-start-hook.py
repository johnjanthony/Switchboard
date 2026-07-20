"""SessionStart hook: register the session's birth with the switchboard server.

Fire-and-forget POST, same discipline as agent-status-hook.py: any failure
(server down, timeout, malformed stdin) exits 0 silently. A missed birth
self-heals - the first switchboard MCP call or agent-status event upserts the
session - which is why this is a POST and not a marker file (a missed death
does NOT self-heal, so SessionEnd keeps its marker mechanism).
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from _hook_common import auth_headers, base_url, read_stdin_json

SESSION_START_PATH = "/session_start"
TIMEOUT_SECONDS = 1.0


def main() -> int:
	payload = read_stdin_json()

	session_id = payload.get("session_id") or ""
	if not session_id:
		return 0
	body = {
		"session_id": session_id,
		"cwd": payload.get("cwd") or "",
		"source": payload.get("source") or "",
	}

	headers = {"Content-Type": "application/json", **auth_headers()}
	req = urllib.request.Request(
		base_url() + SESSION_START_PATH,
		data=json.dumps(body).encode("utf-8"),
		headers=headers,
		method="POST",
	)
	try:
		with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
			resp.read()
	except (urllib.error.URLError, TimeoutError, OSError, ValueError):
		pass
	return 0


if __name__ == "__main__":
	sys.exit(main())
