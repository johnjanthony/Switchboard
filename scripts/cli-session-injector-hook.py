#!/usr/bin/env python
"""PreToolUse hook: inject cli_session_id and cwd into every switchboard MCP call.

Bundled with the switchboard plugin. Registered in hooks/hooks.json with no
matcher; self-filters on tool_name. The hook reads session_id and cwd from
the hook payload and merges them into the tool's input via updatedInput.

Per empirical verification (scripts/verify/test3-hook-injection): updatedInput
REPLACES tool_input despite Claude Code docs claiming merge, so we explicitly
carry forward every original field.

NOTE: this hook's hooks.json entry MUST keep a generous timeout (>= 10s). Claude
Code runs Windows hooks through bundled Git Bash, so the wall-clock (bash start +
shell init + Python startup + this script) can exceed a couple of seconds. If the
hook does not exit within its timeout, Claude Code KILLS it and SILENTLY DISCARDS
this updatedInput, and the MCP tool then runs with un-injected args (the server
rejects the call with "cli_session_id required"). A too-short timeout (the old
value was 2s) is exactly the bug documented in docs/2026-06-17-child-session-hook-injection.md.
"""
from __future__ import annotations
import json
import sys


def main() -> None:
	# Read raw bytes from stdin and let json.loads do the UTF-8 decode. Reading
	# via `json.load(sys.stdin)` decodes through sys.stdin's TextIOWrapper, which
	# on Windows defaults to cp1252 + errors='surrogateescape'. That decoder
	# turns valid UTF-8 multi-byte sequences (em-dash 0xE2 0x80 0x94, many
	# emojis) into mojibake or lone surrogate codepoints, which then survive
	# back into the tool input via updatedInput — corrupting every switchboard
	# message Claude sends. Reading bytes-first sidesteps the wrapper entirely.
	try:
		payload = json.loads(sys.stdin.buffer.read())
	except Exception:
		sys.exit(0)
	tool_name = payload.get("tool_name", "")
	if not tool_name.startswith("mcp__switchboard__"):
		sys.exit(0)
	tool_input = payload.get("tool_input", {}) or {}
	merged = dict(tool_input)
	merged["cli_session_id"] = payload.get("session_id")
	merged["cwd"] = payload.get("cwd")
	print(json.dumps({
		"hookSpecificOutput": {
			"hookEventName": "PreToolUse",
			"permissionDecision": "allow",
			"updatedInput": merged,
		}
	}))


if __name__ == "__main__":
	main()
