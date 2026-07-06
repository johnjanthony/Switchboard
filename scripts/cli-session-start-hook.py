"""SessionStart hook: register the session's birth with the switchboard server.

Fire-and-forget POST, same discipline as agent-status-hook.py: any failure
(server down, timeout, malformed stdin) exits 0 silently. A missed birth
self-heals - the first switchboard MCP call or agent-status event upserts the
session - which is why this is a POST and not a marker file (a missed death
does NOT self-heal, so SessionEnd keeps its marker mechanism).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:9876"
SESSION_START_PATH = "/session_start"
TIMEOUT_SECONDS = 1.0


def main() -> int:
	# Read raw bytes; json.loads handles UTF-8. See cli-session-injector-hook
	# for why sys.stdin text reads mangle UTF-8 on Windows (cp1252 wrapper).
	try:
		raw = sys.stdin.buffer.read()
	except Exception:
		return 0
	try:
		payload = json.loads(raw) if raw else {}
	except Exception:
		return 0

	session_id = payload.get("session_id") or ""
	if not session_id:
		return 0
	body = {
		"session_id": session_id,
		"cwd": payload.get("cwd") or "",
		"source": payload.get("source") or "",
	}

	base_url = os.environ.get("SWITCHBOARD_BASE_URL", DEFAULT_BASE_URL)
	req = urllib.request.Request(
		base_url + SESSION_START_PATH,
		data=json.dumps(body).encode("utf-8"),
		headers={"Content-Type": "application/json"},
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
