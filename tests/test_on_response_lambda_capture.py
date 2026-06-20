"""F-75(b): the malformed-entry error lambdas in _on_response's root-snapshot
loop must log their OWN slot/data, not the last iteration's. Drive the loop
with two malformed entries and assert two distinct logged slots."""

from __future__ import annotations

import asyncio
import pytest


class _Event:
	def __init__(self, event_type, path, data):
		self.event_type = event_type
		self.path = path
		self.data = data


@pytest.mark.asyncio
async def test_malformed_entries_log_distinct_slots():
	from server import firebase as fb_module

	# Build a backend without running __init__ (avoids Firebase app init).
	be = fb_module.FirebaseBackend.__new__(fb_module.FirebaseBackend)
	be._loop = asyncio.get_running_loop()

	logged: list[str] = []

	class _SpyLogger:
		async def surface_error(self, detail, correlation=None):
			logged.append(detail)

	be._logger = _SpyLogger()

	# Two malformed root entries (values are not dicts with 'text').
	event = _Event("put", "/", {"slot-A": 123, "slot-B": 456})
	be._on_response(event)
	# Let the call_soon_threadsafe-scheduled _spawn_bg tasks run.
	await asyncio.sleep(0.05)

	a = [d for d in logged if "slot-A" in d]
	b = [d for d in logged if "slot-B" in d]
	assert a and b, f"each malformed entry must log its own slot; got {logged}"
