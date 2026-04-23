"""Tests for token-bucket RateLimiter."""

from __future__ import annotations

from unittest.mock import patch

from server.rate_limiter import RateLimiter


def test_allows_calls_up_to_burst_limit():
	limiter = RateLimiter(rate_per_minute=3)
	assert limiter.consume("chan-1") is True
	assert limiter.consume("chan-1") is True
	assert limiter.consume("chan-1") is True


def test_rejects_when_limit_exceeded():
	limiter = RateLimiter(rate_per_minute=2)
	limiter.consume("chan-1")
	limiter.consume("chan-1")
	assert limiter.consume("chan-1") is False


def test_different_channels_are_independent():
	limiter = RateLimiter(rate_per_minute=1)
	assert limiter.consume("chan-1") is True
	assert limiter.consume("chan-2") is True  # fresh bucket, unaffected


def test_tokens_refill_over_time():
	limiter = RateLimiter(rate_per_minute=60)  # 1 token/second

	t = 0.0
	with patch("server.rate_limiter.time.monotonic", side_effect=lambda: t):
		# Exhaust all 60 tokens
		for _ in range(60):
			limiter.consume("chan-1")
		assert limiter.consume("chan-1") is False  # bucket empty

		# Advance 2 seconds — 2 tokens refill
		t = 2.0
		assert limiter.consume("chan-1") is True
		assert limiter.consume("chan-1") is True
		assert limiter.consume("chan-1") is False  # back to empty


def test_disabled_when_rate_zero():
	limiter = RateLimiter(rate_per_minute=0)
	for _ in range(1000):
		assert limiter.consume("chan-1") is True


def test_wait_seconds_for_default_rate():
	limiter = RateLimiter(rate_per_minute=30)
	assert limiter.wait_seconds == 2  # ceil(60/30) = 2


def test_wait_seconds_zero_when_disabled():
	limiter = RateLimiter(rate_per_minute=0)
	assert limiter.wait_seconds == 0


def test_rate_per_minute_property():
	limiter = RateLimiter(rate_per_minute=15)
	assert limiter.rate_per_minute == 15


def test_depleted_channel_does_not_affect_other_channel():
	limiter = RateLimiter(rate_per_minute=1)
	limiter.consume("chan-1")           # deplete chan-1
	assert limiter.consume("chan-1") is False  # chan-1 exhausted
	assert limiter.consume("chan-2") is True   # chan-2 unaffected
