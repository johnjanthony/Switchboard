#!/usr/bin/env python
"""Antigravity CLI (agy) hook: session identity + agent status.

Wired via the chezmoi-managed ~/.gemini/config/hooks.json against
PreInvocation, PreToolUse, and PostToolUse. One script, three modes via
--event, so each hook firing costs a single Python spawn.

Identity model: agy hooks cannot rewrite tool arguments (no updatedInput
equivalent, verified against agy 1.1.2), so the model itself must pass
cli_session_id (= agy conversationId) and cwd (= workspacePaths[0]) inside
every switchboard MCP call's Arguments. PreInvocation teaches this every
invocation via an ephemeral system message; PreToolUse enforces it by
denying non-compliant switchboard calls with a corrective reason carrying
the right values. agy surfaces MCP calls as toolCall.name "call_mcp_tool"
with args {ServerName, ToolName, Arguments}.

PreInvocation POSTs /agent_status with event UserPromptSubmit, which also
pops queued session notices server-side; they are delivered to the model as
a second ephemeral message (dropping them would lose them: popped means
consumed).

Fails open everywhere: any error results in exit 0 with benign stdout
(empty JSON or an allow decision), so switchboard being down never breaks
agy. Payload keys are camelCase (protojson). Shares the stdin/base-URL/
Bearer boilerplate with the other hook scripts via _hook_common (a
sys.path[0] sibling in scripts/).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

from _hook_common import auth_headers, base_url, read_stdin_json

AGENT_STATUS_PATH = "/agent_status"
TIMEOUT_SECONDS = 1.0

# Switchboard MCP tool names (unnamespaced: agy nests them under call_mcp_tool).
CLEAR_TOOLS = {"ask_human"}
WAITING_TOOLS = {"message_and_await_agent"}


def _post_status(session_id: str, cwd: str, state: str, event: str, detail: str | None = None) -> dict:
	body = {"session_id": session_id, "state": state, "event": event, "cli": "antigravity"}
	if cwd:
		body["cwd"] = cwd
	if detail is not None:
		body["detail"] = detail
	data = json.dumps(body).encode("utf-8")
	headers = {"Content-Type": "application/json", **auth_headers()}
	req = urllib.request.Request(base_url() + AGENT_STATUS_PATH, data=data, headers=headers, method="POST")
	try:
		with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
			raw = resp.read()
		parsed = json.loads(raw) if raw else {}
		return parsed if isinstance(parsed, dict) else {}
	except Exception:
		return {}


def _identity_message(conv_id: str, workspace: str) -> str:
	return (
		"[switchboard identity] When calling ANY switchboard MCP tool, include "
		f"cli_session_id='{conv_id}' and cwd='{workspace}' INSIDE the tool's "
		"Arguments object, alongside the tool's own arguments. The tool schema's "
		"note that these are injected automatically applies to Claude Code only; "
		"in this CLI nothing injects them, so pass them explicitly on every "
		"switchboard call."
	)


def _context(payload: dict) -> tuple[str, str]:
	conv_id = payload.get("conversationId") or ""
	workspaces = payload.get("workspacePaths") or []
	workspace = workspaces[0] if workspaces and isinstance(workspaces[0], str) else ""
	return conv_id, workspace


def handle_pre_invocation(payload: dict) -> str:
	conv_id, workspace = _context(payload)
	if not conv_id:
		return "{}"
	steps = [{"ephemeralMessage": _identity_message(conv_id, workspace)}]
	response = _post_status(conv_id, workspace, "thinking", "UserPromptSubmit")
	notices = response.get("notices")
	if isinstance(notices, list) and notices:
		steps.append({"ephemeralMessage": "\n\n".join(str(n) for n in notices)})
	return json.dumps({"injectSteps": steps})


def _build_detail(tool_name: str, args: dict) -> str | None:
	if tool_name == "run_command":
		cmd = args.get("CommandLine")
		return cmd[:200] if isinstance(cmd, str) and cmd else None
	if tool_name in ("view_file", "write_to_file"):
		path = args.get("AbsolutePath")
		if isinstance(path, str) and path:
			return path.replace("\\", "/").rsplit("/", 1)[-1][:200]
	return None


def handle_pre_tool_use(payload: dict) -> str:
	conv_id, workspace = _context(payload)
	tool_call = payload.get("toolCall") or {}
	tool_name = tool_call.get("name") or ""
	args = tool_call.get("args") or {}

	state = f"tool:{tool_name}"
	detail = _build_detail(tool_name, args if isinstance(args, dict) else {})

	if tool_name == "call_mcp_tool" and isinstance(args, dict) and args.get("ServerName") == "switchboard":
		mcp_tool = args.get("ToolName") or ""
		mcp_args = args.get("Arguments") if isinstance(args.get("Arguments"), dict) else {}
		if conv_id and mcp_args.get("cli_session_id") != conv_id:
			return json.dumps({
				"decision": "deny",
				"reason": (
					"Missing or wrong session identity (not a permission problem). "
					"Retry the same switchboard tool call with two entries added "
					f"INSIDE the Arguments object: \"cli_session_id\": \"{conv_id}\" "
					f"and \"cwd\": \"{workspace}\", keeping your other arguments unchanged."
				),
			})
		if mcp_tool in CLEAR_TOOLS:
			state = "clear"
		elif mcp_tool in WAITING_TOOLS:
			state = "waiting"
		else:
			state = f"tool:{mcp_tool}"
		detail = None

	if conv_id:
		_post_status(conv_id, workspace, state, "PreToolUse", detail)
	return json.dumps({"decision": "allow"})


def handle_post_tool_use(payload: dict) -> str:
	conv_id, workspace = _context(payload)
	if conv_id:
		_post_status(conv_id, workspace, "thinking", "PostToolUse")
	return "{}"


def main() -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--event", required=False)
	args, _unknown = parser.parse_known_args()
	payload = read_stdin_json()
	fallback = '{"decision": "allow"}' if args.event == "PreToolUse" else "{}"
	try:
		if args.event == "PreInvocation":
			out = handle_pre_invocation(payload)
		elif args.event == "PreToolUse":
			out = handle_pre_tool_use(payload)
		elif args.event == "PostToolUse":
			out = handle_post_tool_use(payload)
		else:
			out = "{}"
	except Exception:
		out = fallback
	print(out)
	return 0


if __name__ == "__main__":
	sys.exit(main())
