"""Agent status hook for Claude Code.

Registered against UserPromptSubmit, PreToolUse, PostToolUse, and Stop. POSTs
the inferred state to switchboard's /agent_status endpoint. Fire-and-forget:
any failure (server unreachable, timeout, malformed stdin) results in silent
exit 0. The script never emits stdout — it cannot influence Claude Code's
decision flow, so it cannot conflict with the away-mode Stop hook.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:9876"
AGENT_STATUS_PATH = "/agent_status"
TIMEOUT_SECONDS = 1.0

# Special-case tool names: switchboard's MCP tools are namespaced as
# `mcp__switchboard__<tool>`.
CLEAR_TOOLS = {
	"mcp__switchboard__ask_human",
}
WAITING_TOOLS = {
	"mcp__switchboard__message_and_await_agent",
}


def _build_detail(tool_name: str, tool_input: dict) -> str | None:
	"""Extract a single-line summary for the status detail field. Returns
	None if no useful detail can be derived. Capped at 200 chars to match
	the server-side write cap; the phone client ellipsizes based on actual
	row width, so we send the full string and let the UI decide how much
	to show."""
	if not isinstance(tool_input, dict):
		return None
	if tool_name == "Bash":
		cmd = tool_input.get("command", "")
		return cmd[:200] if isinstance(cmd, str) and cmd else None
	if tool_name in ("Edit", "Write", "Read", "NotebookEdit"):
		path = tool_input.get("file_path", "")
		if isinstance(path, str) and path:
			return path.replace("\\", "/").rsplit("/", 1)[-1][:200]
		return None
	if tool_name == "WebFetch":
		url = tool_input.get("url", "")
		if isinstance(url, str) and url:
			from urllib.parse import urlparse
			try:
				return (urlparse(url).netloc or url)[:200]
			except Exception:
				return url[:200]
		return None
	if tool_name in ("Glob", "Grep"):
		pattern = tool_input.get("pattern", "")
		return pattern[:200] if isinstance(pattern, str) and pattern else None
	return None


def _post(url: str, body: dict) -> None:
	data = json.dumps(body).encode("utf-8")
	req = urllib.request.Request(
		url,
		data=data,
		headers={"Content-Type": "application/json"},
		method="POST",
	)
	with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
		resp.read()  # drain


def main() -> int:
	try:
		raw = sys.stdin.read()
	except Exception:
		return 0
	try:
		payload = json.loads(raw) if raw else {}
	except Exception:
		return 0

	session_id = payload.get("session_id") or ""
	if not session_id:
		return 0

	event = payload.get("hook_event_name", "")

	state: str | None = None
	detail: str | None = None

	if event == "UserPromptSubmit":
		state = "thinking"
	elif event == "PostToolUse":
		state = "thinking"
	elif event == "Stop":
		state = "clear"
	elif event == "PreToolUse":
		tool_name = payload.get("tool_name", "")
		tool_input = payload.get("tool_input") or {}
		if tool_name in CLEAR_TOOLS:
			state = "clear"
		elif tool_name in WAITING_TOOLS:
			state = "waiting"
		else:
			state = f"tool:{tool_name}"
			detail = _build_detail(tool_name, tool_input)
	else:
		return 0  # unknown event

	base_url = os.environ.get("SWITCHBOARD_BASE_URL", DEFAULT_BASE_URL)
	url = base_url + AGENT_STATUS_PATH
	body = {"session_id": session_id, "state": state}
	if detail is not None:
		body["detail"] = detail
	try:
		_post(url, body)
	except (urllib.error.URLError, TimeoutError, OSError, ValueError):
		pass
	return 0


if __name__ == "__main__":
	sys.exit(main())
