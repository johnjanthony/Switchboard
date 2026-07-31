"""Tests for the per-CLI spawn model/effort catalog."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from server import spawn_catalog
from server.spawn_catalog import (
	ANTIGRAVITY_FALLBACK_IDS,
	CLAUDE_EFFORTS,
	build_catalog,
	parse_agy_models,
	recorded_choice_for_resume,
	validate_spawn_choice,
)

AGY_MODELS_OUTPUT = """gemini-3.6-flash-high
gemini-3.6-flash-medium
gemini-3.6-flash-low
gemini-3.5-flash-high
gemini-3.5-flash-medium
gemini-3.5-flash-low
gemini-3.1-pro-high
gemini-3.1-pro-low
claude-sonnet-4-6
claude-opus-4-6-thinking
gpt-oss-120b-medium
"""


def test_parse_agy_models_strips_and_keeps_order():
	ids = parse_agy_models(AGY_MODELS_OUTPUT)
	assert ids[0] == "gemini-3.6-flash-high"
	assert ids[-1] == "gpt-oss-120b-medium"
	assert len(ids) == 11


def test_parse_agy_models_ignores_blank_and_padded_lines():
	assert parse_agy_models("\n a \n\n b \n") == ["a", "b"]


def test_build_catalog_shapes():
	cat = build_catalog(parse_agy_models(AGY_MODELS_OUTPUT))
	claude_ids = [m["id"] for m in cat["claude"]["models"]]
	assert claude_ids == ["fable", "opus", "sonnet", "haiku"]
	haiku = next(m for m in cat["claude"]["models"] if m["id"] == "haiku")
	assert haiku["efforts"] == []
	fable = next(m for m in cat["claude"]["models"] if m["id"] == "fable")
	assert fable["efforts"] == CLAUDE_EFFORTS
	agy = cat["antigravity"]["models"]
	assert len(agy) == 11
	assert all(m["efforts"] == [] for m in agy)


def test_build_catalog_defaults_to_fallback_snapshot():
	cat = build_catalog(None)
	assert [m["id"] for m in cat["antigravity"]["models"]] == ANTIGRAVITY_FALLBACK_IDS


@pytest.mark.parametrize("agent,model,effort", [
	("claude", None, None),
	("claude", "haiku", None),
	("claude", "sonnet", "low"),
	("claude", "fable", "max"),
	("claude", None, "xhigh"),
	("antigravity", "gemini-3.6-flash-low", None),
	("antigravity", None, None),
])
def test_validate_accepts(agent, model, effort):
	cat = build_catalog(None)
	assert validate_spawn_choice(cat, agent, model, effort) is None


@pytest.mark.parametrize("agent,model,effort,frag", [
	("claude", "bogus", None, "unknown model"),
	("claude", "haiku", "low", "does not take effort"),
	("claude", "sonnet", "turbo", "does not take effort"),
	("claude", None, "turbo", "unknown effort"),
	("antigravity", None, "low", "no effort flag"),
	("antigravity", "gemini-3.6-flash-low", "high", "no effort flag"),
	("antigravity", "bogus-id", None, "unknown model"),
])
def test_validate_rejects_with_phone_ready_message(agent, model, effort, frag):
	cat = build_catalog(None)
	err = validate_spawn_choice(cat, agent, model, effort)
	assert err is not None and frag in err


class _Rec:
	def __init__(self, model=None, effort=None):
		self.spawn_model = model
		self.spawn_effort = effort


class _FakeSessions:
	def __init__(self, rec=None):
		self._rec = rec

	def get(self, sid):
		return self._rec


def test_recorded_choice_passthrough_when_valid():
	cat = build_catalog(None)
	assert recorded_choice_for_resume(cat, _FakeSessions(_Rec("sonnet", "low")), "sid", "claude") == ("sonnet", "low", None)


def test_recorded_choice_drops_invalid_effort_keeps_model():
	cat = build_catalog(None)
	model, effort, notice = recorded_choice_for_resume(cat, _FakeSessions(_Rec("sonnet", "turbo")), "sid", "claude")
	assert (model, effort) == ("sonnet", None)
	assert notice is not None and "turbo" in notice


def test_recorded_choice_drops_all_when_model_invalid():
	cat = build_catalog(None)
	model, effort, notice = recorded_choice_for_resume(cat, _FakeSessions(_Rec("retired-model", "low")), "sid", "claude")
	assert (model, effort) == (None, None)
	assert notice is not None and "retired-model" in notice


def test_recorded_choice_none_when_no_record_or_no_choice():
	cat = build_catalog(None)
	assert recorded_choice_for_resume(cat, _FakeSessions(None), "sid", "claude") == (None, None, None)
	assert recorded_choice_for_resume(cat, None, "sid", "claude") == (None, None, None)
	assert recorded_choice_for_resume(cat, _FakeSessions(_Rec()), "sid", "claude") == (None, None, None)


async def test_probe_agy_models_kills_hung_process_on_timeout(monkeypatch):
	monkeypatch.setattr(spawn_catalog, "AGY_PROBE_TIMEOUT_SECONDS", 0.01)

	hang = asyncio.Event()

	async def never_resolves(*args, **kwargs):
		await hang.wait()
		return b"", b""

	proc = MagicMock()
	proc.communicate = never_resolves
	proc.kill = MagicMock()
	proc.wait = AsyncMock()

	async def fake_create_subprocess_exec(*args, **kwargs):
		return proc

	monkeypatch.setattr(spawn_catalog.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

	logger = MagicMock()
	logger.surface_error = AsyncMock()

	ids = await spawn_catalog.probe_agy_models(logger)

	proc.kill.assert_called_once()
	proc.wait.assert_awaited_once()
	assert ids == list(ANTIGRAVITY_FALLBACK_IDS)
	logger.surface_error.assert_awaited()


@pytest.mark.asyncio
async def test_probe_agy_models_success(monkeypatch):
	from server import spawn_catalog
	proc = AsyncMock()
	proc.returncode = 0
	proc.communicate = AsyncMock(return_value=(AGY_MODELS_OUTPUT.encode(), b""))
	monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
	ids = await spawn_catalog.probe_agy_models()
	assert len(ids) == 11
	assert ids[0] == "gemini-3.6-flash-high"


@pytest.mark.asyncio
async def test_probe_agy_models_failure_falls_back_loudly(monkeypatch):
	from server import spawn_catalog
	monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError("agy not found")))
	logger = AsyncMock()
	ids = await spawn_catalog.probe_agy_models(logger)
	assert ids == list(spawn_catalog.ANTIGRAVITY_FALLBACK_IDS)
	logger.surface_error.assert_awaited()


def test_registry_has_spawn_catalog_slot():
	from server.registry import Registry
	assert Registry().spawn_catalog is None
