"""Tests for HTTP routes registered on the Switchboard app."""

from __future__ import annotations

import json

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from server.registry import Registry


def _make_request() -> Request:
	scope = {"type": "http", "method": "GET", "headers": [], "query_string": b""}
	return Request(scope)


@pytest.mark.asyncio
async def test_away_mode_route_returns_false_by_default(tmp_path):
	from server.main import _build_away_mode_route
	registry = Registry(away_mode_path=tmp_path / "away-mode.json")
	route = _build_away_mode_route(registry)
	resp = await route(_make_request())
	assert resp.status_code == 200
	assert json.loads(resp.body) == {"active": False}


@pytest.mark.asyncio
async def test_away_mode_route_returns_true_when_set(tmp_path):
	from server.main import _build_away_mode_route
	registry = Registry(away_mode_path=tmp_path / "away-mode.json")
	registry.set_away_mode(True)
	route = _build_away_mode_route(registry)
	resp = await route(_make_request())
	assert json.loads(resp.body) == {"active": True}


@pytest.mark.asyncio
async def test_away_mode_route_returns_false_on_registry_error(tmp_path, monkeypatch):
	from server.main import _build_away_mode_route
	registry = Registry(away_mode_path=tmp_path / "away-mode.json")

	def boom(self):
		raise RuntimeError("registry exploded")

	monkeypatch.setattr(Registry, "is_away_mode_active", boom)
	route = _build_away_mode_route(registry)
	resp = await route(_make_request())
	assert resp.status_code == 200
	assert json.loads(resp.body) == {"active": False}
