"""Token-bucket rate limiter for per-channel outbound message throttling."""

from __future__ import annotations

import math
import time


class RateLimiter:
	"""Per-channel token-bucket rate limiter.

	Each channel starts with a full bucket (burst = rate_per_minute tokens).
	Tokens refill continuously at rate/60 per second. When the bucket is empty,
	consume() returns False; callers should return an error to the agent.

	When rate_per_minute == 0, the limiter is disabled (consume always returns True).
	No locking needed — single asyncio event loop.
	"""

	def __init__(self, rate_per_minute: int) -> None:
		self._rate = rate_per_minute
		# channel_id -> (tokens, last_refill_monotonic)
		self._buckets: dict[str, tuple[float, float]] = {}

	@property
	def rate_per_minute(self) -> int:
		return self._rate

	@property
	def wait_seconds(self) -> int:
		"""Minimum seconds an agent must wait before a token refills."""
		return math.ceil(60 / self._rate) if self._rate > 0 else 0

	def consume(self, channel_id: str) -> bool:
		"""Attempt to consume one token for channel_id.

		Returns True if the call is allowed, False if rate-limited.
		On rejection, the refill clock is updated to the current time so that
		token accumulation is computed from the most recent call rather than
		the original depletion point.
		"""
		if self._rate == 0:
			return True

		now = time.monotonic()
		tokens, last_refill = self._buckets.get(channel_id, (float(self._rate), now))

		elapsed = now - last_refill
		tokens = min(float(self._rate), tokens + elapsed * (self._rate / 60.0))

		if tokens >= 1.0:
			self._buckets[channel_id] = (tokens - 1.0, now)
			return True

		self._buckets[channel_id] = (tokens, now)
		return False
