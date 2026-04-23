# Rate-Limiting Design

**Date:** 2026-04-23
**Status:** Approved

## Problem

A runaway agent calling `notify_human` in a tight loop can hammer the Firebase Cloud Messaging backend and potentially trigger FCM quota limits, degrading the very channel relied on during away mode. `ask_human` and `message_and_await_agent` are self-throttling (they block on futures), but `notify_human` and `send_document_human` are fire-and-forget and have no natural back-pressure.

## Approach

Token bucket per channel, implemented in a dedicated `server/rate_limiter.py` module. The gateway calls `limiter.consume(channel_id)` before each outbound write in `notify_human` and `send_document_human`. On rejection, an error string is returned immediately — no backend call, no sleeping.

## `server/rate_limiter.py`

New module. Single class: `RateLimiter`.

**State:** `dict[str, tuple[float, float]]` — maps `channel_id` to `(tokens, last_refill_monotonic)`.

**Constructor:** `RateLimiter(rate_per_minute: int)`. When `rate_per_minute == 0`, the limiter is disabled (`consume` always returns `True`).

**Burst capacity:** Equal to the full per-minute allowance. A fresh channel can send up to `rate_per_minute` messages in rapid succession if it has been quiet; it cannot sustain that rate indefinitely.

**Refill:** Continuous, at `rate_per_minute / 60` tokens per second. No fixed window boundaries — no cliff-edges at the minute mark.

**Public method:** `consume(channel_id: str) -> bool`. Refills tokens for the channel based on elapsed time since last refill, then attempts to consume one token. Returns `True` if consumed (call allowed), `False` if bucket empty (call blocked). No locking required — single asyncio event loop, same assumption as `registry.py`.

## Gateway changes (`server/gateway.py`)

`notify_human` and `send_document_human` each gain a `limiter.consume(channel_id)` check immediately before `backend.write_channel_message()`.

On `False` return:

- Return the following error string to the agent (no backend call made):

  ```
  ERROR: rate limit exceeded — you are sending too fast.
  Limit is {rate} messages/min per channel.
  Wait at least {wait:.0f} seconds before retrying, or slow your notify cadence.
  ```

  Where `wait = ceil(60 / rate)`. Computed from the configured rate so it stays accurate if the limit changes.

- Write a `rate_limited` event to the JSONL audit log: `channel_id`, tool name (`notify_human` or `send_document_human`), timestamp.

No change to the happy path for either tool.

## Config (`server/config.py`)

New field: `rate_limit: int` (default `30`, `0` = disabled). Sourced from env var `SWITCHBOARD_RATE_LIMIT`.

## Wiring (`server/main.py`)

`RateLimiter` instantiated once at startup using `config.rate_limit`. Passed into the gateway alongside `registry` and `backend`. No other callers.

## Tests

| File | What it covers |
|------|----------------|
| `tests/test_rate_limiter.py` | `RateLimiter` in isolation: allows calls up to limit; rejects when exceeded; refills correctly over time (mock `time.monotonic`); disabled when `rate_per_minute=0` |
| `tests/test_gateway_notify_human.py` | Add: rate-limited `notify_human` returns correct error string and does not call backend |
| `tests/test_gateway_document.py` | Add: rate-limited `send_document_human` returns correct error string and does not call backend |

## Out of scope

- Global (cross-channel) rate limiting
- `ask_human` and `message_and_await_agent` (both block on futures; natural back-pressure)
- Persistent rate-limit state across restarts (in-memory only; restart resets all buckets)
- Per-sender limiting (channel granularity is sufficient for a single-developer gateway)
