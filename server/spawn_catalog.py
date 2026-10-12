"""Claude Code model/effort catalog for spawn.

Single source of truth for the spawn dialogs' pick lists AND the dispatch-side
validation allowlist. Claude Code has no enumeration command, so the list is
curated. Published to RTDB spawn_options/ so phone and Operator render the same
lists the server validates against.

The tier lists and flag semantics below were verified by live probing of the
CLI (2026-07); do not edit them without re-probing.
"""

from __future__ import annotations

CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]

# Aliases, not full model names: the CLI resolves each to the latest of its
# line. haiku has no effort concept (the CLI silently ignores --effort for it).
CLAUDE_MODELS: list[dict] = [
	{"id": "fable", "efforts": list(CLAUDE_EFFORTS)},
	{"id": "opus", "efforts": list(CLAUDE_EFFORTS)},
	{"id": "sonnet", "efforts": list(CLAUDE_EFFORTS)},
	{"id": "haiku", "efforts": []},
]


def build_catalog() -> dict:
	"""In-memory catalog and, verbatim, the body of the RTDB spawn_options/
	payload: {"claude": {"models": [{"id", "efforts"}]}}."""
	return {
		"claude": {"models": [{"id": m["id"], "efforts": list(m["efforts"])} for m in CLAUDE_MODELS]},
	}


def validate_spawn_choice(catalog: dict, agent: str, model: str | None, effort: str | None) -> str | None:
	"""None when the (model, effort) pair is spawnable; otherwise a phone-ready
	error string. Absent (None) fields are always valid: no flag is passed and
	the CLI default applies. Switchboard launches Claude Code only, so any other
	agent (an older client can still name one) is rejected."""
	if agent != "claude":
		return f"Cannot spawn: Switchboard launches Claude Code only; agent '{agent}' is not supported."
	models = catalog["claude"]["models"]
	ids = [m["id"] for m in models]
	if model is not None and model not in ids:
		return f"Cannot spawn: unknown model '{model}' for claude. Valid: {', '.join(ids)}."
	if effort is not None:
		if model is not None:
			entry = next(m for m in models if m["id"] == model)
			if effort not in entry["efforts"]:
				valid = ", ".join(entry["efforts"]) if entry["efforts"] else "none (this model has no effort tiers)"
				return f"Cannot spawn: model '{model}' does not take effort '{effort}'. Valid: {valid}."
		elif effort not in CLAUDE_EFFORTS:
			return f"Cannot spawn: unknown effort '{effort}'. Valid: {', '.join(CLAUDE_EFFORTS)}."
	return None


def recorded_choice_for_resume(catalog: dict, sessions, cli_session_id: str) -> tuple:
	"""Recorded spawn-time (model, effort) for a resume launch, fail-soft.

	Returns (model, effort, notice). A recorded value that no longer validates
	against the catalog is dropped rather than blocking the resume (a retired
	model must not make a session unresumable); notice is a phone-ready sentence
	describing the drop, or None when nothing was dropped."""
	rec = sessions.get(cli_session_id) if sessions is not None else None
	if rec is None:
		return None, None, None
	model = getattr(rec, "spawn_model", None)
	effort = getattr(rec, "spawn_effort", None)
	if model is None and effort is None:
		return None, None, None
	if validate_spawn_choice(catalog, "claude", model, effort) is None:
		return model, effort, None
	if model is not None and validate_spawn_choice(catalog, "claude", model, None) is None:
		return model, None, (
			f"Resume note: recorded effort '{effort}' is no longer valid for this CLI; "
			f"resuming with model '{model}' and the CLI's default effort."
		)
	return None, None, (
		f"Resume note: recorded model/effort ('{model}'/'{effort}') no longer valid for this CLI; "
		"resuming with CLI defaults."
	)
