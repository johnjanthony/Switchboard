"""Tests for CollabSession dataclass."""

import asyncio
import pytest
from server.collab import CollabSession


def _make_session(**kwargs) -> CollabSession:
	defaults = dict(
		session_id="proj-abc1",
		agent_senders=("Agent 1", "Agent 2"),
		task="review the code",
	)
	defaults.update(kwargs)
	return CollabSession(**defaults)


@pytest.mark.asyncio
async def test_other_sender_returns_partner():
	s = _make_session()
	assert s.other_sender("Agent 1") == "Agent 2"
	assert s.other_sender("Agent 2") == "Agent 1"


@pytest.mark.asyncio
async def test_deliver_resolves_waiting_future():
	s = _make_session()
	future = s.start_waiting("Agent 2")
	assert not future.done()
	s.deliver("Agent 2", "hello from agent 1")
	assert future.done()
	assert future.result() == "hello from agent 1"


@pytest.mark.asyncio
async def test_deliver_buffers_when_nobody_waiting():
	s = _make_session()
	s.deliver("Agent 2", "buffered message")
	future = s.start_waiting("Agent 2")
	assert future.done()
	assert future.result() == "buffered message"


@pytest.mark.asyncio
async def test_start_waiting_returns_pending_message_immediately():
	s = _make_session()
	s.deliver("Agent 1", "queued reply")
	future = s.start_waiting("Agent 1")
	assert future.done()
	assert future.result() == "queued reply"


@pytest.mark.asyncio
async def test_cancel_waiting_removes_future():
	s = _make_session()
	s.start_waiting("Agent 1")
	s.cancel_waiting("Agent 1")
	s.deliver("Agent 1", "too late")
	future2 = s.start_waiting("Agent 1")
	assert future2.done()
	assert future2.result() == "too late"


@pytest.mark.asyncio
async def test_deliver_inject_resolves_waiting_agent():
	s = _make_session()
	future = s.start_waiting("Agent 1")
	s.deliver_inject("human says hi")
	assert future.done()
	assert future.result() == "human says hi"


@pytest.mark.asyncio
async def test_deliver_inject_buffers_when_nobody_waiting():
	s = _make_session()
	s.deliver_inject("human message")
	future = s.start_waiting("Agent 2")
	assert future.done()
	assert future.result() == "human message"


@pytest.mark.asyncio
async def test_start_waiting_prefers_agent_specific_over_inject():
	s = _make_session()
	s.deliver("Agent 2", "from agent 1")
	s.deliver_inject("from human")
	future = s.start_waiting("Agent 2")
	assert future.done()
	assert future.result() == "from agent 1"
	future2 = s.start_waiting("Agent 1")
	assert future2.done()
	assert future2.result() == "from human"


# Registry tests

from server.registry import Registry


@pytest.mark.asyncio
async def test_registry_add_session_indexes_session():
	registry = Registry()
	session = _make_session()
	registry.add_session(session)
	assert registry.get_session("proj-abc1") is session


@pytest.mark.asyncio
async def test_registry_get_session_returns_none_for_unknown():
	registry = Registry()
	assert registry.get_session("no-such-session") is None


@pytest.mark.asyncio
async def test_registry_remove_session_clears_entry():
	registry = Registry()
	session = _make_session()
	registry.add_session(session)
	registry.remove_session("proj-abc1")
	assert registry.get_session("proj-abc1") is None


@pytest.mark.asyncio
async def test_registry_session_coexists_with_pending_requests():
	registry = Registry()
	session = _make_session()
	registry.add_session(session)
	registry.add("req1", "some-channel", correlation=42)
	assert registry.get("req1") is not None
	assert registry.get_session("proj-abc1") is session


@pytest.mark.asyncio
async def test_registry_add_session_twice_replaces_previous():
	registry = Registry()
	session1 = _make_session(session_id="proj-abc1")
	registry.add_session(session1)
	session2 = CollabSession(
		session_id="proj-abc1",
		agent_senders=("Agent 1", "Agent 2"),
		task="new task",
	)
	registry.add_session(session2)
	assert registry.get_session("proj-abc1") is session2


# message_and_await_agent tool handler tests

from server.config import Config
from server.gateway import build_tool_handlers, TIMEOUT_SENTINEL
from server.logging_jsonl import JsonlLogger
from tests.test_gateway_notify_human import RecordingBackend


def _make_config(tmp_path) -> Config:
	return Config(
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
	result = await handlers.message_and_await_agent("no-session", "Agent 1", "hi")
	assert result == "ERROR: session not found"


@pytest.mark.asyncio
async def test_message_and_await_agent_wrong_sender_returns_error(tmp_path):
	registry = Registry()
	session = _make_session(session_id="proj-abc1")
	registry.add_session(session)
	cfg = _make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))
	result = await handlers.message_and_await_agent("proj-abc1", "Intruder", "hi")
	assert result == "ERROR: session not found"


@pytest.mark.asyncio
async def test_message_and_await_agent_two_agents_exchange(tmp_path):
	registry = Registry()
	session = _make_session(session_id="proj-abc1")
	registry.add_session(session)
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	task_b = asyncio.create_task(
		handlers.message_and_await_agent("proj-abc1", "Agent 2")
	)
	await asyncio.sleep(0)

	task_a = asyncio.create_task(
		handlers.message_and_await_agent("proj-abc1", "Agent 1", "hello from A")
	)
	await asyncio.sleep(0)

	result_b = await asyncio.wait_for(task_b, timeout=1.0)
	assert result_b == "hello from A"

	session.deliver("Agent 1", "hello back from B")
	result_a = await asyncio.wait_for(task_a, timeout=1.0)
	assert result_a == "hello back from B"


@pytest.mark.asyncio
async def test_message_and_await_agent_timeout_returns_sentinel(tmp_path):
	registry = Registry()
	session = _make_session(session_id="proj-abc1")
	registry.add_session(session)
	cfg = Config(
		host="127.0.0.1", port=9876, timeout_seconds=0.05,
		log_path=str(tmp_path / "log.jsonl"),
	)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))
	result = await handlers.message_and_await_agent("proj-abc1", "Agent 1")
	assert result == TIMEOUT_SENTINEL


@pytest.mark.asyncio
async def test_message_and_await_agent_relay_calls_write_channel_message(tmp_path):
	registry = Registry()
	session = _make_session(session_id="proj-abc1")
	registry.add_session(session)
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	task = asyncio.create_task(
		handlers.message_and_await_agent("proj-abc1", "Agent 1", "relay this")
	)
	await asyncio.sleep(0)  # let message_and_await_agent run
	await asyncio.sleep(0.01)  # let relay task execute

	agent_msgs = [m for m in backend.channel_messages if m["message_type"] == "agent"]
	assert len(agent_msgs) == 1
	assert agent_msgs[0]["channel_id"] == "proj-abc1"
	assert agent_msgs[0]["sender"] == "Agent 1"
	assert agent_msgs[0]["content"] == "relay this"

	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task


@pytest.mark.asyncio
async def test_ask_human_writes_channel_message(tmp_path):
	registry = Registry()
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	task = asyncio.create_task(handlers.ask_human("Is this OK?", "my-chan-001"))
	await asyncio.sleep(0)

	question_msgs = [m for m in backend.channel_messages if m["message_type"] == "question"]
	assert len(question_msgs) == 1
	m = question_msgs[0]
	assert m["channel_id"] == "my-chan-001"
	assert m["content"] == "Is this OK?"
	assert m["request_id"] is not None

	registry.resolve_by_correlation(1000, "yes")
	await asyncio.wait_for(task, timeout=1.0)


# Collab spawn integration tests

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


def _make_spawn_config(tmp_path: Path, spawn_root=None) -> Config:
	return Config(
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
	backend.start_inject_listener = AsyncMock()
	registry = Registry()

	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)

	with patch("server.spawn.subprocess.run") as mock_run:
		mock_run.return_value = MagicMock(returncode=0)
		await handler.handle("/spawn myproject --collab review auth")

	pending_path = tmp_path / "spawn-pending.json"
	assert pending_path.exists()
	import json as _json
	data = _json.loads(pending_path.read_text())
	assert "agents" in data
	assert len(data["agents"]) == 2
	assert data["agents"][0]["sender"] == "Agent 1"
	assert data["agents"][1]["sender"] == "Agent 2"
	assert "relay" not in data
	assert "channel_id" in data
	import re
	assert re.match(r"myproject-\d{8}-\d{6}$", data["channel_id"])


@pytest.mark.asyncio
async def test_collab_spawn_writes_sidecar(tmp_path):
	from server.spawn import SpawnHandler
	(tmp_path / "myproject").mkdir()
	cfg = _make_spawn_config(tmp_path, spawn_root=tmp_path)
	backend = MagicMock()
	backend.send_text = AsyncMock()
	backend.send_spawn_ack = AsyncMock()
	backend.write_session_meta = AsyncMock()
	backend.start_inject_listener = AsyncMock()
	registry = Registry()
	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)

	with patch("server.spawn.subprocess.run") as mock_run:
		mock_run.return_value = MagicMock(returncode=0)
		await handler.handle("/spawn myproject --collab review auth")

	sidecar = tmp_path / "collab-sessions.json"
	assert sidecar.exists()
	import json as _json
	entries = _json.loads(sidecar.read_text())
	assert len(entries) == 1
	assert "channel_id" in entries[0]
	assert entries[0]["agent_senders"] == ["Agent 1", "Agent 2"]


@pytest.mark.asyncio
async def test_collab_spawn_registers_session_in_registry(tmp_path):
	from server.spawn import SpawnHandler
	(tmp_path / "myproject").mkdir()
	cfg = _make_spawn_config(tmp_path, spawn_root=tmp_path)
	backend = MagicMock()
	backend.send_text = AsyncMock()
	backend.send_spawn_ack = AsyncMock()
	backend.write_session_meta = AsyncMock()
	backend.start_inject_listener = AsyncMock()
	registry = Registry()
	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)

	with patch("server.spawn.subprocess.run") as mock_run:
		mock_run.return_value = MagicMock(returncode=0)
		await handler.handle("/spawn myproject --collab review auth")

	assert len(registry._sessions) == 1
	session = list(registry._sessions.values())[0]
	assert session.agent_senders == ("Agent 1", "Agent 2")


@pytest.mark.asyncio
async def test_single_agent_spawn_writes_session_meta(tmp_path):
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
		await handler.handle("/spawn myproject do stuff")

	import json as _json
	data = _json.loads((tmp_path / "spawn-pending.json").read_text())
	assert "agents" not in data
	assert "channel_id" in data
	assert len(registry._sessions) == 0
	backend.write_session_meta.assert_called_once()
	args, kwargs = backend.write_session_meta.call_args
	assert args[1] == "single"


@pytest.mark.asyncio
async def test_spawn_rejects_agents_flag(tmp_path):
	from server.spawn import SpawnHandler
	(tmp_path / "myproject").mkdir()
	cfg = _make_spawn_config(tmp_path, spawn_root=tmp_path)
	backend = MagicMock()
	backend.send_text = AsyncMock()
	backend.send_spawn_ack = AsyncMock()
	registry = Registry()
	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
	await handler.handle("/spawn myproject --agents=2 task")
	backend.send_text.assert_called_once()
	assert "--collab" in backend.send_text.call_args[0][0]
