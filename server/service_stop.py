"""The service's stop flag. Raised the moment the stop signal arrives, before
uvicorn tears down the in-flight tool calls, so a cancelled call can tell a
service stop from a client's cancel."""

from __future__ import annotations

import time
from typing import Callable


class ServiceStop:
	def __init__(self, mono: Callable[[], float] = time.monotonic) -> None:
		self._mono = mono
		self._raised_at: float | None = None

	@property
	def requested(self) -> bool:
		return self._raised_at is not None

	def raise_(self) -> None:
		"""Idempotent: a second raise keeps the first time."""
		if self._raised_at is None:
			self._raised_at = self._mono()

	def age_ms(self) -> int | None:
		"""Milliseconds since the flag went up, or None while it is down."""
		if self._raised_at is None:
			return None
		return int((self._mono() - self._raised_at) * 1000)
