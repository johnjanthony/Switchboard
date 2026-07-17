"""ask_human's suggestions must be a JSON array of strings. Malformed shapes
historically leaked into the question body as literal markup; the boundary
now rejects them with an actionable error BEFORE any state is created (no
conversation resolution, no rate-limit consumption, no registry pending,
no Firebase write)."""

from __future__ import annotations

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.gateway.handlers import illegal_suggestions_reason
from server.logging_jsonl import JsonlLogger
from tests.conftest import make_registry_with_loopback
from tests.test_gateway_notify_human import RecordingBackend

_CWD = "c:/work/sw"
_SENDER = "Claude"


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


@pytest.mark.parametrize("suggestions", [None, [], ["Yes"], ["Yes", "No", "Ship it"]])
def test_legal_suggestions_pass(suggestions):
	assert illegal_suggestions_reason(suggestions) is None


@pytest.mark.parametrize("suggestions,fragment", [
	("Yes,No", "str"),
	(42, "int"),
	({"a": 1}, "dict"),
	(("Yes",), "tuple"),
	(["Yes", 5], "element 1"),
	([None], "element 0"),
	([["nested"]], "element 0"),
	([True], "element 0"),
])
def test_illegal_suggestions_rejected(suggestions, fragment):
	reason = illegal_suggestions_reason(suggestions)
	assert reason is not None
	assert fragment in reason


@pytest.mark.asyncio
async def test_boundary_rejects_malformed_suggestions_before_any_state(cfg, logger):
	backend = RecordingBackend()
	registry = make_registry_with_loopback()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	_SID = "s-suggestions-001"
	result = await handlers.ask_human(
		"q?", _SENDER, suggestions="Yes,No", cli_session_id=_SID, cwd=_CWD,
	)
	assert result.startswith('ERROR: suggestions must be a JSON array of strings')
	assert "str (not a list)" in result
	assert "omit suggestions" in result
	assert registry.pending_count == 0
	# No question write reached the backend (validation precedes everything).
	assert backend.channel_messages == []


@pytest.mark.asyncio
async def test_boundary_accepts_valid_suggestions(cfg, logger):
	backend = RecordingBackend()
	registry = make_registry_with_loopback()
	registry.global_away_mode = False
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	_SID = "s-suggestions-002"
	result = await handlers.ask_human(
		"q?", _SENDER, suggestions=["Yes", "No"], cli_session_id=_SID, cwd=_CWD,
	)
	assert result == "ERROR: John is at his desk. Ask this question via the terminal."
