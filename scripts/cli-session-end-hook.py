#!/usr/bin/env python
"""SessionEnd hook: POST {session_id, reason} to switchboard's /cli-session/end
endpoint, which marks the corresponding member dormant.

Best-effort. Won't fire on SIGKILL / BSOD / network loss; T-003 GC mitigates
those cases.

SWITCHBOARD_BASE_URL env var: set this in the shell environment to override the
default localhost URL. WSL agents must set it to the Windows host IP so the
request crosses the WSL-to-Windows boundary (e.g. http://172.x.x.x:9876).
The chezmoi dotfiles template sets this per host.
"""
from __future__ import annotations
import json
import os
import sys
import urllib.request

# Switchboard daemon URL; read from env so WSL agents can point at the Windows
# host IP. chezmoi-template SWITCHBOARD_BASE_URL per host. Default localhost
# for the common Windows-native case.
SWITCHBOARD_URL = os.environ.get("SWITCHBOARD_BASE_URL", "http://127.0.0.1:9876")


def main() -> None:
	try:
		payload = json.load(sys.stdin)
	except Exception:
		sys.exit(0)
	session_id = payload.get("session_id")
	reason = payload.get("reason", "other")
	if not session_id:
		sys.exit(0)
	body = json.dumps({"session_id": session_id, "reason": reason}).encode("utf-8")
	req = urllib.request.Request(
		SWITCHBOARD_URL + "/cli-session/end",
		data=body,
		headers={"Content-Type": "application/json"},
		method="POST",
	)
	try:
		urllib.request.urlopen(req, timeout=4)
	except Exception:
		# Best-effort; don't block claude shutdown on switchboard being down.
		pass


if __name__ == "__main__":
	main()
