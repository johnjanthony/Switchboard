"""model_label: chip naming and the observed-versus-spawn fallback."""

import pytest

from server.model_label import friendly_model_name, resolve


# The first eight ids are every model id found in the assistant lines of 691
# local transcripts on 2026-09-28.
@pytest.mark.parametrize("model_id, expected", [
	("claude-opus-5", "Opus 5"),
	("claude-opus-5-5", "Opus 5.5"),
	("claude-sonnet-5", "Sonnet 5"),
	("claude-sonnet-5-5", "Sonnet 5.5"),
	("claude-fable-5", "Fable 5"),
	("claude-fable-5-1", "Fable 5.1"),
	("claude-opus-4-8", "Opus 4.8"),
	("claude-haiku-4-5-20251001", "Haiku 4.5"),
	("opus", "Opus"),
	("fable", "Fable"),
	("haiku", "Haiku"),
	("gemini-3.8-flash", "Gemini 3.8 Flash"),
	("gemini-3.6-flash-low", "Gemini 3.6 Flash Low"),
	("Gemini 3.1 Pro", "Gemini 3.1 Pro"),
	("Gemini 3.6 Flash (High)", "Gemini 3.6 Flash (High)"),
	("claude-opus-4-8[1m]", "claude-opus-4-8[1m]"),
	("claude-opus", "claude-opus"),
	("claude-haiku-20251001", "claude-haiku-20251001"),
	("<synthetic>", "<synthetic>"),
	("xyz-1", "xyz-1"),
])
def test_friendly_model_name(model_id, expected):
	assert friendly_model_name(model_id) == expected


@pytest.mark.parametrize("model, effort, spawn_model, spawn_effort, expected", [
	("claude-opus-5-5", "xhigh", "sonnet", "low", ("Opus 5.5 · xhigh", "observed")),
	# An observed model never borrows the spawn effort.
	("claude-haiku-4-5-20251001", None, "opus", "high", ("Haiku 4.5", "observed")),
	(None, None, "opus", "high", ("Opus · high", "spawn")),
	(None, None, None, "high", ("Default · high", "spawn")),
	(None, None, "sonnet", None, ("Sonnet", "spawn")),
	(None, None, None, None, (None, None)),
	("", "", "", "", (None, None)),
	# An observed effort without an observed model is not an observation.
	(None, "xhigh", None, None, (None, None)),
	# A malformed (non-string) value counts as absent.
	(42, None, "opus", None, ("Opus", "spawn")),
])
def test_resolve(model, effort, spawn_model, spawn_effort, expected):
	assert resolve(model, effort, spawn_model, spawn_effort) == expected
