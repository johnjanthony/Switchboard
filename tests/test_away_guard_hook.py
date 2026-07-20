"""Tests for scripts/away-mode-tool-guard-hook.py - run as a subprocess.

The guard denies the built-in AskUserQuestion tool while away mode is on,
redirecting the agent to ask_human. It must query /away-mode WITHOUT a
session_id (a session_id query pops the session's queued wake notices, which
belong to the turn-end hook's delivery path)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.test_turn_end_hook import _FakeServer

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "away-mode-tool-guard-hook.py"

_ASK_PAYLOAD = json.dumps({
	"session_id": "sess-guard-1",
	"tool_name": "AskUserQuestion",
	"tool_input": {"questions": [{"question": "Pick one?", "options": [{"label": "a"}, {"label": "b"}]}]},
	"cwd": "c:/work/switchboard",
})


def _run(stdin: str = _ASK_PAYLOAD, url_env: str | None = None, extra_env: dict | None = None) -> subprocess.CompletedProcess:
	env = os.environ.copy()
	env.pop("SWITCHBOARD_TOKEN", None)
	if url_env is not None:
		base = url_env[:-len("/away-mode")] if url_env.endswith("/away-mode") else url_env
		env["SWITCHBOARD_BASE_URL"] = base
	if extra_env:
		env.update(extra_env)
	return subprocess.run(
		[sys.executable, str(SCRIPT)],
		input=stdin,
		capture_output=True,
		text=True,
		timeout=10,
		env=env,
	)


def test_script_exists():
	assert SCRIPT.exists(), f"Guard script missing at {SCRIPT}"


def test_away_active_denies_with_redirect_reason():
	with _FakeServer({"active": True}) as srv:
		r = _run(url_env=srv.url)
	assert r.returncode == 0
	out = json.loads(r.stdout)
	hso = out["hookSpecificOutput"]
	assert hso["hookEventName"] == "PreToolUse"
	assert hso["permissionDecision"] == "deny"
	reason = hso["permissionDecisionReason"]
	assert "ask_human" in reason
	assert "suggestions" in reason
	assert "one ask_human call per question" in reason
	assert "set_away_mode(false)" in reason


def test_away_inactive_silent_allow():
	with _FakeServer({"active": False}) as srv:
		r = _run(url_env=srv.url)
	assert r.returncode == 0
	assert r.stdout.strip() == ""


def test_other_tool_short_circuits_without_querying():
	payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
	with _FakeServer({"active": True}) as srv:
		r = _run(stdin=payload, url_env=srv.url)
	assert r.returncode == 0
	assert r.stdout.strip() == ""
	assert srv.received_paths == []


def test_query_omits_session_id():
	"""The stdin payload carries a session_id, but the guard must query the
	bare /away-mode path - a session_id query would pop (steal) wake notices
	owed to the turn-end hook."""
	with _FakeServer({"active": True}, record_queries=True) as srv:
		r = _run(url_env=srv.url)
	assert r.returncode == 0
	assert srv.received_paths == ["/away-mode"]


def test_connection_refused_fail_open():
	r = _run(url_env="http://127.0.0.1:1/away-mode")
	assert r.returncode == 0
	assert r.stdout.strip() == ""


def test_http_500_fail_open():
	with _FakeServer(None, status=500) as srv:
		r = _run(url_env=srv.url)
	assert r.returncode == 0
	assert r.stdout.strip() == ""


def test_empty_stdin_fail_open():
	with _FakeServer({"active": True}) as srv:
		r = _run(stdin="", url_env=srv.url)
	assert r.returncode == 0
	assert r.stdout.strip() == ""


def test_bearer_token_sent_when_env_set():
	with _FakeServer({"active": False}) as srv:
		r = _run(url_env=srv.url, extra_env={"SWITCHBOARD_TOKEN": "sekrit-9"})
	assert r.returncode == 0
	assert srv.received_auth == ["Bearer sekrit-9"]
