"""Tests for scripts/agy-identity-hook.py (Antigravity CLI hooks) - run as a subprocess."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "agy-identity-hook.py"

CONV_ID = "7b4f7804-4ce4-4b61-8704-6c97b60d54e5"


def _payload(**extra) -> str:
	base = {
		"conversationId": CONV_ID,
		"workspacePaths": ["C:/Work/agy-probe"],
		"transcriptPath": "C:/Users/x/.gemini/antigravity-cli/brain/x/t.jsonl",
		"modelName": "gemini-pro-agent",
	}
	base.update(extra)
	return json.dumps(base)


def _mcp_tool_call(tool: str, arguments: dict) -> dict:
	return {"toolCall": {"name": "call_mcp_tool", "args": {"ServerName": "switchboard", "ToolName": tool, "Arguments": arguments}}, "stepIdx": 5}


def _run(event: str, stdin: str, base_url: str | None = None) -> subprocess.CompletedProcess:
	import os
	env = os.environ.copy()
	env.pop("SWITCHBOARD_TOKEN", None)
	# Point at a closed port by default so status POSTs fail fast and silently.
	env["SWITCHBOARD_BASE_URL"] = base_url or "http://127.0.0.1:1"
	return subprocess.run(
		[sys.executable, str(SCRIPT), "--event", event],
		input=stdin, capture_output=True, text=True, timeout=15, env=env,
	)


class _PostCapturingServer:
	"""Minimal HTTP server capturing POST bodies to /agent_status; responds with a fixed JSON payload."""

	def __init__(self, response: dict | None = None):
		self.response = response if response is not None else {}
		self.posts: list[dict] = []
		self._httpd = None
		self._thread = None
		self.port = None

	def __enter__(self):
		import http.server
		import threading
		posts = self.posts
		response = self.response

		class Handler(http.server.BaseHTTPRequestHandler):
			def do_POST(self):
				length = int(self.headers.get("Content-Length", 0))
				posts.append(json.loads(self.rfile.read(length)))
				body = json.dumps(response).encode("utf-8")
				self.send_response(200)
				self.send_header("Content-Type", "application/json")
				self.send_header("Content-Length", str(len(body)))
				self.end_headers()
				self.wfile.write(body)

			def log_message(self, *args):
				pass

		self._httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
		self.port = self._httpd.server_port
		self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
		self._thread.start()
		return self

	def __exit__(self, *exc):
		self._httpd.shutdown()
		self._httpd.server_close()
		return False


def test_preinvocation_emits_identity_ephemeral():
	result = _run("PreInvocation", _payload())
	assert result.returncode == 0
	out = json.loads(result.stdout)
	steps = out["injectSteps"]
	assert len(steps) == 1
	msg = steps[0]["ephemeralMessage"]
	assert CONV_ID in msg
	assert "C:/Work/agy-probe" in msg
	assert "cli_session_id" in msg


def test_preinvocation_posts_status_and_appends_notices():
	with _PostCapturingServer(response={"notices": ["John convened you into conv-abc."]}) as srv:
		result = _run("PreInvocation", _payload(), base_url=f"http://127.0.0.1:{srv.port}")
	out = json.loads(result.stdout)
	steps = out["injectSteps"]
	assert len(steps) == 2
	assert "convened" in steps[1]["ephemeralMessage"]
	assert len(srv.posts) == 1
	post = srv.posts[0]
	assert post["session_id"] == CONV_ID
	assert post["event"] == "UserPromptSubmit"
	assert post["state"] == "thinking"
	assert post["cli"] == "antigravity"
	assert post["cwd"] == "C:/Work/agy-probe"


def test_pretooluse_allows_compliant_switchboard_call():
	call = _mcp_tool_call("lookup_conversation_ids", {"title_contains": "x", "cli_session_id": CONV_ID, "cwd": "C:/Work/agy-probe"})
	result = _run("PreToolUse", _payload(**call))
	out = json.loads(result.stdout)
	assert out == {"decision": "allow"}


def test_pretooluse_denies_missing_identity_with_corrective_reason():
	call = _mcp_tool_call("lookup_conversation_ids", {"title_contains": "x"})
	result = _run("PreToolUse", _payload(**call))
	out = json.loads(result.stdout)
	assert out["decision"] == "deny"
	assert CONV_ID in out["reason"]
	assert "Arguments" in out["reason"]


def test_pretooluse_denies_wrong_identity():
	call = _mcp_tool_call("ask_human", {"question": "q", "sender": "s", "cli_session_id": "wrong-id"})
	result = _run("PreToolUse", _payload(**call))
	out = json.loads(result.stdout)
	assert out["decision"] == "deny"


def test_pretooluse_allows_non_switchboard_tools():
	payload = _payload(toolCall={"name": "view_file", "args": {"AbsolutePath": "C:\\x\\y.txt"}}, stepIdx=2)
	result = _run("PreToolUse", payload)
	out = json.loads(result.stdout)
	assert out == {"decision": "allow"}


def test_pretooluse_status_states_for_switchboard_tools():
	cases = [
		("ask_human", "clear"),
		("message_and_await_agent", "waiting"),
		("notify_human", "tool:notify_human"),
	]
	for tool, expected_state in cases:
		call = _mcp_tool_call(tool, {"sender": "s", "cli_session_id": CONV_ID})
		with _PostCapturingServer() as srv:
			_run("PreToolUse", _payload(**call), base_url=f"http://127.0.0.1:{srv.port}")
		assert srv.posts and srv.posts[0]["state"] == expected_state, tool
		assert srv.posts[0]["event"] == "PreToolUse"


def test_pretooluse_no_status_post_on_deny():
	call = _mcp_tool_call("ask_human", {"question": "q", "sender": "s"})
	with _PostCapturingServer() as srv:
		_run("PreToolUse", _payload(**call), base_url=f"http://127.0.0.1:{srv.port}")
	assert srv.posts == []


def test_posttooluse_posts_thinking_and_emits_empty_object():
	with _PostCapturingServer() as srv:
		result = _run("PostToolUse", _payload(stepIdx=7), base_url=f"http://127.0.0.1:{srv.port}")
	assert json.loads(result.stdout) == {}
	assert srv.posts and srv.posts[0]["state"] == "thinking"
	assert srv.posts[0]["event"] == "PostToolUse"


def test_fails_open_on_garbage_stdin():
	# Empty payload means no conversationId: PreInvocation and PostToolUse emit
	# a bare {}, PreToolUse emits a bare allow. All exit 0.
	r1 = _run("PreInvocation", "not json {{{")
	assert r1.returncode == 0 and json.loads(r1.stdout) == {}
	r2 = _run("PreToolUse", "not json {{{")
	assert r2.returncode == 0 and json.loads(r2.stdout) == {"decision": "allow"}
	r3 = _run("PostToolUse", "not json {{{")
	assert r3.returncode == 0 and json.loads(r3.stdout) == {}


def test_fails_open_when_server_down():
	result = _run("PreInvocation", _payload())
	assert result.returncode == 0
	out = json.loads(result.stdout)
	assert len(out["injectSteps"]) == 1
