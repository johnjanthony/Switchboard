"""Tests for scripts/turn-end-hook-away-mode.py — run as a subprocess."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "turn-end-hook-away-mode.py"

_DEFAULT_CWD_PAYLOAD = json.dumps({"cwd": "c:/work/switchboard"})


def _run(cli: str, stdin: str = _DEFAULT_CWD_PAYLOAD, url_env: str | None = None) -> subprocess.CompletedProcess:
	env = None
	if url_env is not None:
		import os
		env = os.environ.copy()
		env["SWITCHBOARD_URL"] = url_env
	return subprocess.run(
		[sys.executable, str(SCRIPT), "--cli", cli],
		input=stdin,
		capture_output=True,
		text=True,
		timeout=10,
		env=env,
	)


class _FakeServer:
	"""Context manager that starts a tiny HTTP server returning a fixed payload.

	Set `record_queries=True` to capture the raw path+query of each GET request.
	"""

	def __init__(self, payload: dict | None, status: int = 200, hang: bool = False, record_queries: bool = False):
		self.payload = payload
		self.status = status
		self.hang = hang
		self.record_queries = record_queries
		self._thread = None
		self._httpd = None
		self.port = None
		self.received_paths: list[str] = []

	def __enter__(self):
		import http.server
		import threading

		payload = self.payload
		status = self.status
		hang = self.hang
		received_paths = self.received_paths

		class Handler(http.server.BaseHTTPRequestHandler):
			def do_GET(self):
				received_paths.append(self.path)
				if hang:
					import time
					time.sleep(5)
					return
				self.send_response(status)
				self.send_header("Content-Type", "application/json")
				self.end_headers()
				if payload is not None:
					self.wfile.write(json.dumps(payload).encode("utf-8"))

			def log_message(self, *a, **kw):
				pass

		self._httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
		self.port = self._httpd.server_address[1]
		self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
		self._thread.start()
		return self

	def __exit__(self, *a):
		self._httpd.shutdown()
		self._httpd.server_close()

	@property
	def url(self) -> str:
		return f"http://127.0.0.1:{self.port}/away-mode"


def test_script_exists():
	assert SCRIPT.exists(), f"Hook script missing at {SCRIPT}"


def test_claude_active_true_emits_block_json():
	with _FakeServer({"active": True}) as srv:
		r = _run("claude", url_env=srv.url)
	assert r.returncode == 0
	out = json.loads(r.stdout)
	assert out["decision"] == "block"
	assert "away mode" in out["reason"].lower()
	assert "ask_human" in out["reason"]


def test_gemini_active_true_emits_deny_json_with_continue():
	with _FakeServer({"active": True}) as srv:
		r = _run("gemini", url_env=srv.url)
	assert r.returncode == 0
	out = json.loads(r.stdout)
	assert out["decision"] == "deny"
	assert out["continue"] is True
	assert "away mode" in out["reason"].lower()


def test_claude_active_false_silent_exit():
	with _FakeServer({"active": False}) as srv:
		r = _run("claude", url_env=srv.url)
	assert r.returncode == 0
	assert r.stdout.strip() == ""


def test_gemini_active_false_silent_exit():
	with _FakeServer({"active": False}) as srv:
		r = _run("gemini", url_env=srv.url)
	assert r.returncode == 0
	assert r.stdout.strip() == ""


def test_connection_refused_silent_exit():
	r = _run("claude", url_env="http://127.0.0.1:1/away-mode")
	assert r.returncode == 0
	assert r.stdout.strip() == ""


def test_http_500_silent_exit():
	with _FakeServer(None, status=500) as srv:
		r = _run("claude", url_env=srv.url)
	assert r.returncode == 0
	assert r.stdout.strip() == ""


def test_malformed_payload_silent_exit():
	import http.server
	import threading

	class Handler(http.server.BaseHTTPRequestHandler):
		def do_GET(self):
			self.send_response(200)
			self.end_headers()
			self.wfile.write(b"not json")

		def log_message(self, *a, **kw):
			pass

	httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
	port = httpd.server_address[1]
	t = threading.Thread(target=httpd.serve_forever, daemon=True)
	t.start()
	try:
		r = _run("claude", url_env=f"http://127.0.0.1:{port}/away-mode")
	finally:
		httpd.shutdown()
		httpd.server_close()
	assert r.returncode == 0
	assert r.stdout.strip() == ""


def test_unknown_cli_exits_silently():
	r = _run("notacli", url_env="http://127.0.0.1:1/away-mode")
	assert r.returncode == 0
	assert r.stdout.strip() == ""


def test_hook_sends_cwd_as_query_param():
	"""Hook includes ?cwd=... in the URL it requests."""
	with _FakeServer({"active": True}, record_queries=True) as srv:
		r = _run("claude", stdin=json.dumps({"cwd": "c:/work/myproj"}), url_env=srv.url)
	assert r.returncode == 0
	assert len(srv.received_paths) == 1
	assert "cwd=" in srv.received_paths[0]
	assert "myproj" in srv.received_paths[0]


def test_hook_missing_cwd_fail_open():
	"""When stdin has no cwd field, hook fails open (exit 0, no block)."""
	with _FakeServer({"active": True}) as srv:
		r = _run("claude", stdin=json.dumps({"other": "data"}), url_env=srv.url)
	assert r.returncode == 0
	assert r.stdout.strip() == ""


def test_hook_empty_stdin_fail_open():
	"""Empty stdin means no cwd — hook fails open."""
	with _FakeServer({"active": True}) as srv:
		r = _run("claude", stdin="", url_env=srv.url)
	assert r.returncode == 0
	assert r.stdout.strip() == ""


def test_hook_invalid_json_stdin_fail_open():
	"""Malformed stdin JSON is treated as no cwd — hook fails open."""
	with _FakeServer({"active": True}) as srv:
		r = _run("claude", stdin="not json at all", url_env=srv.url)
	assert r.returncode == 0
	assert r.stdout.strip() == ""
