"""Token-bucket rate limiter for outbound message throttling.

Keyed on whatever string the caller passes — today that's `conversation_id`;
historically it was `channel_id`. The limiter is key-agnostic.
"""

from __future__ import annotations

import math
import time

# A bucket whose tokens are at the cap and hasn't been touched in this many
# seconds is indistinguishable from a fresh bucket — drop it on the next sweep
# so the dict doesn't grow unboundedly across the lifetime of the process.
_BUCKET_IDLE_TTL_SECONDS = 600.0
_BUCKET_SWEEP_INTERVAL_SECONDS = 300.0


class RateLimiter:
	"""Per-key token-bucket rate limiter.

	Each key starts with a full bucket (burst = rate_per_minute tokens).
	Tokens refill continuously at rate/60 per second. When the bucket is empty,
	consume() returns False; callers should return an error to the agent.

	When rate_per_minute == 0, the limiter is disabled (consume always returns True).
	No locking needed — single asyncio event loop.
	"""

	def __init__(self, rate_per_minute: int) -> None:
		self._rate = rate_per_minute
		# key -> (tokens, last_refill_monotonic)
		self._buckets: dict[str, tuple[float, float]] = {}
		self._last_sweep: float = 0.0

	@property
	def rate_per_minute(self) -> int:
		return self._rate

	@property
	def wait_seconds(self) -> int:
		"""Minimum seconds an agent must wait before a token refills."""
		return math.ceil(60 / self._rate) if self._rate > 0 else 0

	def _sweep_idle(self, now: float) -> None:
		"""Drop entries whose tokens are at the cap and last-refilled long ago.
		Such entries behave identically to a fresh bucket on the next consume,
		so retaining them in the dict is pure memory churn."""
		if now - self._last_sweep < _BUCKET_SWEEP_INTERVAL_SECONDS:
			return
		self._last_sweep = now
		cap = float(self._rate)
		stale = [
			k for k, (tokens, last_refill) in self._buckets.items()
			if tokens >= cap and (now - last_refill) >= _BUCKET_IDLE_TTL_SECONDS
		]
		for k in stale:
			self._buckets.pop(k, None)

	def consume(self, key: str) -> bool:
		"""Attempt to consume one token for key.

		Returns True if the call is allowed, False if rate-limited.
		On rejection, the refill clock is updated to the current time so that
		token accumulation is computed from the most recent call rather than
		the original depletion point.
		"""
		if self._rate == 0:
			return True

		now = time.monotonic()
		self._sweep_idle(now)
		tokens, last_refill = self._buckets.get(key, (float(self._rate), now))

		elapsed = now - last_refill
		tokens = min(float(self._rate), tokens + elapsed * (self._rate / 60.0))

		if tokens >= 1.0:
			self._buckets[key] = (tokens - 1.0, now)
			return True

		self._buckets[key] = (tokens, now)
		return False
