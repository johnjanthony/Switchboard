"""Validation of the telemetry reports the Claude Code mod posts to
POST /sessions/{sid}/telemetry. Each field is checked on its own: one that
fails is dropped and the rest of the report still applies, because a report
is a partial update and the next measurement brings fresh figures anyway."""

from __future__ import annotations

from datetime import datetime

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
TITLE_SOURCES = ("custom-title", "ai-title")
QUOTA_KINDS = ("five_hour", "seven_day")


def _is_int(value) -> bool:
	return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value) -> bool:
	return isinstance(value, (int, float)) and not isinstance(value, bool)


def _iso_or_none(value):
	if not isinstance(value, str):
		return None
	try:
		datetime.fromisoformat(value)
	except ValueError:
		return None
	return value


def _context(raw):
	if not isinstance(raw, dict) or not _is_int(raw.get("window")) or raw["window"] <= 0:
		return None
	tokens = raw.get("tokens")
	return {"tokens": tokens if _is_int(tokens) and tokens >= 0 else None, "window": raw["window"]}


def _rate_limits(raw) -> list[dict]:
	if not isinstance(raw, list):
		return []
	out = []
	for item in raw:
		if not isinstance(item, dict) or item.get("kind") not in QUOTA_KINDS:
			continue
		used = item.get("percent_used")
		if not _is_number(used) or not 0 <= used <= 100:
			continue
		out.append({"kind": item["kind"], "percent_used": used, "resets_at": _iso_or_none(item.get("resets_at"))})
	return out


def _title(raw):
	if not isinstance(raw, dict) or raw.get("source") not in TITLE_SOURCES:
		return None
	name = raw.get("name")
	if not isinstance(name, str) or not name.strip():
		return None
	return {"name": name.strip(), "source": raw["source"]}


def parse_telemetry(body) -> dict:
	"""The valid part of a telemetry body, normalized; {} for a non-object."""
	if not isinstance(body, dict):
		return {}
	report: dict = {}
	context = _context(body.get("context"))
	if context is not None:
		report["context"] = context
	rate_limits = _rate_limits(body.get("rate_limits"))
	if rate_limits:
		report["rate_limits"] = rate_limits
	model = body.get("model")
	if isinstance(model, str) and model.strip():
		report["model"] = model.strip()
	if "effort" in body and (body["effort"] is None or body["effort"] in EFFORT_LEVELS):
		report["effort"] = body["effort"]
	title = _title(body.get("title"))
	if title is not None:
		report["title"] = title
	return report
