"""Per-CLI model/effort catalog for spawn.

Single source of truth for the spawn dialogs' pick lists AND the dispatch-side
validation allowlist. Claude Code has no enumeration command, so its list is
curated; Antigravity's comes from `agy models` at startup (fallback snapshot
below). Published to RTDB spawn_options/ so phone and Operator render the same
lists the server validates against.

The tier lists and flag semantics below were verified by live probing of both
CLIs (2026-07); do not edit them without re-probing.
"""

from __future__ import annotations

import asyncio

CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]

# Aliases, not full model names: the CLI resolves each to the latest of its
# line. haiku has no effort concept (the CLI silently ignores --effort for it).
CLAUDE_MODELS: list[dict] = [
	{"id": "fable", "efforts": list(CLAUDE_EFFORTS)},
	{"id": "opus", "efforts": list(CLAUDE_EFFORTS)},
	{"id": "sonnet", "efforts": list(CLAUDE_EFFORTS)},
	{"id": "haiku", "efforts": []},
]

# Snapshot of `agy models` (2026-07-30), used when the startup probe fails.
# agy bakes effort into the model id, so these are complete spawnable values.
ANTIGRAVITY_FALLBACK_IDS: list[str] = [
	"gemini-3.6-flash-high",
	"gemini-3.6-flash-medium",
	"gemini-3.6-flash-low",
	"gemini-3.5-flash-high",
	"gemini-3.5-flash-medium",
	"gemini-3.5-flash-low",
	"gemini-3.1-pro-high",
	"gemini-3.1-pro-low",
	"claude-sonnet-4-6",
	"claude-opus-4-6-thinking",
	"gpt-oss-120b-medium",
]

AGY_PROBE_TIMEOUT_SECONDS = 10.0


def parse_agy_models(output: str) -> list[str]:
	"""Model ids from `agy models` stdout: stripped non-empty lines, order kept
	(list order is display order on the clients)."""
	return [line.strip() for line in output.splitlines() if line.strip()]


async def probe_agy_models(logger=None) -> list[str]:
	"""Run `agy models` and return its ids; fall back to the snapshot loudly on
	any failure (agy missing, nonzero exit, timeout, empty output)."""
	try:
		proc = await asyncio.create_subprocess_exec(
			"agy", "models",
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE,
		)
		try:
			out, err = await asyncio.wait_for(proc.communicate(), timeout=AGY_PROBE_TIMEOUT_SECONDS)
		except asyncio.TimeoutError:
			# A hung probe must not outlive its timeout as an orphan.
			proc.kill()
			await proc.wait()
			raise
		if proc.returncode == 0:
			ids = parse_agy_models(out.decode("utf-8", errors="replace"))
			if ids:
				return ids
		raise RuntimeError(f"agy models exit {proc.returncode}: {err.decode('utf-8', errors='replace')[:200]}")
	except Exception as exc:
		if logger is not None:
			await logger.surface_error(f"spawn_catalog_agy_probe_failed: {exc}; using fallback snapshot")
		return list(ANTIGRAVITY_FALLBACK_IDS)


def build_catalog(agy_ids: list[str] | None = None) -> dict:
	"""In-memory catalog and, verbatim, the body of the RTDB spawn_options/
	payload: {cli: {"models": [{"id", "efforts"}]}}."""
	ids = list(agy_ids) if agy_ids else list(ANTIGRAVITY_FALLBACK_IDS)
	return {
		"claude": {"models": [{"id": m["id"], "efforts": list(m["efforts"])} for m in CLAUDE_MODELS]},
		"antigravity": {"models": [{"id": i, "efforts": []} for i in ids]},
	}


def validate_spawn_choice(catalog: dict, agent: str, model: str | None, effort: str | None) -> str | None:
	"""None when the (model, effort) pair is spawnable for the CLI; otherwise a
	phone-ready error string. Absent (None) fields are always valid: no flag is
	passed and the CLI default applies. Any agent other than "antigravity"
	validates as claude, matching the launcher's dispatch rule."""
	cli_key = "antigravity" if agent == "antigravity" else "claude"
	models = catalog[cli_key]["models"]
	ids = [m["id"] for m in models]
	if cli_key == "antigravity" and effort is not None:
		return (
			f"Cannot spawn: Antigravity takes no effort flag (effort is part of the model id); got effort='{effort}'. "
			f"Valid models: {', '.join(ids)}."
		)
	if model is not None and model not in ids:
		return f"Cannot spawn: unknown model '{model}' for {cli_key}. Valid: {', '.join(ids)}."
	if effort is not None:
		if model is not None:
			entry = next(m for m in models if m["id"] == model)
			if effort not in entry["efforts"]:
				valid = ", ".join(entry["efforts"]) if entry["efforts"] else "none (this model has no effort tiers)"
				return f"Cannot spawn: model '{model}' does not take effort '{effort}'. Valid: {valid}."
		elif effort not in CLAUDE_EFFORTS:
			return f"Cannot spawn: unknown effort '{effort}'. Valid: {', '.join(CLAUDE_EFFORTS)}."
	return None


def recorded_choice_for_resume(catalog: dict, sessions, cli_session_id: str, agent: str) -> tuple:
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
	if validate_spawn_choice(catalog, agent, model, effort) is None:
		return model, effort, None
	if model is not None and validate_spawn_choice(catalog, agent, model, None) is None:
		return model, None, (
			f"Resume note: recorded effort '{effort}' is no longer valid for this CLI; "
			f"resuming with model '{model}' and the CLI's default effort."
		)
	return None, None, (
		f"Resume note: recorded model/effort ('{model}'/'{effort}') no longer valid for this CLI; "
		"resuming with CLI defaults."
	)
