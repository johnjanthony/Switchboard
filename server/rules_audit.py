"""Startup audit of the DEPLOYED RTDB security rules (REV-004).

The entire phone command channel (spawn / force-end / combine / away-mode)
rests on the RTDB rules being a real single-UID lock. The repo's
database.rules.json is a placeholder, and nothing previously verified what is
actually deployed. main.py runs audit_rtdb_rules at startup: problems surface
loudly (JSONL + best-effort phone notice) but never block startup - a rules
regression must not take the gateway down with it."""

from __future__ import annotations

import re

_ROOT_TRUE = re.compile(r'"\.(read|write)"\s*:\s*true\b')
_EXPIRY_TESTMODE = re.compile(r"now\s*<\s*\d")


def classify_rtdb_rules(rules_text: str) -> list[str]:
	"""Return human-readable problems with the deployed rules text; an empty
	list means the rules look like a real lock. String-based on purpose: the
	rules endpoint may return JSON-with-comments that json.loads rejects."""
	if not rules_text or not rules_text.strip():
		return ["deployed rules are empty"]
	problems: list[str] = []
	if "YOUR_FIREBASE_UID" in rules_text:
		problems.append("deployed rules still contain the YOUR_FIREBASE_UID placeholder")
	if _ROOT_TRUE.search(rules_text):
		problems.append("a .read or .write rule is literally true (world-readable/writable)")
	if _EXPIRY_TESTMODE.search(rules_text):
		problems.append("rules use a console test-mode expiry clause (now < ...)")
	return problems


async def audit_rtdb_rules(backend, logger) -> None:
	"""Fetch + classify the deployed rules; surface problems, swallow everything."""
	try:
		rules_text = await backend.fetch_database_rules()
	except Exception as exc:
		await logger.surface_error(f"rtdb_rules_audit_error: {exc!r}")
		return
	problems = classify_rtdb_rules(rules_text)
	if not problems:
		return
	detail = "; ".join(problems)
	await logger.surface_error(f"rtdb_rules_audit_failed: {detail}")
	if hasattr(backend, "send_text"):
		try:
			await backend.send_text(f"Switchboard startup: deployed RTDB rules look unsafe - {detail}")
		except Exception:
			pass
