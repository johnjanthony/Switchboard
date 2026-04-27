"""Tests for CollabSession dataclass."""

import asyncio
import pytest
from server.collab import CollabSession


def _make_session(**kwargs) -> CollabSession:
	defaults = dict(
		cwd="proj-abc1",
		agent_senders=["Claude", "Gemini"],
		task="review the code",
	)
	defaults.update(kwargs)
	return CollabSession(**defaults)


@pytest.mark.asyncio
async def test_other_sender_returns_partner():
	s = _make_session()
	assert s.other_sender("Claude") == "Gemini"
	assert s.other_sender("Gemini") == "Claude"


@pytest.mark.asyncio
async def test_deliver_resolves_waiting_future():
	s = _make_session()
	future = s.start_waiting("Gemini")
	assert not future.done()
	s.deliver("Gemini", "hello from agent 1")
	assert future.done()
	assert future.result() == "hello from agent 1"


@pytest.mark.asyncio
async def test_deliver_buffers_when_nobody_waiting():
	s = _make_session()
	s.deliver("Gemini", "buffered message")
	future = s.start_waiting("Gemini")
	assert future.done()
	assert future.result() == "buffered message"


@pytest.mark.asyncio
async def test_start_waiting_returns_pending_message_immediately():
	s = _make_session()
	s.deliver("Claude", "queued reply")
	future = s.start_waiting("Claude")
	assert future.done()
	assert future.result() == "queued reply"


@pytest.mark.asyncio
async def test_cancel_waiting_removes_future():
	s = _make_session()
	s.start_waiting("Claude")
	s.cancel_waiting("Claude")
	s.deliver("Claude", "too late")
	future2 = s.start_waiting("Claude")
	assert future2.done()
	assert future2.result() == "too late"


@pytest.mark.asyncio
async def test_deliver_inject_resolves_waiting_agent():
	s = _make_session()
	future = s.start_waiting("Claude")
	s.deliver_inject("human says hi")
	assert future.done()
	assert future.result() == "human says hi"


@pytest.mark.asyncio
async def test_deliver_inject_buffers_when_nobody_waiting():
	s = _make_session()
	s.deliver_inject("human message")
	future = s.start_waiting("Gemini")
	assert future.done()
	assert future.result() == "human message"


@pytest.mark.asyncio
async def test_start_waiting_prefers_agent_specific_over_inject():
	s = _make_session()
	s.deliver("Gemini", "from agent 1")
	s.deliver_inject("from human")
	future = s.start_waiting("Gemini")
	assert future.done()
	assert future.result() == "from agent 1"
	future2 = s.start_waiting("Claude")
	assert future2.done()
	assert future2.result() == "from human"


# enroll() tests

def test_enroll_adds_new_sender():
	s = CollabSession(cwd="ch", agent_senders=[], task="")
	assert s.enroll("Alice") is None
	assert s.agent_senders == ["Alice"]


def test_enroll_second_distinct_sender():
	s = CollabSession(cwd="ch", agent_senders=[], task="")
	s.enroll("Alice")
	assert s.enroll("Bob") is None
	assert s.agent_senders == ["Alice", "Bob"]


def test_enroll_idempotent_when_full():
	s = CollabSession(cwd="ch", agent_senders=["Alice", "Bob"], task="")
	assert s.enroll("Alice") is None
	assert s.enroll("Bob") is None
	assert s.agent_senders == ["Alice", "Bob"]


def test_enroll_duplicate_name_when_not_full_returns_duplicate():
	s = CollabSession(cwd="ch", agent_senders=[], task="")
	s.enroll("Alice")
	assert s.enroll("Alice") == "duplicate"
	assert s.agent_senders == ["Alice"]


def test_enroll_third_distinct_sender_returns_full():
	s = CollabSession(cwd="ch", agent_senders=["Alice", "Bob"], task="")
	assert s.enroll("Charlie") == "full"
	assert s.agent_senders == ["Alice", "Bob"]


@pytest.mark.asyncio
async def test_other_sender_after_dynamic_enrollment():
	s = CollabSession(cwd="ch", agent_senders=[], task="")
	s.enroll("Alice")
	s.enroll("Bob")
	assert s.other_sender("Alice") == "Bob"
	assert s.other_sender("Bob") == "Alice"


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
	registry.add(cwd="c:/work/sw", sender="Claude", request_id="req1")
	assert registry.get(("c:/work/sw", "Claude")) is not None
	assert registry.get_session("proj-abc1") is session


@pytest.mark.asyncio
async def test_registry_add_session_twice_replaces_previous():
	registry = Registry()
	session1 = _make_session(cwd="proj-abc1")
	registry.add_session(session1)
	session2 = CollabSession(
		cwd="proj-abc1",
		agent_senders=["Claude", "Gemini"],
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


_BYO_CWD = "c:/work/byo"
_PROJ_CWD = "c:/work/proj-abc1"


@pytest.mark.asyncio
async def test_message_and_await_agent_unknown_channel_creates_byo_session(tmp_path):
	registry = Registry()
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))
	# Calling with an unknown cwd creates a BYO session — does not error
	task = asyncio.create_task(
		handlers.message_and_await_agent(_BYO_CWD, "Alice")
	)
	await asyncio.sleep(0.01)
	assert registry.get_session(_BYO_CWD) is not None
	session = registry.get_session(_BYO_CWD)
	assert session.is_byo is True
	assert "Alice" in session.agent_senders
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task


@pytest.mark.asyncio
async def test_message_and_await_agent_third_sender_returns_full_error(tmp_path):
	registry = Registry()
	session = _make_session(cwd=_PROJ_CWD)
	registry.add_session(session)
	cfg = _make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))
	result = await handlers.message_and_await_agent(_PROJ_CWD, "Intruder", message="hi")
	assert result == "ERROR: session is full"


@pytest.mark.asyncio
async def test_message_and_await_agent_duplicate_sender_returns_error(tmp_path):
	registry = Registry()
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))
	# First call enrolls Alice
	task = asyncio.create_task(
		handlers.message_and_await_agent(_BYO_CWD, "Alice")
	)
	await asyncio.sleep(0.01)
	# Second call with same name is a duplicate collision
	result = await handlers.message_and_await_agent(_BYO_CWD, "Alice")
	assert result == "ERROR: sender 'Alice' is already enrolled — use a unique sender name"
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task


@pytest.mark.asyncio
async def test_message_and_await_agent_byo_fires_write_session_meta(tmp_path):
	registry = Registry()
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))
	task = asyncio.create_task(
		handlers.message_and_await_agent("c:/work/byo-meta", "Alice")
	)
	await asyncio.sleep(0.05)
	assert any(m["channel_id"] == "c:/work/byo-meta" for m in backend.session_metas)
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task


@pytest.mark.asyncio
async def test_message_and_await_agent_byo_fires_inject_listener(tmp_path):
	registry = Registry()
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))
	task = asyncio.create_task(
		handlers.message_and_await_agent("c:/work/byo-inject", "Alice")
	)
	await asyncio.sleep(0.05)
	assert "c:/work/byo-inject" in backend.inject_listeners
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task


@pytest.mark.asyncio
async def test_message_and_await_agent_byo_writes_sidecar(tmp_path):
	import json as _json
	registry = Registry()
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))
	task = asyncio.create_task(
		handlers.message_and_await_agent("c:/work/byo-sidecar", "Alice")
	)
	await asyncio.sleep(0.05)
	sidecar = tmp_path / "collab-sessions.json"
	assert sidecar.exists()
	entries = _json.loads(sidecar.read_text())
	assert any(e["channel_id"] == "c:/work/byo-sidecar" for e in entries)
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task


@pytest.mark.asyncio
async def test_byo_listener_first_receives_initiator_message(tmp_path):
	"""Listener calls with no message, initiator calls with message — listener receives it."""
	registry = Registry()
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	# Listener calls first with no message
	task_listener = asyncio.create_task(
		handlers.message_and_await_agent(_BYO_CWD, "Alice")
	)
	await asyncio.sleep(0)

	# Initiator calls with a message
	task_initiator = asyncio.create_task(
		handlers.message_and_await_agent(_BYO_CWD, "Bob", message="hello from Bob")
	)
	await asyncio.sleep(0)

	# Alice (listener) should receive Bob's message
	result_listener = await asyncio.wait_for(task_listener, timeout=1.0)
	assert result_listener == "hello from Bob"

	# Clean up Bob's waiting task
	registry.get_session(_BYO_CWD).deliver("Bob", "reply from Alice")
	result_initiator = await asyncio.wait_for(task_initiator, timeout=1.0)
	assert result_initiator == "reply from Alice"


@pytest.mark.asyncio
async def test_byo_initiator_first_receives_listener_message(tmp_path):
	"""Initiator calls first with message (buffered), listener calls with no message
	and immediately receives the buffered message."""
	_CWD2 = "c:/work/byo2"
	registry = Registry()
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	# Initiator calls first with a message
	task_initiator = asyncio.create_task(
		handlers.message_and_await_agent(_CWD2, "Bob", message="hello from Bob")
	)
	await asyncio.sleep(0)

	# Listener calls with no message — receives the buffered message immediately
	task_listener = asyncio.create_task(
		handlers.message_and_await_agent(_CWD2, "Alice")
	)
	result_listener = await asyncio.wait_for(task_listener, timeout=1.0)
	assert result_listener == "hello from Bob"

	# Clean up Bob's waiting task
	registry.get_session(_CWD2).deliver("Bob", "reply from Alice")
	result_initiator = await asyncio.wait_for(task_initiator, timeout=1.0)
	assert result_initiator == "reply from Alice"


@pytest.mark.asyncio
async def test_byo_both_with_messages_each_receives_partners_opening(tmp_path):
	"""Both agents call with a message. Each receives the other's opening message."""
	_CWD3 = "c:/work/byo3"
	registry = Registry()
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	task_alice = asyncio.create_task(
		handlers.message_and_await_agent(_CWD3, "Alice", message="Alice's opening")
	)
	await asyncio.sleep(0)

	task_bob = asyncio.create_task(
		handlers.message_and_await_agent(_CWD3, "Bob", message="Bob's opening")
	)

	result_alice = await asyncio.wait_for(task_alice, timeout=1.0)
	result_bob = await asyncio.wait_for(task_bob, timeout=1.0)

	assert result_alice == "Bob's opening"
	assert result_bob == "Alice's opening"


@pytest.mark.asyncio
async def test_byo_initiator_first_relay_fires_with_correct_sender(tmp_path):
	"""When buffered message is drained, relay fires with the original sender name."""
	_RELAY_CWD = "c:/work/byo-relay"
	registry = Registry()
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	# Bob sends first (buffered)
	task_bob = asyncio.create_task(
		handlers.message_and_await_agent(_RELAY_CWD, "Bob", message="Bob speaks first")
	)
	await asyncio.sleep(0)

	# Alice joins with no message — drains buffer
	task_alice = asyncio.create_task(
		handlers.message_and_await_agent(_RELAY_CWD, "Alice")
	)
	await asyncio.sleep(0.05)  # let relay tasks execute

	agent_msgs = [m for m in backend.channel_messages if m["message_type"] == "agent"]
	assert any(m["sender"] == "Bob" and m["content"] == "Bob speaks first" for m in agent_msgs)

	# Clean up
	await asyncio.wait_for(task_alice, timeout=1.0)
	registry.get_session(_RELAY_CWD).deliver("Bob", "done")
	await asyncio.wait_for(task_bob, timeout=1.0)


@pytest.mark.asyncio
async def test_message_and_await_agent_two_agents_exchange(tmp_path):
	registry = Registry()
	session = _make_session(cwd=_PROJ_CWD)
	registry.add_session(session)
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	task_b = asyncio.create_task(
		handlers.message_and_await_agent(_PROJ_CWD, "Gemini")
	)
	await asyncio.sleep(0)

	task_a = asyncio.create_task(
		handlers.message_and_await_agent(_PROJ_CWD, "Claude", message="hello from A")
	)
	await asyncio.sleep(0)

	result_b = await asyncio.wait_for(task_b, timeout=1.0)
	assert result_b == "hello from A"

	session.deliver("Claude", "hello back from B")
	result_a = await asyncio.wait_for(task_a, timeout=1.0)
	assert result_a == "hello back from B"


@pytest.mark.asyncio
async def test_message_and_await_agent_timeout_returns_sentinel(tmp_path):
	registry = Registry()
	session = _make_session(cwd=_PROJ_CWD)
	registry.add_session(session)
	cfg = Config(
		host="127.0.0.1", port=9876, timeout_seconds=0.05,
		log_path=str(tmp_path / "log.jsonl"),
	)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))
	result = await handlers.message_and_await_agent(_PROJ_CWD, "Claude")
	assert result == TIMEOUT_SENTINEL


@pytest.mark.asyncio
async def test_message_and_await_agent_relay_calls_write_channel_message(tmp_path):
	registry = Registry()
	session = _make_session(cwd=_PROJ_CWD)
	registry.add_session(session)
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	task = asyncio.create_task(
		handlers.message_and_await_agent(_PROJ_CWD, "Claude", message="relay this")
	)
	await asyncio.sleep(0)  # let message_and_await_agent run
	await asyncio.sleep(0.01)  # let relay task execute

	agent_msgs = [m for m in backend.channel_messages if m["message_type"] == "agent"]
	assert len(agent_msgs) == 1
	assert agent_msgs[0]["channel_id"] == _PROJ_CWD
	assert agent_msgs[0]["sender"] == "Claude"
	assert agent_msgs[0]["content"] == "relay this"

	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task


@pytest.mark.asyncio
async def test_ask_human_writes_channel_message(tmp_path):
	_ASK_CWD = "c:/work/my-chan"
	registry = Registry()
	registry.set_global_away(True)
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	task = asyncio.create_task(handlers.ask_human("Is this OK?", _ASK_CWD))
	await asyncio.sleep(0)

	question_msgs = [m for m in backend.channel_messages if m["message_type"] == "question"]
	assert len(question_msgs) == 1
	m = question_msgs[0]
	assert m["channel_id"] == _ASK_CWD
	assert m["content"] == "Is this OK?"
	assert m["request_id"] is not None

	# Resolve with default sender "Claude"
	registry.resolve(cwd=_ASK_CWD, sender="Claude", text="yes")
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
	assert data["agents"][0]["sender"] == "Claude"
	assert data["agents"][1]["sender"] == "Gemini"
	assert "relay" not in data
	assert "channel_id" in data
	from server.canonicalization import canonicalize_cwd
	assert data["channel_id"] == canonicalize_cwd(str(tmp_path / "myproject"))


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
	assert entries[0]["agent_senders"] == ["Claude", "Gemini"]


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
	assert session.agent_senders == ["Claude", "Gemini"]


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


# Title prepend integration tests

_PREPEND_CWD = "c:/work/prepend-test"


@pytest.mark.asyncio
async def test_title_prepend_first_message_delivered_to_partner(tmp_path):
	"""First message with a title arrives at the partner with the prepend header."""
	registry = Registry()
	session = _make_session(cwd=_PREPEND_CWD)
	registry.add_session(session)
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	task_b = asyncio.create_task(
		handlers.message_and_await_agent(_PREPEND_CWD, "Gemini")
	)
	await asyncio.sleep(0)

	task_a = asyncio.create_task(
		handlers.message_and_await_agent(_PREPEND_CWD, "Claude", title="T1", message="hello")
	)
	await asyncio.sleep(0)

	result_b = await asyncio.wait_for(task_b, timeout=1.0)
	assert 'Claude\'s current session title: "T1"' in result_b
	assert result_b.endswith("\n\nhello")

	session.deliver("Claude", "reply from Gemini")
	await asyncio.wait_for(task_a, timeout=1.0)


@pytest.mark.asyncio
async def test_title_prepend_same_title_no_repeat(tmp_path):
	"""Second message with the same title is delivered without the header."""
	_CWD = "c:/work/prepend-repeat"
	registry = Registry()
	session = _make_session(cwd=_CWD)
	registry.add_session(session)
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	task_b1 = asyncio.create_task(handlers.message_and_await_agent(_CWD, "Gemini"))
	await asyncio.sleep(0)
	task_a1 = asyncio.create_task(
		handlers.message_and_await_agent(_CWD, "Claude", title="T1", message="first")
	)
	await asyncio.sleep(0)
	result_b1 = await asyncio.wait_for(task_b1, timeout=1.0)
	assert "T1" in result_b1  # first message has prepend

	session.deliver("Claude", "ack1")
	await asyncio.wait_for(task_a1, timeout=1.0)

	# Second round — same title
	task_b2 = asyncio.create_task(handlers.message_and_await_agent(_CWD, "Gemini"))
	await asyncio.sleep(0)
	task_a2 = asyncio.create_task(
		handlers.message_and_await_agent(_CWD, "Claude", title="T1", message="second")
	)
	await asyncio.sleep(0)
	result_b2 = await asyncio.wait_for(task_b2, timeout=1.0)
	assert result_b2 == "second"  # no prepend on second message with same title

	session.deliver("Claude", "ack2")
	await asyncio.wait_for(task_a2, timeout=1.0)


@pytest.mark.asyncio
async def test_title_prepend_changed_title_fires_new_prepend(tmp_path):
	"""When title changes, prepend fires with new title and old title is not present."""
	_CWD = "c:/work/prepend-change"
	registry = Registry()
	session = _make_session(cwd=_CWD)
	registry.add_session(session)
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	task_b1 = asyncio.create_task(handlers.message_and_await_agent(_CWD, "Gemini"))
	await asyncio.sleep(0)
	task_a1 = asyncio.create_task(
		handlers.message_and_await_agent(_CWD, "Claude", title="T1", message="first")
	)
	await asyncio.sleep(0)
	await asyncio.wait_for(task_b1, timeout=1.0)
	session.deliver("Claude", "ack1")
	await asyncio.wait_for(task_a1, timeout=1.0)

	# Second round — title changed
	task_b2 = asyncio.create_task(handlers.message_and_await_agent(_CWD, "Gemini"))
	await asyncio.sleep(0)
	task_a2 = asyncio.create_task(
		handlers.message_and_await_agent(_CWD, "Claude", title="T2", message="second")
	)
	await asyncio.sleep(0)
	result_b2 = await asyncio.wait_for(task_b2, timeout=1.0)
	assert "T2" in result_b2
	assert "T1" not in result_b2

	session.deliver("Claude", "ack2")
	await asyncio.wait_for(task_a2, timeout=1.0)


@pytest.mark.asyncio
async def test_title_prepend_firebase_relay_uses_original_message(tmp_path):
	"""backend.write_channel_message (the Firebase relay) sees the original message, not the prepended version."""
	_CWD = "c:/work/prepend-relay"
	registry = Registry()
	session = _make_session(cwd=_CWD)
	registry.add_session(session)
	cfg = _make_config(tmp_path)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	task_b = asyncio.create_task(handlers.message_and_await_agent(_CWD, "Gemini"))
	await asyncio.sleep(0)
	task_a = asyncio.create_task(
		handlers.message_and_await_agent(_CWD, "Claude", title="MyTitle", message="raw text")
	)
	await asyncio.sleep(0.05)

	result_b = await asyncio.wait_for(task_b, timeout=1.0)
	assert "MyTitle" in result_b  # partner agent sees prepend

	agent_relays = [m for m in backend.channel_messages if m["message_type"] == "agent"]
	assert len(agent_relays) == 1
	assert agent_relays[0]["content"] == "raw text"  # Firebase sees original

	session.deliver("Claude", "done")
	await asyncio.wait_for(task_a, timeout=1.0)
