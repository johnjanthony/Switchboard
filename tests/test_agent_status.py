import pytest
from server.collab import CollabSession
from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from tests.test_gateway_notify_human import RecordingBackend
import asyncio


@pytest.fixture
def cfg(tmp_path):
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.fixture
def logger(cfg):
	return JsonlLogger(cfg.log_path)


def _canonical(cwd: str) -> str:
	from server.canonicalization import canonicalize_cwd
	return canonicalize_cwd(cwd)


def _enable_away(registry: Registry, cwd: str) -> None:
	"""Mark `cwd` as in away-mode in the in-memory cache, bypassing Firebase
	mirror callbacks. Required for any test that expects a status write,
	because handle_agent_status is gated on away-mode."""
	registry.update_cwd_override_cache(_canonical(cwd), True)


@pytest.mark.asyncio
async def test_resolves_sender_from_recent_messaging_call(cfg, logger, tmp_path):
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	cwd = str(tmp_path)
	_enable_away(registry, cwd)
	registry.record_messaging_sender(_canonical(cwd), "Claude-A")

	await handlers.handle_agent_status(cwd, "thinking", None)

	assert backend.agent_status_writes == [(_canonical(cwd), "Claude-A", "thinking", None)]


@pytest.mark.asyncio
async def test_falls_back_to_claude_when_no_messaging_history(cfg, logger, tmp_path):
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	cwd = str(tmp_path)
	_enable_away(registry, cwd)

	await handlers.handle_agent_status(cwd, "thinking", None)

	assert backend.agent_status_writes == [(_canonical(cwd), "Claude", "thinking", None)]


@pytest.mark.asyncio
async def test_uses_collab_baton_holder(cfg, logger, tmp_path):
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	cwd = str(tmp_path)
	canonical = _canonical(cwd)
	_enable_away(registry, cwd)

	# Set up an active collab session with Claude-A blocked, so Claude-B holds the baton
	session = CollabSession(cwd=canonical, agent_senders=["Claude-A", "Claude-B"], task="t")
	session._waiting["Claude-A"] = asyncio.get_event_loop().create_future()
	registry.add_session(session)

	# Even though "Claude-A" was the last messaging-call sender, the baton holder wins
	registry.record_messaging_sender(canonical, "Claude-A")

	await handlers.handle_agent_status(cwd, "tool:Bash", "npm test")

	assert backend.agent_status_writes == [(canonical, "Claude-B", "tool:Bash", "npm test")]


@pytest.mark.asyncio
async def test_clear_state_passes_through(cfg, logger, tmp_path):
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	cwd = str(tmp_path)
	_enable_away(registry, cwd)

	await handlers.handle_agent_status(cwd, "clear", None)

	assert backend.agent_status_writes == [(_canonical(cwd), "Claude", "clear", None)]


@pytest.mark.asyncio
async def test_canonicalizes_cwd(cfg, logger, tmp_path):
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	# Pass a Windows-style path; expect canonical lowercased forward-slash form
	# (matches what the existing canonicalize_cwd produces for the tmp_path).
	cwd_input = str(tmp_path).replace("/", "\\")
	_enable_away(registry, cwd_input)

	await handlers.handle_agent_status(cwd_input, "thinking", None)

	# The handler should have called the backend with the canonical form, not the input form
	assert len(backend.agent_status_writes) == 1
	stored_cwd = backend.agent_status_writes[0][0]
	assert stored_cwd == _canonical(cwd_input)
	assert "\\" not in stored_cwd


@pytest.mark.asyncio
async def test_truncates_oversized_detail(cfg, logger, tmp_path):
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	cwd = str(tmp_path)
	_enable_away(registry, cwd)
	long_detail = "x" * 500

	await handlers.handle_agent_status(cwd, "tool:Bash", long_detail)

	stored_detail = backend.agent_status_writes[0][3]
	assert stored_detail is not None
	assert len(stored_detail) == 200


@pytest.mark.asyncio
async def test_swallows_backend_exception(cfg, logger, tmp_path):
	class BoomBackend(RecordingBackend):
		async def write_agent_status(self, cwd, sender, state, detail):
			raise RuntimeError("firebase down")

	registry = Registry()
	backend = BoomBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	_enable_away(registry, str(tmp_path))

	# Must not raise — handler swallows exceptions to maintain fire-and-forget contract
	await handlers.handle_agent_status(str(tmp_path), "thinking", None)


@pytest.mark.asyncio
async def test_skips_write_when_not_in_away_mode(cfg, logger, tmp_path):
	"""At-desk events are silently dropped: no backend call, no error."""
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	cwd = str(tmp_path)
	# NOT enabling away mode — registry default is at-desk

	await handlers.handle_agent_status(cwd, "tool:Bash", "npm test")

	assert backend.agent_status_writes == []


@pytest.mark.asyncio
async def test_writes_when_global_away_mode_active(cfg, logger, tmp_path):
	"""Global away mode (no per-cwd override) also gates writes through."""
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	cwd = str(tmp_path)
	registry.update_global_away_cache(True)

	await handlers.handle_agent_status(cwd, "thinking", None)

	assert len(backend.agent_status_writes) == 1


@pytest.mark.asyncio
async def test_per_cwd_override_false_beats_global_away(cfg, logger, tmp_path):
	"""Per-cwd override at False overrides global away → no write."""
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	cwd = str(tmp_path)
	registry.update_global_away_cache(True)
	registry.update_cwd_override_cache(_canonical(cwd), False)

	await handlers.handle_agent_status(cwd, "thinking", None)

	assert backend.agent_status_writes == []
