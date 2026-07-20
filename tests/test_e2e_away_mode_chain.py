"""P5-2 (H17/M12): in-process end-to-end of the away-mode chain: phone command
-> dispatch_away_mode_commands -> registry flip -> /away-mode read path. Closes
the hook-gating-integration gap left by the dispatch-only unit tests."""

from __future__ import annotations

import asyncio

import pytest
from starlette.requests import Request

from server.gateway.dispatch import dispatch_away_mode_commands
from server.logging_jsonl import JsonlLogger
from server.main import _build_away_mode_route
from server.registry import Registry
from server.session_registry import SessionRegistry
from tests.test_dispatch_away_mode_commands import _make_backend, _make_supervisor, _now_iso


async def _away_mode_active(registry: Registry) -> bool:
	"""Drive the real GET /away-mode route and read back its JSON body."""
	route = _build_away_mode_route(registry, SessionRegistry())
	scope = {"type": "http", "method": "GET", "headers": [], "query_string": b""}
	response = await route(Request(scope))
	import json
	return json.loads(bytes(response.body))["active"]


@pytest.mark.asyncio
async def test_enter_then_exit_flips_and_resolves_pendings(tmp_path):
	registry = Registry()
	registry.global_away_mode = False

	# enter_global: the /away-mode read path must report active True.
	backend = _make_backend([{"type": "enter_global", "issued_at": _now_iso()}])
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	supervisor = _make_supervisor()
	with pytest.raises(asyncio.CancelledError):
		await dispatch_away_mode_commands(registry, backend, logger, supervisor)

	assert registry.global_away_mode is True
	assert await _away_mode_active(registry) is True, "/away-mode must report active after enter_global"

	# A pending ask_human is parked while away.
	future = registry.add("conv-e2e", "s-e2e", "Claude", request_id="req-e2e", msg_id="msg-e2e")

	# exit_global with send_default: flips False AND resolves the pending.
	backend2 = _make_backend([
		{"type": "exit_global", "issued_at": _now_iso(), "decision": "send_default", "default_text": "Back at desk"},
	])
	supervisor2 = _make_supervisor()
	with pytest.raises(asyncio.CancelledError):
		await dispatch_away_mode_commands(registry, backend2, logger, supervisor2)

	assert registry.global_away_mode is False
	assert await _away_mode_active(registry) is False, "/away-mode must report inactive after exit_global"
	assert future.done() and future.result() == "Back at desk", "send_default must resolve the pending"
