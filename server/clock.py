"""Single shared wall-clock helper. Modules needing an ISO-8601 UTC
timestamp import now_iso from here instead of defining their own."""

from __future__ import annotations

from datetime import datetime, timezone


def now_iso() -> str:
	return datetime.now(timezone.utc).isoformat()
