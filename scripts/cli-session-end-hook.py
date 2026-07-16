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
unset, falls back to a path relative to this script, which is correct only
when the plugin runs in-place from the repo. Under the version-gated plugin
cache the fallback dir is NEVER swept, so the hook also drops an
_UNPROVISIONED.txt breadcrumb there naming the env var. Best-effort: never
blocks shutdown.
"""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from _hook_common import read_stdin_json

BREADCRUMB_NAME = "_UNPROVISIONED.txt"
BREADCRUMB_TEXT = (
	"SWITCHBOARD_MARKER_DIR is not set, so SessionEnd markers are written to\n"
	"this fallback directory next to the hook script. If this copy of the\n"
	"script runs from the version-gated Claude Code plugin cache, the\n"
	"switchboard server NEVER sweeps this directory and sessions are never\n"
	"marked dormant. Fix: set the SWITCHBOARD_MARKER_DIR environment variable\n"
	"to the server's <repo>/logs/session-end directory (see the repo\n"
	"CLAUDE.md, Setup). If the plugin runs in-place from the repo checkout,\n"
	"this directory IS the server's sweep dir and markers are applied.\n"
)


def _marker_dir() -> tuple[Path, bool]:
	"""Resolve the marker directory. Returns (dir, used_fallback)."""
	env = os.environ.get("SWITCHBOARD_MARKER_DIR")
	if env:
		return Path(env), False
	return Path(__file__).resolve().parents[1] / "logs" / "session-end", True


def main() -> None:
	payload = read_stdin_json()
	session_id = payload.get("session_id")
	reason = payload.get("reason", "other")
	if not session_id:
		sys.exit(0)
	try:
		marker_dir, used_fallback = _marker_dir()
		marker_dir.mkdir(parents=True, exist_ok=True)
		if used_fallback:
			try:
				(marker_dir / BREADCRUMB_NAME).write_text(BREADCRUMB_TEXT, encoding="utf-8")
			except OSError:
				pass
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
