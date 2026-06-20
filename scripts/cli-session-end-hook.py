#!/usr/bin/env python
"""SessionEnd hook: write a marker file the switchboard server sweeps to mark
the member dormant.

Claude Code SessionEnd hooks are fire-and-forget and do not block the process
from exiting (see CC hooks docs). A synchronous network POST therefore races
process termination and is dropped intermittently. A filesystem write is fast
enough to win that race, so this hook writes a small marker file instead; the
server's session-end sweep loop applies it via handle_session_end.

Marker dir: SWITCHBOARD_MARKER_DIR if set (the server's <logs>/session-end,
expressed in this surface's path: a Windows path for Windows-native sessions,
a /mnt/c/... path for WSL sessions; set per host by the chezmoi dotfiles). If
unset, falls back to a path relative to this script, which is correct only when
the plugin runs in-place from the repo. Best-effort: never blocks shutdown.
"""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _marker_dir() -> Path:
	env = os.environ.get("SWITCHBOARD_MARKER_DIR")
	if env:
		return Path(env)
	# Fallback: <script>/../logs/session-end (the repo's logs dir when the
	# plugin runs in-place). Set SWITCHBOARD_MARKER_DIR to be sure, especially
	# under WSL where this script's location is not the Windows logs dir.
	return Path(__file__).resolve().parents[1] / "logs" / "session-end"


def main() -> None:
	# Read raw bytes; json.loads handles UTF-8. See cli-session-injector-hook
	# for why we can't use json.load(sys.stdin) on Windows.
	try:
		payload = json.loads(sys.stdin.buffer.read())
	except Exception:
		sys.exit(0)
	session_id = payload.get("session_id")
	reason = payload.get("reason", "other")
	if not session_id:
		sys.exit(0)
	try:
		marker_dir = _marker_dir()
		marker_dir.mkdir(parents=True, exist_ok=True)
		# session_id is a UUID (filesystem-safe); sanitize defensively anyway.
		safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_") or "unknown"
		marker = {
			"session_id": session_id,
			"reason": reason,
			"ended_at": datetime.now(timezone.utc).isoformat(),
		}
		tmp = marker_dir / f"{safe}.json.tmp"
		tmp.write_text(json.dumps(marker), encoding="utf-8")
		os.replace(tmp, marker_dir / f"{safe}.json")
	except Exception:
		# Best-effort; never block claude shutdown on a marker-write failure.
		pass


if __name__ == "__main__":
	main()
