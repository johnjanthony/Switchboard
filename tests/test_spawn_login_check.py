"""Unit tests for SpawnHandler._user_has_interactive_session — the no-login
precondition that gates /spawn from launching a wt tab into a non-existent
desktop session. Lives in its own module so the autouse fixture in
test_spawn_handler.py (which patches the method to True for integration tests)
doesn't shadow the real implementation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from server.config import Config
from server.logging_jsonl import JsonlLogger
from server.registry import Registry


def _handler(tmp_path: Path):
	from server.spawn import SpawnHandler
	cfg = Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
		windows_spawn_root=tmp_path,
	)
	backend = AsyncMock()
	return SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())


def _mock_quser(stdout: bytes, returncode: int = 0):
	mock_proc = AsyncMock()
	mock_proc.communicate.return_value = (stdout, b"")
	mock_proc.returncode = returncode
	return AsyncMock(return_value=mock_proc)


@pytest.mark.asyncio
async def test_active_console_session_returns_true(tmp_path):
	"""An Active console session means a user is logged in at the keyboard."""
	handler = _handler(tmp_path)
	stdout = (
		b" USERNAME              SESSIONNAME        ID  STATE   IDLE TIME  LOGON TIME\n"
		b">johnanthony           console             1  Active      .     5/2/2026 1:23 PM\n"
	)
	with patch("asyncio.create_subprocess_exec", _mock_quser(stdout)):
		assert await handler._user_has_interactive_session() is True


@pytest.mark.asyncio
async def test_disconnected_session_returns_true(tmp_path):
	"""A Disc(onnected) session — RDP user disconnected without signing out —
	still counts. A wt tab spawned now becomes visible when they reconnect.
	The SESSIONNAME column is blank for Disc sessions, which shifts whitespace
	separators; the parser must be robust to that."""
	handler = _handler(tmp_path)
	stdout = (
		b" USERNAME              SESSIONNAME        ID  STATE   IDLE TIME  LOGON TIME\n"
		b" johnanthony                               2  Disc        2     5/1/2026 9:15 AM\n"
	)
	with patch("asyncio.create_subprocess_exec", _mock_quser(stdout)):
		assert await handler._user_has_interactive_session() is True


@pytest.mark.asyncio
async def test_no_users_logged_on_returns_false(tmp_path):
	"""quser exits non-zero when no users are logged on. That's the failing
	condition the gate exists to catch."""
	handler = _handler(tmp_path)
	with patch(
		"asyncio.create_subprocess_exec",
		_mock_quser(stdout=b"", returncode=1),
	):
		assert await handler._user_has_interactive_session() is False


@pytest.mark.asyncio
async def test_quser_missing_degrades_open(tmp_path):
	"""If quser doesn't exist (stripped Windows install, non-Windows host, etc.),
	don't block spawn — let the existing schtasks failure path produce its own
	error if the toolchain is genuinely broken."""
	handler = _handler(tmp_path)
	with patch(
		"asyncio.create_subprocess_exec",
		side_effect=FileNotFoundError("quser not found"),
	):
		assert await handler._user_has_interactive_session() is True


@pytest.mark.asyncio
async def test_zero_returncode_with_only_header_returns_false(tmp_path):
	"""Defensive: if quser somehow returns 0 with only a header row (no data
	rows), there's no active session. Real-world this shouldn't happen, but the
	parser shouldn't fall through to True on empty data."""
	handler = _handler(tmp_path)
	stdout = b" USERNAME              SESSIONNAME        ID  STATE   IDLE TIME  LOGON TIME\n"
	with patch("asyncio.create_subprocess_exec", _mock_quser(stdout)):
		assert await handler._user_has_interactive_session() is False


@pytest.mark.asyncio
async def test_resume_aborts_when_no_desktop_session(tmp_path):
	"""P0-4: handle_resume must gate on the desktop-session check like
	handle_fresh: a phone Resume with nobody logged in must abort with a
	notice, not mint a conversation whose wt tab can never launch."""
	from server.registry import Conversation, ConversationMember
	handler = _handler(tmp_path)
	registry = handler._registry
	conv = Conversation(id="conv-src", title="Src")
	member = ConversationMember(
		cli_session_id="sess-dormant", sender="Claude", cwd="C:/Work/X",
		surface="windows", joined_at=0.0, alive=False,
	)
	conv.members_active["Claude"] = member
	registry.conversations["conv-src"] = conv

	with patch(
		"asyncio.create_subprocess_exec",
		_mock_quser(stdout=b"", returncode=1),  # no users logged on
	):
		await handler.handle_resume({
			"type": "resume",
			"source_conversation_id": "conv-src",
			"issued_at": "2026-06-11T00:00:00Z",
		})

	# No continuation conv minted, member untouched, nothing bound, no pending file
	assert all(c.continued_from != "conv-src" for c in registry.conversations.values())
	assert member.alive is False
	assert "sess-dormant" not in registry.session_to_conversation_id
	assert list(tmp_path.glob("spawn-pending-*.json")) == []
	# Away mode must not be auto-enabled by an aborted resume
	assert registry.global_away_mode is False
	# Notice surfaced to the phone (same degrade behavior as handle_fresh)
	handler._backend.send_text.assert_awaited_once()
	notice = handler._backend.send_text.await_args.args[0]
	assert "logged in" in notice
