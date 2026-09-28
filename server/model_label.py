"""Session model chip: display naming plus the observed-versus-spawn fallback.

The server resolves the chip text once, in SessionRecord.to_payload, so the
phone and Operator render the same string and cannot drift apart. Pure, no I/O.
"""

from __future__ import annotations

import re

_CLAUDE_FAMILIES = ("fable", "opus", "sonnet", "haiku")
_DATE_SEGMENT = re.compile(r"\d{8}")
_SEP = " \u00b7 "


def friendly_model_name(model_id: str) -> str:
	"""Display name for a model id. An id this does not recognize comes back
	unchanged: a raw id is honest, a guessed name is not."""
	if any(ch.isspace() for ch in model_id):
		# Already a display name (Antigravity reports these, e.g. "Gemini 3.1 Pro").
		return model_id
	if model_id in _CLAUDE_FAMILIES:
		return model_id.title()
	parts = model_id.split("-")
	if len(parts) >= 3 and parts[0] == "claude" and parts[1] in _CLAUDE_FAMILIES:
		numbers = parts[2:]
		if _DATE_SEGMENT.fullmatch(numbers[-1]):
			numbers = numbers[:-1]
		if numbers and all(p.isdigit() for p in numbers):
			return f"{parts[1].title()} {'.'.join(numbers)}"
		return model_id
	if len(parts) >= 2 and parts[0] == "gemini":
		return " ".join(p.title() if p.isalpha() else p for p in parts)
	return model_id


def resolve(
	model: str | None, effort: str | None, spawn_model: str | None, spawn_effort: str | None,
) -> tuple[str | None, str | None]:
	"""(label, source) for a session's chip; source is "observed", "spawn" or
	None. An observed model brings the observed effort even when that is
	absent, so a switch to a model without effort tiers never shows a stale
	one. The spawn pick shows only until the first observation."""
	model, effort = _present(model), _present(effort)
	spawn_model, spawn_effort = _present(spawn_model), _present(spawn_effort)
	if model:
		return _compose(friendly_model_name(model), effort), "observed"
	if spawn_model or spawn_effort:
		name = friendly_model_name(spawn_model) if spawn_model else "Default"
		return _compose(name, spawn_effort), "spawn"
	return None, None


def _present(value: object) -> str | None:
	return value if isinstance(value, str) and value else None


def _compose(name: str, effort: str | None) -> str:
	return f"{name}{_SEP}{effort}" if effort else name
