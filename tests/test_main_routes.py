"""Tests for HTTP routes registered on the Switchboard app."""

from __future__ import annotations

import asyncio
import json

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from server.main import _build_away_mode_route, _wire_away_mode_mirror
from server.registry import Registry

_CWD = "c:/work/sw"


def _make_request(cwd: str = "") -> Request:
	qs = f"cwd={cwd}".encode() if cwd else b""
	scope = {"type": "http", "method": "GET", "headers": [], "query_string": qs}
	return Request(scope)


@pytest.mark.asyncio
async def test_away_mode_route_returns_false_by_default(tmp_path):
	registry = Registry(away_mode_path=tmp_path / "away-mode.json")
	route = _build_away_mode_route(registry)
	resp = await route(_make_request(_CWD))
	assert resp.status_code == 200
	assert json.loads(resp.body) == {"active": False}


@pytest.mark.asyncio
async def test_away_mode_route_returns_false_when_no_cwd_param(tmp_path):
	"""No cwd query param → False (fail-open)."""
	registry = Registry(away_mode_path=tmp_path / "away-mode.json")
	registry.set_global_away(True)
	route = _build_away_mode_route(registry)
	resp = await route(_make_request())  # no cwd
	assert resp.status_code == 200
	assert json.loads(resp.body) == {"active": False}


@pytest.mark.asyncio
async def test_away_mode_route_returns_true_when_global_away_set(tmp_path):
	registry = Registry(away_mode_path=tmp_path / "away-mode.json")
	registry.set_global_away(True)
	route = _build_away_mode_route(registry)
	resp = await route(_make_request(_CWD))
	assert json.loads(resp.body) == {"active": True}


@pytest.mark.asyncio
async def test_away_mode_route_returns_true_when_cwd_override_set(tmp_path):
	registry = Registry(away_mode_path=tmp_path / "away-mode.json")
	registry.set_cwd_override(_CWD, True)
	route = _build_away_mode_route(registry)
	resp = await route(_make_request(_CWD))
	assert json.loads(resp.body) == {"active": True}


@pytest.mark.asyncio
async def test_away_mode_route_returns_false_on_invalid_cwd(tmp_path):
	"""Non-absolute cwd fails canonicalization → returns False."""
	registry = Registry(away_mode_path=tmp_path / "away-mode.json")
	registry.set_global_away(True)
	route = _build_away_mode_route(registry)
	resp = await route(_make_request("relative/path"))
	assert resp.status_code == 200
	assert json.loads(resp.body) == {"active": False}


@pytest.mark.asyncio
async def test_away_mode_route_cwd_override_false_overrides_global(tmp_path):
	"""Per-cwd override False takes precedence over global True."""
	registry = Registry(away_mode_path=tmp_path / "away-mode.json")
	registry.set_global_away(True)
	registry.set_cwd_override(_CWD, False)
	route = _build_away_mode_route(registry)
	resp = await route(_make_request(_CWD))
	assert json.loads(resp.body) == {"active": False}


class _MirrorBackend:
	def __init__(self):
		self.calls: list[tuple] = []

	async def write_away_mode_mirror(self, cwd: str | None, active: bool) -> None:
		self.calls.append((cwd, active))


@pytest.mark.asyncio
async def test_wire_away_mode_mirror_pushes_initial_and_on_toggle(tmp_path):
	registry = Registry(away_mode_path=tmp_path / "away.json")
	registry.set_global_away(True)  # simulate sidecar surviving a prior run

	backend = _MirrorBackend()
	await _wire_away_mode_mirror(registry, backend)

	# Startup push happened with the sidecar value (global, cwd=None)
	assert backend.calls == [(None, True)]

	# Subsequent global toggle fires the callback
	registry.set_global_away(False)
	await asyncio.sleep(0)
	assert backend.calls == [(None, True), (None, False)]


@pytest.mark.asyncio
async def test_wire_away_mode_mirror_pushes_on_cwd_override(tmp_path):
	"""Per-cwd override fires the mirror callback with the cwd value."""
	registry = Registry(away_mode_path=tmp_path / "away.json")
	backend = _MirrorBackend()
	await _wire_away_mode_mirror(registry, backend)

	# Initial push was False (global).
	assert backend.calls == [(None, False)]

	registry.set_cwd_override(_CWD, True)
	await asyncio.sleep(0)
	# Per-cwd override: mirror called with (cwd, True)
	assert backend.calls == [(None, False), (_CWD, True)]
