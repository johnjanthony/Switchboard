#!/usr/bin/env python
"""PreToolUse hook: inject cli_session_id and cwd into every switchboard MCP call.

Bundled with the switchboard plugin. Registered in hooks/hooks.json with no
matcher; self-filters on tool_name. The hook reads session_id and cwd from
the hook payload and merges them into the tool's input via updatedInput.

Per empirical verification (scripts/verify/test3-hook-injection): updatedInput
REPLACES tool_input despite Claude Code docs claiming merge, so we explicitly
carry forward every original field.
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
