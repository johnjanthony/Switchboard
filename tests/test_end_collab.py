"""Tests for end_collab tool handler — covers edge cases E1-E6."""

from __future__ import annotations

import asyncio

import pytest

from server.collab import CollabSession
from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from tests.test_gateway_notify_human import RecordingBackend


_CWD = "c:/work/end-collab-test"


def _make_config(tmp_path) -> Config:
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


def _make_session(cwd: str = _CWD) -> CollabSession:
	return CollabSession(cwd=cwd, agent_senders=["Alice", "Bob"], task="t")


@pytest.mark.asyncio
async def test_end_collab_returns_reporter_message_when_hand_off_true(tmp_path):
	registry = Registry()
	registry.add_session(_make_session())
	cfg = _make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))

	result = await handlers.end_collab(_CWD, "Alice", message="bye", hand_off_to_human=True)
	assert result == "ok. You are the designated reporter. Report consensus to John."
	assert registry.get_session(_CWD) is None
	assert registry.was_recently_ended(_CWD)


@pytest.mark.asyncio
async def test_end_collab_returns_partner_reporter_message_when_hand_off_false(tmp_path):
	registry = Registry()
	registry.add_session(_make_session())
	cfg = _make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))

	result = await handlers.end_collab(_CWD, "Alice", message="you take it", hand_off_to_human=False)
	assert result == "ok. Collab ended. Partner is reporting."
	assert registry.get_session(_CWD) is None


@pytest.mark.asyncio
async def test_end_collab_e3_resolves_active_block_with_sentinel(tmp_path):
	"""Partner is currently blocked in message_and_await_agent; end_collab resolves their future
	with the sentinel and the message appended."""
	registry = Registry()
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	# Bob blocks waiting for a message
	bob_task = asyncio.create_task(
		handlers.message_and_await_agent(_CWD, "Bob")
	)
	await asyncio.sleep(0)

	# Alice also enrolls (but doesn't await — she calls end_collab next)
	alice_task = asyncio.create_task(
		handlers.message_and_await_agent(_CWD, "Alice")
	)
	await asyncio.sleep(0)

	# Cancel Alice's await — we just needed her enrolled
	alice_task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await alice_task

	# Now Alice ends collab with a final message
	result = await handlers.end_collab(_CWD, "Alice", message="final summary")
	assert result.startswith("ok. You are the designated reporter")

	# Bob's blocking await should resolve with sentinel + message
	bob_result = await asyncio.wait_for(bob_task, timeout=1.0)
	assert bob_result == "__COLLAB_ENDED__\nfinal summary"


@pytest.mark.asyncio
async def test_end_collab_sentinel_without_message(tmp_path):
	"""When no message is given, sentinel arrives bare."""
	registry = Registry()
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	bob_task = asyncio.create_task(handlers.message_and_await_agent(_CWD, "Bob"))
	await asyncio.sleep(0)

	alice_task = asyncio.create_task(handlers.message_and_await_agent(_CWD, "Alice"))
	await asyncio.sleep(0)
	alice_task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await alice_task

	await handlers.end_collab(_CWD, "Alice")

	bob_result = await asyncio.wait_for(bob_task, timeout=1.0)
	assert bob_result == "__COLLAB_ENDED__"


@pytest.mark.asyncio
async def test_end_collab_e1_simultaneous_calls_second_gets_partner_already_ended(tmp_path):
	"""E1: first call wins; second sees was_recently_ended and gets the partner-ended message."""
	registry = Registry()
	registry.add_session(_make_session())
	cfg = _make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))

	result1 = await handlers.end_collab(_CWD, "Alice", hand_off_to_human=True)
	assert result1.startswith("ok. You are the designated reporter")

	# Bob calls end_collab after Alice — session is gone but breadcrumb remains
	result2 = await handlers.end_collab(_CWD, "Bob", hand_off_to_human=True)
	assert result2 == "ok. You are NOT the reporter; partner already ended. Collab closed."


@pytest.mark.asyncio
async def test_end_collab_e2_pre_enrollment_purges_session(tmp_path):
	"""E2: end_collab with no partner enrolled still purges; partner's first call creates fresh session."""
	registry = Registry()
	# Alice enrolls but Bob has not yet
	session = CollabSession(cwd=_CWD, agent_senders=["Alice"], task="t")
	registry.add_session(session)
	cfg = _make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))

	result = await handlers.end_collab(_CWD, "Alice", message="never mind", hand_off_to_human=False)
	assert result == "ok. Collab ended. Partner is reporting."
	assert registry.get_session(_CWD) is None

	# Bob's subsequent first call creates a brand-new session
	bob_task = asyncio.create_task(handlers.message_and_await_agent(_CWD, "Bob"))
	await asyncio.sleep(0.05)
	new_session = registry.get_session(_CWD)
	assert new_session is not None
	assert new_session is not session  # different object
	assert "Bob" in new_session.agent_senders

	bob_task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await bob_task


@pytest.mark.asyncio
async def test_end_collab_e4_non_member_returns_error(tmp_path):
	"""E4: caller not in agent_senders gets an ERROR."""
	registry = Registry()
	registry.add_session(_make_session())  # members are Alice + Bob
	cfg = _make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))

	result = await handlers.end_collab(_CWD, "Stranger")
	assert result == "ERROR: not a member of this session"
	# Session must NOT be purged by a stranger's call
	assert registry.get_session(_CWD) is not None


@pytest.mark.asyncio
async def test_end_collab_no_session_at_all_returns_error(tmp_path):
	"""When no session has ever existed for cwd, return the not-a-member error."""
	registry = Registry()
	cfg = _make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))

	result = await handlers.end_collab("c:/work/never-created", "Alice")
	assert result == "ERROR: not a member of this session"


@pytest.mark.asyncio
async def test_end_collab_e6_pending_inject_blocks_termination(tmp_path):
	"""E6: pending inject in queue prevents end_collab; agent must drain first."""
	registry = Registry()
	session = _make_session()
	# Stash a pending inject in the session's __inject__ queue
	session._pending["__inject__"] = ["urgent message from John"]
	registry.add_session(session)
	cfg = _make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))

	result = await handlers.end_collab(_CWD, "Alice")
	assert result.startswith("ERROR: human inject queue is non-empty")
	# Session must remain so the agent can drain it
	assert registry.get_session(_CWD) is not None


@pytest.mark.asyncio
async def test_end_collab_resume_via_byo_creates_fresh_session(tmp_path):
	"""After end_collab, the very next message_and_await_agent creates a fresh session
	and clears the recently-ended breadcrumb."""
	registry = Registry()
	registry.add_session(_make_session())
	cfg = _make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))

	await handlers.end_collab(_CWD, "Alice")
	assert registry.was_recently_ended(_CWD)

	# Fresh BYO call — creates a new session and clears the breadcrumb
	new_task = asyncio.create_task(handlers.message_and_await_agent(_CWD, "Charlie"))
	await asyncio.sleep(0.05)
	assert registry.get_session(_CWD) is not None
	assert not registry.was_recently_ended(_CWD)

	new_task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await new_task


@pytest.mark.asyncio
async def test_end_collab_invalid_cwd_returns_error(tmp_path):
	registry = Registry()
	cfg = _make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))

	result = await handlers.end_collab("not-an-absolute-path", "Alice")
	assert result.startswith("ERROR: invalid cwd:")


@pytest.mark.asyncio
async def test_end_collab_logs_to_session_log(tmp_path):
	registry = Registry()
	registry.add_session(_make_session())
	cfg = _make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))

	await handlers.end_collab(_CWD, "Alice", message="bye", hand_off_to_human=True)

	# Session log should contain a line for the end_collab event
	sessions_dir = tmp_path / "sessions"
	assert sessions_dir.exists()
	logs = list(sessions_dir.glob("*.log"))
	assert logs
	body = logs[0].read_text(encoding="utf-8")
	assert "end_collab" in body
	assert "hand_off_to_human=True" in body


@pytest.mark.asyncio
async def test_end_collab_stranger_after_end_returns_not_member(tmp_path):
	"""Stranger (not member of original session) still gets not-a-member error even
	when the recently-ended breadcrumb exists."""
	registry = Registry()
	registry.add_session(_make_session())  # members Alice + Bob
	cfg = _make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))

	await handlers.end_collab(_CWD, "Alice")
	assert registry.was_recently_ended(_CWD)

	# Stranger calls end_collab. The breadcrumb is for Alice/Bob's race, not Stranger.
	result = await handlers.end_collab(_CWD, "Stranger")
	assert result == "ERROR: not a member of this session"


@pytest.mark.asyncio
async def test_end_collab_then_spawn_creates_fresh_session(tmp_path, monkeypatch):
	"""After end_collab, calling the spawn handler for collab creates a fresh session
	and clears the recently-ended breadcrumb."""
	from server.spawn import SpawnHandler
	registry = Registry()
	registry.add_session(_make_session())
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	logger = JsonlLogger(cfg.log_path)
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	
	await handlers.end_collab(_CWD, "Alice")
	assert registry.was_recently_ended(_CWD)

	# Mock spawn_root and subprocess for SpawnHandler
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	proj = spawn_root / "testproj"
	proj.mkdir()
	cfg_with_spawn = Config(
		host="127.0.0.1", port=9876, timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
		spawn_root=spawn_root
	)
	
	monkeypatch.setattr("subprocess.run", lambda *a, **kw: None)
	spawn_handler = SpawnHandler(cfg_with_spawn, backend, logger, registry)
	
	# Simulate /spawn --collab testproj
	# Note: testproj canonicalizes to tmp_path/projects/testproj which is NOT _CWD
	# so we must use the actual path.
	project_cwd = "c:/work/testproj" # manually matched for test predictability
	monkeypatch.setattr("server.spawn.canonicalize_cwd", lambda path: project_cwd)
	
	# Reset breadcrumb for the specific path we're spawning
	registry.mark_session_ended(project_cwd, ["Alice", "Bob"])
	assert registry.was_recently_ended(project_cwd)
	
	await spawn_handler.handle(f"/spawn --collab testproj")
	
	assert registry.get_session(project_cwd) is not None
	assert not registry.was_recently_ended(project_cwd)
