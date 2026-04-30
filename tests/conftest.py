"""Shared pytest fixtures."""

import pytest

from server.registry import Registry


@pytest.fixture
def anyio_backend():
	"""pytest-asyncio / anyio shim — stick to asyncio only."""
	return "asyncio"


def make_registry_with_loopback() -> Registry:
	"""Build a Registry whose away-mode callback loops straight back into
	update_*_cache. Mimics what the Firebase listener will do in production
	(Task 8) — every set_* write fires the callback, which in real life hits
	Firebase and bounces back via the listener; here we collapse that round-trip
	for tests so set_* calls remain observable through the in-memory cache.

	Use this anywhere a test calls Registry.set_global_away / set_cwd_override /
	remove_cwd_override (directly or via a handler) and then asserts the cache
	state via global_away() / cwd_overrides() / is_away_mode_active()."""
	r = Registry()
	def _loopback(cwd, active):
		if cwd is None:
			r.update_global_away_cache(bool(active))
		else:
			r.update_cwd_override_cache(cwd, active)
	r.set_away_mode_callback(_loopback)
	return r
