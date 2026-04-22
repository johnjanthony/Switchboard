"""Tests for CollabSession dataclass."""

import asyncio
import pytest
from server.collab import CollabSession


def _make_session(**kwargs) -> CollabSession:
	defaults = dict(
		session_id="proj-abc1",
		agent_ids=("proj-abc1-1", "proj-abc1-2"),
		task="review the code",
		relay=False,
	)
	defaults.update(kwargs)
	return CollabSession(**defaults)


@pytest.mark.asyncio
async def test_other_agent_returns_partner():
	s = _make_session()
	assert s.other_agent("proj-abc1-1") == "proj-abc1-2"
	assert s.other_agent("proj-abc1-2") == "proj-abc1-1"


@pytest.mark.asyncio
async def test_deliver_resolves_waiting_future():
	s = _make_session()
	future = s.start_waiting("proj-abc1-2")
	assert not future.done()
	s.deliver("proj-abc1-2", "hello from agent 1")
	assert future.done()
	assert future.result() == "hello from agent 1"


@pytest.mark.asyncio
async def test_deliver_buffers_when_nobody_waiting():
	s = _make_session()
	s.deliver("proj-abc1-2", "buffered message")
	# Now agent-2 calls start_waiting — should get buffered message immediately
	future = s.start_waiting("proj-abc1-2")
	assert future.done()
	assert future.result() == "buffered message"


@pytest.mark.asyncio
async def test_start_waiting_returns_pending_message_immediately():
	s = _make_session()
	s.deliver("proj-abc1-1", "queued reply")
	future = s.start_waiting("proj-abc1-1")
	assert future.done()
	assert future.result() == "queued reply"


@pytest.mark.asyncio
async def test_cancel_waiting_removes_future():
	s = _make_session()
	s.start_waiting("proj-abc1-1")
	s.cancel_waiting("proj-abc1-1")
	# After cancel, delivering does not raise
	s.deliver("proj-abc1-1", "too late")
	# Message is buffered since future was removed
	future2 = s.start_waiting("proj-abc1-1")
	assert future2.done()
	assert future2.result() == "too late"


@pytest.mark.asyncio
async def test_deliver_inject_resolves_waiting_agent():
	s = _make_session()
	future = s.start_waiting("proj-abc1-1")
	s.deliver_inject("human says hi")
	assert future.done()
	assert future.result() == "human says hi"


@pytest.mark.asyncio
async def test_deliver_inject_buffers_when_nobody_waiting():
	s = _make_session()
	s.deliver_inject("human message")
	future = s.start_waiting("proj-abc1-2")
	assert future.done()
	assert future.result() == "human message"


@pytest.mark.asyncio
async def test_start_waiting_prefers_agent_specific_over_inject():
	s = _make_session()
	# Both an agent-specific message and an inject are buffered
	s.deliver("proj-abc1-2", "from agent 1")
	s.deliver_inject("from human")
	# Agent 2 calls start_waiting — should get the agent-specific message, not the injection
	future = s.start_waiting("proj-abc1-2")
	assert future.done()
	assert future.result() == "from agent 1"
	# The inject should still be buffered for the next waiter
	future2 = s.start_waiting("proj-abc1-1")
	assert future2.done()
	assert future2.result() == "from human"


# Registry tests

from server.registry import Registry


@pytest.mark.asyncio
async def test_registry_add_session_indexes_both_dicts():
	registry = Registry()
	session = _make_session()
	registry.add_session(session)
	assert registry.get_session("proj-abc1") is session
	assert registry.get_session_for_agent("proj-abc1-1") is session
	assert registry.get_session_for_agent("proj-abc1-2") is session


@pytest.mark.asyncio
async def test_registry_get_session_returns_none_for_unknown():
	registry = Registry()
	assert registry.get_session("no-such-session") is None


@pytest.mark.asyncio
async def test_registry_get_session_for_agent_returns_none_for_unknown():
	registry = Registry()
	assert registry.get_session_for_agent("nobody") is None


@pytest.mark.asyncio
async def test_registry_remove_session_clears_both_indexes():
	registry = Registry()
	session = _make_session()
	registry.add_session(session)
	registry.remove_session("proj-abc1")
	assert registry.get_session("proj-abc1") is None
	assert registry.get_session_for_agent("proj-abc1-1") is None
	assert registry.get_session_for_agent("proj-abc1-2") is None


@pytest.mark.asyncio
async def test_registry_session_coexists_with_pending_requests():
	registry = Registry()
	session = _make_session()
	registry.add_session(session)
	future = registry.add("req1", "some-agent", correlation=42)
	assert registry.get("req1") is not None
	assert registry.get_session("proj-abc1") is session


@pytest.mark.asyncio
async def test_registry_add_session_twice_clears_stale_index():
	registry = Registry()
	session1 = _make_session(
		session_id="proj-abc1",
		agent_ids=("proj-abc1-1", "proj-abc1-2"),
	)
	registry.add_session(session1)

	# Replace with a new session that has the same session_id but different agent_ids
	session2 = CollabSession(
		session_id="proj-abc1",
		agent_ids=("proj-abc1-3", "proj-abc1-4"),
		task="new task",
		relay=False,
	)
	registry.add_session(session2)

	# New agents should resolve to session2
	assert registry.get_session_for_agent("proj-abc1-3") is session2
	assert registry.get_session_for_agent("proj-abc1-4") is session2
	# Old agents should be evicted from the index
	assert registry.get_session_for_agent("proj-abc1-1") is None
	assert registry.get_session_for_agent("proj-abc1-2") is None


# message_and_await_agent tool handler tests

from server.config import Config
from server.gateway import build_tool_handlers, TIMEOUT_SENTINEL
from server.logging_jsonl import JsonlLogger
from tests.test_gateway_notify_human import RecordingBackend


def _make_config(tmp_path) -> Config:
	return Config(
		telegram_bot_token="tok",
		telegram_chat_id="123",
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.mark.asyncio
async def test_message_and_await_agent_unknown_session_returns_error(tmp_path):
	registry = Registry()
	cfg = _make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))
	result = await handlers.message_and_await_agent("no-session", "no-agent", "hi")
	assert result == "ERROR: session not found"


@pytest.mark.asyncio
async def test_message_and_await_agent_wrong_agent_id_returns_error(tmp_path):
	registry = Registry()
	session = _make_session(session_id="proj-abc1")
	registry.add_session(session)
	cfg = _make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))
	result = await handlers.message_and_await_agent("proj-abc1", "intruder", "hi")
	assert result == "ERROR: session not found"


@pytest.mark.asyncio
async def test_message_and_await_agent_two_agents_exchange(tmp_path):
	registry = Registry()
	session = _make_session(session_id="proj-abc1", relay=False)
	registry.add_session(session)
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	# Agent 2 waits first (no message)
	task_b = asyncio.create_task(
		handlers.message_and_await_agent("proj-abc1", "proj-abc1-2")
	)
	await asyncio.sleep(0)

	# Agent 1 sends and waits
	task_a = asyncio.create_task(
		handlers.message_and_await_agent("proj-abc1", "proj-abc1-1", "hello from A")
	)
	await asyncio.sleep(0)

	# Agent 2 should have received "hello from A"
	result_b = await asyncio.wait_for(task_b, timeout=1.0)
	assert result_b == "hello from A"

	# Agent 2 replies
	session.deliver("proj-abc1-1", "hello back from B")
	result_a = await asyncio.wait_for(task_a, timeout=1.0)
	assert result_a == "hello back from B"


@pytest.mark.asyncio
async def test_message_and_await_agent_timeout_returns_sentinel(tmp_path):
	registry = Registry()
	session = _make_session(session_id="proj-abc1")
	registry.add_session(session)
	cfg = Config(
		telegram_bot_token="tok",
		telegram_chat_id="123",
		host="127.0.0.1",
		port=9876,
		timeout_seconds=0.05,
		log_path=str(tmp_path / "log.jsonl"),
	)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))
	result = await handlers.message_and_await_agent("proj-abc1", "proj-abc1-1")
	assert result == TIMEOUT_SENTINEL


@pytest.mark.asyncio
async def test_message_and_await_agent_relay_calls_write_session_message(tmp_path):
	registry = Registry()
	session = _make_session(session_id="proj-abc1", relay=True)
	registry.add_session(session)
	cfg = _make_config(tmp_path)

	class TrackingBackend(RecordingBackend):
		def __init__(self):
			super().__init__()
			self.session_messages = []

		async def write_session_message(self, session_id, agent_id, msg_type, content, request_id=None):
			self.session_messages.append((session_id, agent_id, msg_type, content))

	backend = TrackingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	# Agent 1 sends; we don't await (it blocks waiting for reply)
	task = asyncio.create_task(
		handlers.message_and_await_agent("proj-abc1", "proj-abc1-1", "relay this")
	)
	await asyncio.sleep(0)  # let message_and_await_agent run and create relay task
	await asyncio.sleep(0)  # let relay task execute
	assert len(backend.session_messages) == 1
	assert backend.session_messages[0] == ("proj-abc1", "proj-abc1-1", "collab", "relay this")
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task


@pytest.mark.asyncio
async def test_message_and_await_agent_no_relay_skips_write(tmp_path):
	registry = Registry()
	session = _make_session(session_id="proj-abc1", relay=False)
	registry.add_session(session)
	cfg = _make_config(tmp_path)

	class TrackingBackend(RecordingBackend):
		def __init__(self):
			super().__init__()
			self.session_messages = []

		async def write_session_message(self, session_id, agent_id, msg_type, content, request_id=None):
			self.session_messages.append((session_id, agent_id, msg_type, content))

	backend = TrackingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	task = asyncio.create_task(
		handlers.message_and_await_agent("proj-abc1", "proj-abc1-1", "no relay")
	)
	await asyncio.sleep(0)
	assert len(backend.session_messages) == 0
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task


@pytest.mark.asyncio
async def test_ask_human_in_collab_session_writes_session_message(tmp_path):
	registry = Registry()
	session = _make_session(session_id="proj-abc1", relay=True)
	registry.add_session(session)
	cfg = _make_config(tmp_path)

	class TrackingBackend(RecordingBackend):
		def __init__(self):
			super().__init__()
			self.session_messages = []

		async def write_session_message(self, session_id, agent_id, msg_type, content, request_id=None):
			self.session_messages.append((session_id, agent_id, msg_type, content, request_id))

	backend = TrackingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	task = asyncio.create_task(handlers.ask_human("Is this OK?", "proj-abc1-1"))
	await asyncio.sleep(0)

	# ask_human should have written a session message
	assert len(backend.session_messages) == 1
	sid, aid, mtype, content, rid = backend.session_messages[0]
	assert sid == "proj-abc1"
	assert aid == "proj-abc1-1"
	assert mtype == "ask_human"
	assert content == "Is this OK?"
	assert rid is not None

	# Resolve the pending request to clean up — look up the request in the registry
	# The correlation from RecordingBackend.send_question is the integer counter (1000, 1001, ...)
	registry.resolve_by_correlation(1000, "yes")
	await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_ask_human_outside_collab_session_skips_session_message(tmp_path):
	registry = Registry()
	cfg = _make_config(tmp_path)

	class TrackingBackend(RecordingBackend):
		def __init__(self):
			super().__init__()
			self.session_messages = []

		async def write_session_message(self, *args, **kwargs):
			self.session_messages.append(args)

	backend = TrackingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	task = asyncio.create_task(handlers.ask_human("Overwrite?", "solo-agent"))
	await asyncio.sleep(0)
	assert len(backend.session_messages) == 0
	registry.resolve_by_correlation(1000, "yes")
	await asyncio.wait_for(task, timeout=1.0)


# Spawn flag parsing tests

from server.spawn import _parse_spawn_flags


def test_parse_spawn_flags_default():
	remaining, agents, relay = _parse_spawn_flags("myproject do stuff")
	assert remaining == "myproject do stuff"
	assert agents == 1
	assert relay is False


def test_parse_spawn_flags_agents_2():
	remaining, agents, relay = _parse_spawn_flags("myproject --agents=2 do stuff")
	assert remaining == "myproject do stuff"
	assert agents == 2
	assert relay is False


def test_parse_spawn_flags_relay():
	remaining, agents, relay = _parse_spawn_flags("myproject --relay do stuff")
	assert remaining == "myproject do stuff"
	assert agents == 1
	assert relay is True


def test_parse_spawn_flags_both():
	remaining, agents, relay = _parse_spawn_flags("myproject --agents=2 --relay review auth")
	assert remaining == "myproject review auth"
	assert agents == 2
	assert relay is True


def test_parse_spawn_flags_flags_at_end():
	remaining, agents, relay = _parse_spawn_flags("myproject do stuff --agents=2")
	assert remaining == "myproject do stuff"
	assert agents == 2
	assert relay is False


# Startup sidecar notification tests

import json as _json
from pathlib import Path as _Path
from server.main import _notify_lost_collab_sessions


@pytest.mark.asyncio
async def test_startup_sidecar_notifies_and_clears(tmp_path):
	sidecar = tmp_path / "collab-sessions.json"
	sidecar.write_text(_json.dumps([
		{"session_id": "proj-abc1", "agent_ids": ["proj-abc1-1", "proj-abc1-2"],
		 "task": "review", "created_at": "2026-04-21T00:00:00"},
		{"session_id": "proj-def2", "agent_ids": ["proj-def2-1", "proj-def2-2"],
		 "task": "fix bug", "created_at": "2026-04-21T01:00:00"},
	]), encoding="utf-8")

	backend = RecordingBackend()
	await _notify_lost_collab_sessions(sidecar, backend)

	assert len(backend.sent_notifications) == 2
	texts = [n[1] for n in backend.sent_notifications]
	assert any("proj-abc1" in t for t in texts)
	assert any("proj-def2" in t for t in texts)
	# Sidecar cleared
	assert not sidecar.exists()


@pytest.mark.asyncio
async def test_startup_sidecar_missing_is_noop(tmp_path):
	sidecar = tmp_path / "collab-sessions.json"
	backend = RecordingBackend()
	await _notify_lost_collab_sessions(sidecar, backend)
	assert len(backend.sent_notifications) == 0


@pytest.mark.asyncio
async def test_dispatch_inject_delivers_to_waiting_agent(tmp_path):
	from server.gateway import dispatch_inject_queue

	registry = Registry()
	session = _make_session(session_id="proj-abc1")
	registry.add_session(session)

	future = session.start_waiting("proj-abc1-1")

	async def mock_poll():
		yield ("proj-abc1", "inj1", "hello from john")
		raise asyncio.CancelledError()

	class InjectBackend(RecordingBackend):
		async def poll_inject_messages(self):
			async for item in mock_poll():
				yield item

	backend = InjectBackend()
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))

	try:
		await asyncio.wait_for(
			dispatch_inject_queue(registry, backend, logger),
			timeout=0.5,
		)
	except (asyncio.TimeoutError, asyncio.CancelledError):
		pass

	assert future.done()
	assert future.result() == "hello from john"


# Collab spawn integration tests

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


def _make_spawn_config(tmp_path: Path, spawn_root=None) -> Config:
	return Config(
		telegram_bot_token="tok",
		telegram_chat_id="123",
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
		spawn_root=spawn_root,
	)


@pytest.mark.asyncio
async def test_collab_spawn_writes_agents_array_in_pending_json(tmp_path):
	from server.spawn import SpawnHandler
	(tmp_path / "myproject").mkdir()
	cfg = _make_spawn_config(tmp_path, spawn_root=tmp_path)
	backend = MagicMock()
	backend.send_text = AsyncMock()
	backend.send_spawn_ack = AsyncMock()
	backend.write_session_meta = AsyncMock()
	registry = Registry()

	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)

	with patch("server.spawn.subprocess.run") as mock_run:
		mock_run.return_value = MagicMock(returncode=0)
		await handler.handle("/spawn myproject --agents=2 review auth")

	pending_path = tmp_path / "spawn-pending.json"
	assert pending_path.exists()
	import json as _json
	data = _json.loads(pending_path.read_text())
	assert "agents" in data
	assert len(data["agents"]) == 2
	assert data["agents"][0]["agent_id"].endswith("-1")
	assert data["agents"][1]["agent_id"].endswith("-2")
	assert data["relay"] is False


@pytest.mark.asyncio
async def test_collab_spawn_relay_flag_sets_relay_true(tmp_path):
	from server.spawn import SpawnHandler
	(tmp_path / "myproject").mkdir()
	cfg = _make_spawn_config(tmp_path, spawn_root=tmp_path)
	backend = MagicMock()
	backend.send_text = AsyncMock()
	backend.send_spawn_ack = AsyncMock()
	backend.write_session_meta = AsyncMock()
	registry = Registry()

	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)

	with patch("server.spawn.subprocess.run") as mock_run:
		mock_run.return_value = MagicMock(returncode=0)
		await handler.handle("/spawn myproject --agents=2 --relay review auth")

	import json as _json
	data = _json.loads((tmp_path / "spawn-pending.json").read_text())
	assert data["relay"] is True


@pytest.mark.asyncio
async def test_collab_spawn_writes_sidecar(tmp_path):
	from server.spawn import SpawnHandler
	(tmp_path / "myproject").mkdir()
	cfg = _make_spawn_config(tmp_path, spawn_root=tmp_path)
	backend = MagicMock()
	backend.send_text = AsyncMock()
	backend.send_spawn_ack = AsyncMock()
	backend.write_session_meta = AsyncMock()
	registry = Registry()

	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)

	with patch("server.spawn.subprocess.run") as mock_run:
		mock_run.return_value = MagicMock(returncode=0)
		await handler.handle("/spawn myproject --agents=2 review auth")

	sidecar = tmp_path / "collab-sessions.json"
	assert sidecar.exists()
	import json as _json
	entries = _json.loads(sidecar.read_text())
	assert len(entries) == 1
	assert "session_id" in entries[0]
	assert len(entries[0]["agent_ids"]) == 2


@pytest.mark.asyncio
async def test_collab_spawn_registers_session_in_registry(tmp_path):
	from server.spawn import SpawnHandler
	(tmp_path / "myproject").mkdir()
	cfg = _make_spawn_config(tmp_path, spawn_root=tmp_path)
	backend = MagicMock()
	backend.send_text = AsyncMock()
	backend.send_spawn_ack = AsyncMock()
	backend.write_session_meta = AsyncMock()
	registry = Registry()

	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)

	with patch("server.spawn.subprocess.run") as mock_run:
		mock_run.return_value = MagicMock(returncode=0)
		await handler.handle("/spawn myproject --agents=2 review auth")

	assert len(registry._sessions) == 1


@pytest.mark.asyncio
async def test_single_agent_spawn_unaffected_by_collab_changes(tmp_path):
	from server.spawn import SpawnHandler
	(tmp_path / "myproject").mkdir()
	cfg = _make_spawn_config(tmp_path, spawn_root=tmp_path)
	backend = MagicMock()
	backend.send_text = AsyncMock()
	backend.send_spawn_ack = AsyncMock()
	registry = Registry()

	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)

	with patch("server.spawn.subprocess.run") as mock_run:
		mock_run.return_value = MagicMock(returncode=0)
		await handler.handle("/spawn myproject do stuff")

	import json as _json
	data = _json.loads((tmp_path / "spawn-pending.json").read_text())
	# Single-agent format: no "agents" array
	assert "agents" not in data
	assert "prompt" in data
	assert len(registry._sessions) == 0


@pytest.mark.asyncio
async def test_collab_spawn_sidecar_appends_on_second_spawn(tmp_path):
	from server.spawn import SpawnHandler
	(tmp_path / "myproject").mkdir()
	cfg = _make_spawn_config(tmp_path, spawn_root=tmp_path)
	backend = MagicMock()
	backend.send_text = AsyncMock()
	backend.send_spawn_ack = AsyncMock()
	backend.write_session_meta = AsyncMock()
	registry = Registry()

	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)

	with patch("server.spawn.subprocess.run") as mock_run:
		mock_run.return_value = MagicMock(returncode=0)
		await handler.handle("/spawn myproject --agents=2 first task")
		handler._last_spawn_time = None  # reset rate limit for second spawn
		await handler.handle("/spawn myproject --agents=2 second task")

	import json as _json
	entries = _json.loads((tmp_path / "collab-sessions.json").read_text())
	assert len(entries) == 2
	assert entries[0]["task"] == "first task"
	assert entries[1]["task"] == "second task"


@pytest.mark.asyncio
async def test_collab_spawn_rejects_unsupported_agents_count(tmp_path):
	from server.spawn import SpawnHandler
	(tmp_path / "myproject").mkdir()
	cfg = _make_spawn_config(tmp_path, spawn_root=tmp_path)
	backend = MagicMock()
	backend.send_text = AsyncMock()
	backend.send_spawn_ack = AsyncMock()
	registry = Registry()

	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)

	await handler.handle("/spawn myproject --agents=3 task")

	backend.send_text.assert_called_once()
	assert "Unsupported" in backend.send_text.call_args[0][0]
	assert len(registry._sessions) == 0
