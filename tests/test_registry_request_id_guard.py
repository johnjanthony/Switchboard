"""T-148 primitive: resolve/remove act on the request_id they were minted for. resolve
looks pendings up BY request_id (no separate guard needed - a stale request_id simply
finds nothing). remove is keyed by (conversation_id, cli_session_id) with an optional
request_id guard so a superseded asker's cleanup only removes its own entry."""
import asyncio
import pytest


def _add(registry, conv, session_id, sender, req):
	# registry.add creates the future on the running loop.
	return registry.add(conv, session_id, sender, req)


@pytest.mark.asyncio
async def test_resolve_with_matching_request_id_resolves():
	from server.registry import Registry
	r = Registry()
	fut = _add(r, "conv-1", "sess-A", "Claude", "req-A")
	out = r.resolve("conv-1", "req-A", "answer")
	assert out == "req-A"
	assert fut.result() == "answer"


@pytest.mark.asyncio
async def test_resolve_with_mismatched_request_id_is_noop():
	from server.registry import Registry
	r = Registry()
	fut = _add(r, "conv-1", "sess-A", "Claude", "req-NEW")  # the live entry
	# A stale answer for an older request_id arrives.
	out = r.resolve("conv-1", "req-OLD", "stale answer")
	assert out is None
	assert r.pending_count == 1, "the live entry must remain pending"
	assert not fut.done(), "the live future must NOT be resolved with stale text"


@pytest.mark.asyncio
async def test_remove_with_mismatched_request_id_is_noop():
	from server.registry import Registry
	r = Registry()
	fut = _add(r, "conv-1", "sess-A", "Claude", "req-NEW")  # the live entry
	out = r.remove("conv-1", "sess-A", request_id="req-OLD")
	assert out is None
	assert r.pending_count == 1, "the live entry must remain pending"
	assert not fut.cancelled(), "the live future must NOT be cancelled"


@pytest.mark.asyncio
async def test_remove_with_matching_request_id_cancels():
	from server.registry import Registry
	r = Registry()
	fut = _add(r, "conv-1", "sess-A", "Claude", "req-A")
	out = r.remove("conv-1", "sess-A", request_id="req-A")
	assert out == "req-A"
	assert r.pending_count == 0
	assert fut.cancelled()
