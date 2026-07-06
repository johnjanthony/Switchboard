"""Tests for scripts/agent-status-hook.py. Tested via subprocess to exercise
the actual stdin/HTTP flow."""

import json
import subprocess
import sys
import threading
import http.server
import socketserver
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "scripts" / "agent-status-hook.py"


class _Capture(http.server.BaseHTTPRequestHandler):
	posts: list[dict] = []

	def do_POST(self):
		length = int(self.headers.get("Content-Length", "0"))
		raw = self.rfile.read(length)
		try:
			_Capture.posts.append(json.loads(raw))
		except Exception:
			_Capture.posts.append({"_raw": raw.decode("utf-8", "replace")})
		self.send_response(200)
		self.end_headers()

	def log_message(self, *args, **kwargs):
		pass  # silence


def _start_server():
	_Capture.posts = []
	srv = socketserver.TCPServer(("127.0.0.1", 0), _Capture)
	port = srv.server_address[1]
	thread = threading.Thread(target=srv.serve_forever, daemon=True)
	thread.start()
	return srv, port


def _run_hook(stdin_payload: dict, port: int, env_overrides: dict | None = None):
	import os
	env = dict(os.environ)
	# The hook reads SWITCHBOARD_BASE_URL and appends /agent_status itself.
	env["SWITCHBOARD_BASE_URL"] = f"http://127.0.0.1:{port}"
	if env_overrides:
		env.update(env_overrides)
	result = subprocess.run(
		[sys.executable, str(HOOK)],
		input=json.dumps(stdin_payload).encode("utf-8"),
		capture_output=True,
		env=env,
		timeout=5,
	)
	return result


def test_user_prompt_submit_sends_thinking():
	srv, port = _start_server()
	try:
		_run_hook({
			"hook_event_name": "UserPromptSubmit",
			"session_id": "s-1",
			"prompt": "hi",
		}, port)
	finally:
		srv.shutdown()
	assert len(_Capture.posts) == 1
	body = _Capture.posts[0]
	assert body["state"] == "thinking"
	assert body["session_id"] == "s-1"
	assert "cwd" not in body


def test_pre_tool_use_for_bash_includes_command_detail():
	srv, port = _start_server()
	try:
		_run_hook({
			"hook_event_name": "PreToolUse",
			"session_id": "s-1",
			"tool_name": "Bash",
			"tool_input": {"command": "npm test --watch"},
		}, port)
	finally:
		srv.shutdown()
	body = _Capture.posts[0]
	assert body["state"] == "tool:Bash"
	assert body["detail"] == "npm test --watch"


def test_pre_tool_use_for_edit_includes_filename_detail():
	srv, port = _start_server()
	try:
		_run_hook({
			"hook_event_name": "PreToolUse",
			"session_id": "s-1",
			"tool_name": "Edit",
			"tool_input": {"file_path": "/c/Work/switchboard/server/main.py"},
		}, port)
	finally:
		srv.shutdown()
	body = _Capture.posts[0]
	assert body["state"] == "tool:Edit"
	assert body["detail"] == "main.py"


def test_pre_tool_use_for_ask_human_sends_clear():
	srv, port = _start_server()
	try:
		_run_hook({
			"hook_event_name": "PreToolUse",
			"session_id": "s-1",
			"tool_name": "mcp__switchboard__ask_human",
			"tool_input": {"question": "?"},
		}, port)
	finally:
		srv.shutdown()
	body = _Capture.posts[0]
	assert body["state"] == "clear"
	assert body.get("detail") is None


def test_pre_tool_use_for_message_and_await_agent_sends_waiting():
	srv, port = _start_server()
	try:
		_run_hook({
			"hook_event_name": "PreToolUse",
			"session_id": "s-1",
			"tool_name": "mcp__switchboard__message_and_await_agent",
			"tool_input": {},
		}, port)
	finally:
		srv.shutdown()
	body = _Capture.posts[0]
	assert body["state"] == "waiting"


def test_post_tool_use_sends_thinking():
	srv, port = _start_server()
	try:
		_run_hook({
			"hook_event_name": "PostToolUse",
			"session_id": "s-1",
			"tool_name": "Bash",
			"tool_input": {"command": "ls"},
			"tool_response": {},
		}, port)
	finally:
		srv.shutdown()
	body = _Capture.posts[0]
	assert body["state"] == "thinking"


def test_stop_sends_clear():
	srv, port = _start_server()
	try:
		_run_hook({
			"hook_event_name": "Stop",
			"session_id": "s-1",
		}, port)
	finally:
		srv.shutdown()
	body = _Capture.posts[0]
	assert body["state"] == "clear"


def test_connection_refused_exits_zero(tmp_path):
	# Point at a port nothing is listening on
	import os
	env = dict(os.environ)
	env["SWITCHBOARD_BASE_URL"] = "http://127.0.0.1:1"
	result = subprocess.run(
		[sys.executable, str(HOOK)],
		input=json.dumps({"hook_event_name": "Stop", "session_id": "s-1"}).encode("utf-8"),
		capture_output=True,
		env=env,
		timeout=5,
	)
	assert result.returncode == 0


def test_malformed_stdin_exits_zero():
	import os
	env = dict(os.environ)
	env["SWITCHBOARD_BASE_URL"] = "http://127.0.0.1:1"
	result = subprocess.run(
		[sys.executable, str(HOOK)],
		input=b"not json at all",
		capture_output=True,
		env=env,
		timeout=5,
	)
	assert result.returncode == 0


def test_missing_session_id_exits_zero_no_post():
	srv, port = _start_server()
	try:
		_run_hook({"hook_event_name": "Stop"}, port)  # no session_id
	finally:
		srv.shutdown()
	assert _Capture.posts == []


def test_body_includes_event_matching_hook_event_name():
	srv, port = _start_server()
	try:
		_run_hook({
			"hook_event_name": "PostToolUse",
			"session_id": "s-1",
			"tool_name": "Bash",
			"tool_input": {"command": "ls"},
			"tool_response": {},
		}, port)
	finally:
		srv.shutdown()
	body = _Capture.posts[0]
	assert body["event"] == "PostToolUse"


START_HOOK = Path(__file__).resolve().parent.parent / "scripts" / "cli-session-start-hook.py"


def _run_start_hook(stdin_payload, port, raw_stdin=None):
	import os
	env = dict(os.environ)
	env["SWITCHBOARD_BASE_URL"] = f"http://127.0.0.1:{port}"
	data = raw_stdin if raw_stdin is not None else json.dumps(stdin_payload).encode("utf-8")
	result = subprocess.run(
		[sys.executable, str(START_HOOK)],
		input=data,
		capture_output=True,
		env=env,
		timeout=5,
	)
	return result


def test_session_start_posts_session_id_cwd_source():
	srv, port = _start_server()
	try:
		result = _run_start_hook({
			"session_id": "s-1",
			"cwd": "/c/Work/switchboard",
			"source": "startup",
		}, port)
	finally:
		srv.shutdown()
	assert result.returncode == 0
	assert len(_Capture.posts) == 1
	body = _Capture.posts[0]
	assert body == {"session_id": "s-1", "cwd": "/c/Work/switchboard", "source": "startup"}


def test_session_start_empty_stdin_exits_zero_no_post():
	srv, port = _start_server()
	try:
		result = _run_start_hook(None, port, raw_stdin=b"")
	finally:
		srv.shutdown()
	assert result.returncode == 0
	assert _Capture.posts == []


def test_session_start_malformed_stdin_exits_zero_no_post():
	srv, port = _start_server()
	try:
		result = _run_start_hook(None, port, raw_stdin=b"not json at all")
	finally:
		srv.shutdown()
	assert result.returncode == 0
	assert _Capture.posts == []
