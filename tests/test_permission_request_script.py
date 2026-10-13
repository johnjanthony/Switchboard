"""plugin/scripts/permission-request.sh, the PermissionRequest command hook,
run with bash against a small local HTTP server."""

import json
import os
import shutil
import subprocess
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "permission-request.sh"


def _bash() -> str | None:
	if sys.platform == "win32":
		# Not shutil.which: on Windows that can find System32's WSL launcher.
		git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
		return str(git_bash) if git_bash.exists() else None
	return shutil.which("bash")


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not found")

HOOK_INPUT = {"session_id": "s1", "hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "ls"}}
DECISION = {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}}


class _Server:
	"""Answers each POST with the next (status, body) or (status, body, delay
	seconds) from replies, recording each request's path, headers and body."""

	def __init__(self, replies, port=0):
		self.replies = list(replies)
		self.requests = []
		self.reply_for = None
		outer = self

		class Handler(BaseHTTPRequestHandler):
			def do_POST(self):
				length = int(self.headers.get("Content-Length") or 0)
				outer.requests.append({"path": self.path, "headers": dict(self.headers), "body": self.rfile.read(length)})
				if outer.reply_for:
					reply = outer.reply_for()
				else:
					reply = outer.replies.pop(0) if outer.replies else (204, b"")
				status, body, *delay = reply
				if delay:
					time.sleep(delay[0])
				self.send_response(status)
				self.send_header("Content-Length", str(len(body)))
				self.end_headers()
				self.wfile.write(body)

			def log_message(self, *args):
				pass

		self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
		self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
		threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

	def close(self):
		self.httpd.shutdown()
		self.httpd.server_close()


def _run(base_url, *, token=None, payload=None, retry_seconds="30", extra_env=None):
	env = {k: v for k, v in os.environ.items() if not k.startswith("SWITCHBOARD_")}
	env.update({
		"SWITCHBOARD_BASE_URL": base_url,
		"SWITCHBOARD_APPROVAL_RETRY_SECONDS": retry_seconds,
		"SWITCHBOARD_APPROVAL_RETRY_DELAY": "0.2",
	})
	if token is not None:
		env["SWITCHBOARD_TOKEN"] = token
	env.update(extra_env or {})
	data = json.dumps(payload if payload is not None else HOOK_INPUT).encode()
	return subprocess.run([BASH, str(SCRIPT)], input=data, capture_output=True, env=env, timeout=90)


def test_a_decision_is_printed_and_the_input_is_sent_as_is():
	server = _Server([(200, json.dumps(DECISION).encode())])
	try:
		result = _run(server.url, token="tok-1")
	finally:
		server.close()
	assert result.returncode == 0
	assert json.loads(result.stdout) == DECISION
	(request,) = server.requests
	assert request["path"] == "/approvals"
	assert json.loads(request["body"]) == HOOK_INPUT
	assert request["headers"]["Authorization"] == "Bearer tok-1"


def test_no_token_sends_no_authorization_header():
	server = _Server([(204, b"")])
	try:
		_run(server.url)
	finally:
		server.close()
	assert "Authorization" not in server.requests[0]["headers"]


@pytest.mark.parametrize("status", [204, 401, 404, 429, 500])
def test_any_other_answer_prints_nothing_and_stops(status):
	server = _Server([(status, b'{"error": "x"}')])
	try:
		result = _run(server.url)
	finally:
		server.close()
	assert result.returncode == 0
	assert result.stdout == b""
	assert len(server.requests) == 1


def test_a_503_is_retried_until_the_decision_comes():
	server = _Server([(503, b""), (503, b""), (200, json.dumps(DECISION).encode())])
	try:
		result = _run(server.url)
	finally:
		server.close()
	assert result.returncode == 0
	assert json.loads(result.stdout) == DECISION
	assert len(server.requests) == 3


def test_an_unreachable_server_is_retried_then_given_up_quietly():
	result = _run("http://127.0.0.1:9", retry_seconds="1")
	assert result.returncode == 0
	assert result.stdout == b""


def test_a_server_that_comes_up_after_a_refusal_is_reached():
	with socket.socket() as probe:
		probe.bind(("127.0.0.1", 0))
		port = probe.getsockname()[1]
	outcome = {}
	runner = threading.Thread(target=lambda: outcome.update(result=_run(f"http://127.0.0.1:{port}", retry_seconds="60")))
	runner.start()
	time.sleep(4)
	server = _Server([(200, json.dumps(DECISION).encode())], port=port)
	try:
		runner.join(timeout=60)
	finally:
		server.close()
	result = outcome["result"]
	assert result.returncode == 0
	assert json.loads(result.stdout) == DECISION


def test_a_503_after_a_long_hold_starts_a_fresh_retry_window():
	server = _Server([(503, b"", 3), (200, json.dumps(DECISION).encode())])
	try:
		result = _run(server.url, retry_seconds="2", extra_env={"SWITCHBOARD_APPROVAL_HELD_SECONDS": "1"})
	finally:
		server.close()
	assert result.returncode == 0
	assert json.loads(result.stdout) == DECISION
	assert len(server.requests) == 2


def test_a_second_restart_after_a_quick_503_gets_its_own_window():
	server = _Server([(503, b""), (503, b"", 3), (200, json.dumps(DECISION).encode())])
	try:
		result = _run(server.url, retry_seconds="2", extra_env={"SWITCHBOARD_APPROVAL_HELD_SECONDS": "1"})
	finally:
		server.close()
	assert result.returncode == 0
	assert json.loads(result.stdout) == DECISION
	assert len(server.requests) == 3


def test_a_server_stuck_answering_quick_503s_is_given_up_on():
	cutoff = time.monotonic() + 15
	server = _Server([])
	server.reply_for = lambda: (503, b"") if time.monotonic() < cutoff else (204, b"")
	started = time.monotonic()
	try:
		result = _run(
			server.url, retry_seconds="2", extra_env={"SWITCHBOARD_APPROVAL_HELD_SECONDS": "2"}
		)
	finally:
		server.close()
	assert result.returncode == 0
	assert result.stdout == b""
	assert time.monotonic() - started < 8


def test_malformed_knobs_fall_back_to_the_defaults_quietly():
	server = _Server([(503, b""), (200, json.dumps(DECISION).encode())])
	try:
		result = _run(
			server.url,
			retry_seconds="1 2",
			extra_env={"SWITCHBOARD_APPROVAL_HELD_SECONDS": "abc", "SWITCHBOARD_APPROVAL_RETRY_DELAY": "x"},
		)
	finally:
		server.close()
	assert result.returncode == 0
	assert json.loads(result.stdout) == DECISION
	assert result.stderr == b""


def test_a_large_input_goes_through_whole():
	payload = {**HOOK_INPUT, "tool_name": "Write", "tool_input": {"file_path": "C:/x.txt", "content": "z" * 2_000_000}}
	server = _Server([(204, b"")])
	try:
		_run(server.url, payload=payload)
	finally:
		server.close()
	assert json.loads(server.requests[0]["body"]) == payload


def test_hooks_json_names_the_script_with_a_day_long_timeout():
	hooks = json.loads((SCRIPT.parents[1] / "hooks" / "hooks.json").read_text(encoding="utf-8"))
	assert hooks["modules"] == ["./switchboard.ts"]
	(entry,) = hooks["hooks"]["PermissionRequest"]
	(hook,) = entry["hooks"]
	assert hook == {
		"type": "command",
		"command": 'bash "${CLAUDE_PLUGIN_ROOT}/scripts/permission-request.sh"',
		"timeout": 86400,
	}
