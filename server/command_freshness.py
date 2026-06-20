"""Freshness gate for phone-issued command queues (P2-1, decided 2026-06-11).

Commands carry an issued_at ISO stamp (every Android and server writer emits
one). Snapshot replay means a command can be hours old when the server comes
back; executing a stale combine/spawn would surprise John at the desk, so
anything older than COMMAND_TTL_SECONDS is dropped with a phone-visible
notice instead. Lives outside firebase.py so the gateway dispatchers can use
it without importing the Firebase SDK surface.
"""

from __future__ import annotations

from datetime import datetime, timezone

COMMAND_TTL_SECONDS = 600  # 10 minutes (decided 2026-06-11)


def command_age_seconds(issued_at) -> float | None:
	"""Age of a command from its issued_at ISO stamp, in seconds.

	Returns None when the stamp is missing or unparseable: fail OPEN (treat
	as fresh) so a malformed stamp cannot silently drop a command John just
	sent. Naive stamps are assumed UTC (all writers emit UTC)."""
	if not isinstance(issued_at, str) or not issued_at:
		return None
	try:
		ts = datetime.fromisoformat(issued_at.replace("Z", "+00:00"))
	except ValueError:
		return None
	if ts.tzinfo is None:
		ts = ts.replace(tzinfo=timezone.utc)
	return (datetime.now(timezone.utc) - ts).total_seconds()
