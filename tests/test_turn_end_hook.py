"""Tests for scripts/turn-end-hook-away-mode.py — run as a subprocess."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "turn-end-hook-away-mode.py"

_DEFAULT_CWD_PAYLOAD = json.dumps({"cwd": "c:/work/switchboard"})


def _run(
	cli: str,
	stdin: str = _DEFAULT_CWD_PAYLOAD,
	url_env: str | None = None,
	extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
	"""url_env may be either a full away-mode URL (e.g. http://host:port/away-mode)
	or a bare base URL. The hook reads SWITCHBOARD_BASE_URL and appends /away-mode,
	so this helper strips a trailing /away-mode if present."""
	env = None
	if url_env is not None:
		import os
		env = os.environ.copy()
		env.pop("SWITCHBOARD_TOKEN", None)
		base = url_env[:-len("/away-mode")] if url_env.endswith("/away-mode") else url_env
		env["SWITCHBOARD_BASE_URL"] = base
		if extra_env:
			env.update(extra_env)
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
		self.received_auth: list = []

	def __enter__(self):
		import http.server
		import threading

		payload = self.payload
		status = self.status
		hang = self.hang
		received_paths = self.received_paths
		received_auth = self.received_auth

		class Handler(http.server.BaseHTTPRequestHandler):
			def do_GET(self):
				received_auth.append(self.headers.get("Authorization"))
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


def test_hook_does_not_send_cwd_query_param():
	"""cwd left the protocol entirely - the server route reads only session_id,
	so the hook no longer sends cwd at all."""
	with _FakeServer({"active": True}, record_queries=True) as srv:
		r = _run("claude", stdin=json.dumps({"cwd": "c:/work/myproj"}), url_env=srv.url)
	assert r.returncode == 0
	assert len(srv.received_paths) == 1
	assert "cwd=" not in srv.received_paths[0]
	out = json.loads(r.stdout)
	assert out["decision"] == "block"


def test_hook_missing_cwd_still_enforces():
	"""Away-mode enforcement is global: a payload without cwd must still query
	and block (the old missing-cwd fail-open was a fail-quiet hole)."""
	with _FakeServer({"active": True}) as srv:
		r = _run("claude", stdin=json.dumps({"other": "data"}), url_env=srv.url)
	assert r.returncode == 0
	out = json.loads(r.stdout)
	assert out["decision"] == "block"


def test_hook_empty_stdin_still_enforces():
	with _FakeServer({"active": True}) as srv:
		r = _run("claude", stdin="", url_env=srv.url)
	assert r.returncode == 0
	out = json.loads(r.stdout)
	assert out["decision"] == "block"


def test_hook_invalid_json_stdin_still_enforces():
	with _FakeServer({"active": True}) as srv:
		r = _run("claude", stdin="not json at all", url_env=srv.url)
	assert r.returncode == 0
	out = json.loads(r.stdout)
	assert out["decision"] == "block"


def test_hook_no_session_id_queries_bare_path():
	"""Without a session_id the hook queries /away-mode with no query string."""
	with _FakeServer({"active": False}, record_queries=True) as srv:
		r = _run("claude", stdin=json.dumps({"cwd": "c:/x"}), url_env=srv.url)
	assert r.returncode == 0
	assert srv.received_paths == ["/away-mode"]


# --- T7 / H9: collab-partner-state augmentation ---

class _DualRouteFakeServer:
	"""Like _FakeServer but dispatches by path: /away-mode vs /collab-partner-state.

	Provides two payloads, one for each route. Either may be None to return 404.
	"""

	def __init__(self, away_payload: dict | None, partner_payload: dict | None):
		self.away_payload = away_payload
		self.partner_payload = partner_payload
		self._thread = None
		self._httpd = None
		self.port: int | None = None
		self.received_paths: list[str] = []

	def __enter__(self):
		import http.server
		import threading

		away_payload = self.away_payload
		partner_payload = self.partner_payload
		received = self.received_paths

		class Handler(http.server.BaseHTTPRequestHandler):
			def do_GET(self):
				received.append(self.path)
				if self.path.startswith("/away-mode"):
					payload = away_payload
				elif self.path.startswith("/collab-partner-state"):
					payload = partner_payload
				else:
					self.send_response(404)
					self.end_headers()
					return
				if payload is None:
					self.send_response(404)
					self.end_headers()
					return
				self.send_response(200)
				self.send_header("Content-Type", "application/json")
				self.end_headers()
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
	def away_url(self) -> str:
		return f"http://127.0.0.1:{self.port}/away-mode"

	@property
	def partner_url(self) -> str:
		return f"http://127.0.0.1:{self.port}/collab-partner-state"


def _run_dual(cli: str, away_url: str, partner_url: str, stdin: str = _DEFAULT_CWD_PAYLOAD) -> subprocess.CompletedProcess:
	import os
	env = os.environ.copy()
	# The hook reads SWITCHBOARD_BASE_URL and appends /away-mode itself; strip
	# the trailing path so the base resolves correctly.
	base = away_url[:-len("/away-mode")] if away_url.endswith("/away-mode") else away_url
	env["SWITCHBOARD_BASE_URL"] = base
	env["SWITCHBOARD_PARTNER_STATE_URL"] = partner_url
	return subprocess.run(
		[sys.executable, str(SCRIPT), "--cli", cli],
		input=stdin,
		capture_output=True,
		text=True,
		timeout=10,
		env=env,
	)


def test_partner_blocked_away_mode_active_emits_base_block():
	"""The /collab-partner-state endpoint is gone in the v2 redesign. When away-mode
	is active the hook emits the base block reason regardless of partner state.
	The reason tells the agent to end its turn on ask_human (never on the non-blocking
	notify_human) and mentions set_away_mode; it no longer enumerates the collab tools."""
	with _DualRouteFakeServer({"active": True}, {"state": "blocked"}) as srv:
		r = _run_dual("claude", srv.away_url, srv.partner_url)
	assert r.returncode == 0
	out = json.loads(r.stdout)
	assert out["decision"] == "block"
	assert "away mode" in out["reason"].lower()
	assert "ask_human" in out["reason"]
	assert "notify_human" in out["reason"]
	assert "set_away_mode" in out["reason"]
	# end_collab was retired in the v2 redesign
	assert "end_collab" not in out["reason"]


def test_partner_live_away_mode_active_emits_base_block():
	"""Away-mode active regardless of partner state — base block reason only."""
	with _DualRouteFakeServer({"active": True}, {"state": "live"}) as srv:
		r = _run_dual("claude", srv.away_url, srv.partner_url)
	assert r.returncode == 0
	out = json.loads(r.stdout)
	assert out["decision"] == "block"
	assert "away mode" in out["reason"].lower()
	# No separate partner-blocked clause (that feature was retired)
	assert "end_collab" not in out["reason"]
	assert "partner is blocked" not in out["reason"].lower()


def test_no_session_away_mode_active_emits_base_block():
	"""Away-mode active, any partner state — base block reason only."""
	with _DualRouteFakeServer({"active": True}, {"state": "none"}) as srv:
		r = _run_dual("claude", srv.away_url, srv.partner_url)
	assert r.returncode == 0
	out = json.loads(r.stdout)
	assert out["decision"] == "block"
	assert "away mode" in out["reason"].lower()
	assert "end_collab" not in out["reason"]


def test_partner_state_check_skipped_when_away_mode_inactive():
	"""When away-mode is OFF, the hook exits silently and never queries the
	partner-state route. Gating per Option G design discussion: at-desk dialogs
	with John shouldn't be hindered by the partner-blocked check."""
	with _DualRouteFakeServer({"active": False}, {"state": "blocked"}) as srv:
		r = _run_dual("claude", srv.away_url, srv.partner_url)
	assert r.returncode == 0
	assert r.stdout.strip() == ""
	# Only the away-mode route was queried; partner-state was skipped entirely.
	partner_queries = [p for p in srv.received_paths if p.startswith("/collab-partner-state")]
	assert len(partner_queries) == 0, f"partner-state should not be queried; got {partner_queries}"


def test_gemini_away_mode_active_emits_deny():
	"""Gemini variant: deny + continue=True, with v2 tool instructions."""
	with _DualRouteFakeServer({"active": True}, {"state": "blocked"}) as srv:
		r = _run_dual("gemini", srv.away_url, srv.partner_url)
	assert r.returncode == 0
	out = json.loads(r.stdout)
	assert out["decision"] == "deny"
	assert out["continue"] is True
	assert "away mode" in out["reason"].lower()
	assert "ask_human" in out["reason"]


def test_away_mode_active_emits_block_regardless_of_partner_state_route():
	"""The partner-state endpoint is retired; hook emits the base block when
	away-mode is active, regardless of whether a partner route responds."""
	with _DualRouteFakeServer({"active": True}, partner_payload=None) as srv:  # 404 on partner route
		r = _run_dual("claude", srv.away_url, srv.partner_url)
	assert r.returncode == 0
	out = json.loads(r.stdout)
	assert out["decision"] == "block"
	assert "away mode" in out["reason"].lower()
	# No partner clause
	assert "end_collab" not in out["reason"]
	assert "partner is blocked" not in out["reason"].lower()


# --- Convening chunk 3: session_id + notices ---

def test_hook_sends_session_id_as_query_param():
	"""Hook includes ?session_id=... in the URL it requests, derived from stdin."""
	with _FakeServer({"active": False, "notices": []}, record_queries=True) as srv:
		r = _run("claude", stdin=json.dumps({"cwd": "c:/work/myproj", "session_id": "sess-123"}), url_env=srv.url)
	assert r.returncode == 0
	assert len(srv.received_paths) == 1
	assert "session_id=" in srv.received_paths[0]
	assert "sess-123" in srv.received_paths[0]


def test_notices_only_emits_block_with_notice_reason():
	"""Away mode inactive but notices present still blocks the turn - convening
	is an at-desk operation too."""
	with _FakeServer({"active": False, "notices": ["N"]}) as srv:
		r = _run("claude", url_env=srv.url)
	assert r.returncode == 0
	out = json.loads(r.stdout)
	assert out["decision"] == "block"
	assert out["reason"] == "N"


def test_active_and_notices_combines_reason_notice_first():
	"""When both away mode and notices are present, the notice text precedes
	the away-mode reason in one block."""
	with _FakeServer({"active": True, "notices": ["N"]}) as srv:
		r = _run("claude", url_env=srv.url)
	assert r.returncode == 0
	out = json.loads(r.stdout)
	assert out["decision"] == "block"
	assert "N" in out["reason"]
	assert "away mode" in out["reason"].lower()
	assert out["reason"].index("N") < out["reason"].lower().index("away mode")


def test_inactive_no_notices_silent_exit():
	"""Explicit empty-notices-list case stays silent, matching unchanged behavior."""
	with _FakeServer({"active": False, "notices": []}) as srv:
		r = _run("claude", url_env=srv.url)
	assert r.returncode == 0
	assert r.stdout.strip() == ""


def test_turn_end_hook_sends_bearer_token_when_env_set():
	with _FakeServer({"active": False}) as srv:
		r = _run("claude", url_env=srv.url, extra_env={"SWITCHBOARD_TOKEN": "sekrit-123"})
	assert r.returncode == 0
	assert srv.received_auth == ["Bearer sekrit-123"]


def test_turn_end_hook_no_auth_header_without_token():
	with _FakeServer({"active": False}) as srv:
		r = _run("claude", url_env=srv.url)
	assert r.returncode == 0
	assert srv.received_auth == [None]
