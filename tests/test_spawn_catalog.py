"""Tests for the Claude Code spawn model/effort catalog."""

from __future__ import annotations

import pytest

from server.spawn_catalog import (
	CLAUDE_EFFORTS,
	build_catalog,
	recorded_choice_for_resume,
	validate_spawn_choice,
)


def test_build_catalog_shapes():
	cat = build_catalog()
	assert set(cat) == {"claude"}
	claude_ids = [m["id"] for m in cat["claude"]["models"]]
	assert claude_ids == ["fable", "opus", "sonnet", "haiku"]
	haiku = next(m for m in cat["claude"]["models"] if m["id"] == "haiku")
	assert haiku["efforts"] == []
	fable = next(m for m in cat["claude"]["models"] if m["id"] == "fable")
	assert fable["efforts"] == CLAUDE_EFFORTS


@pytest.mark.parametrize("agent,model,effort", [
	("claude", None, None),
	("claude", "haiku", None),
	("claude", "sonnet", "low"),
	("claude", "fable", "max"),
	("claude", None, "xhigh"),
])
def test_validate_accepts(agent, model, effort):
	assert validate_spawn_choice(build_catalog(), agent, model, effort) is None


@pytest.mark.parametrize("agent,model,effort,frag", [
	("claude", "bogus", None, "unknown model"),
	("claude", "haiku", "low", "does not take effort"),
	("claude", "sonnet", "turbo", "does not take effort"),
	("claude", None, "turbo", "unknown effort"),
	("retired-cli", None, None, "Claude Code only"),
	("retired-cli", "sonnet", "low", "Claude Code only"),
])
def test_validate_rejects_with_phone_ready_message(agent, model, effort, frag):
	err = validate_spawn_choice(build_catalog(), agent, model, effort)
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
	cat = build_catalog()
	assert recorded_choice_for_resume(cat, _FakeSessions(_Rec("sonnet", "low")), "sid") == ("sonnet", "low", None)


def test_recorded_choice_drops_invalid_effort_keeps_model():
	cat = build_catalog()
	model, effort, notice = recorded_choice_for_resume(cat, _FakeSessions(_Rec("sonnet", "turbo")), "sid")
	assert (model, effort) == ("sonnet", None)
	assert notice is not None and "turbo" in notice


def test_recorded_choice_drops_all_when_model_invalid():
	cat = build_catalog()
	model, effort, notice = recorded_choice_for_resume(cat, _FakeSessions(_Rec("retired-model", "low")), "sid")
	assert (model, effort) == (None, None)
	assert notice is not None and "retired-model" in notice


def test_recorded_choice_none_when_no_record_or_no_choice():
	cat = build_catalog()
	assert recorded_choice_for_resume(cat, _FakeSessions(None), "sid") == (None, None, None)
	assert recorded_choice_for_resume(cat, None, "sid") == (None, None, None)
	assert recorded_choice_for_resume(cat, _FakeSessions(_Rec()), "sid") == (None, None, None)


def test_registry_has_spawn_catalog_slot():
	from server.registry import Registry
	assert Registry().spawn_catalog is None
