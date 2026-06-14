"""T-148 primitive: resolve/remove must honor an optional request_id guard so a
stale or superseded operation acts only on the entry it was minted for, never on
a newer entry that happens to share the (conversation_id, sender) key. A None
request_id means no guard (legacy behavior)."""
import asyncio
import pytest


def _add(registry, conv, sender, req):
	# registry.add creates the future on the running loop.
	return registry.add(conversation_id=conv, sender=sender, request_id=req)


@pytest.mark.asyncio
async def test_resolve_with_matching_request_id_resolves():
	from server.registry import Registry
	r = Registry()
	fut = _add(r, "conv-1", "Claude", "req-A")
	out = r.resolve("conv-1", "Claude", "answer", request_id="req-A")
	assert out == "req-A"
	assert fut.result() == "answer"


@pytest.mark.asyncio
async def test_resolve_with_mismatched_request_id_is_noop():
	from server.registry import Registry
	r = Registry()
	fut = _add(r, "conv-1", "Claude", "req-NEW")  # the live entry
	# A stale answer for an older request_id arrives under the same key.
	out = r.resolve("conv-1", "Claude", "stale answer", request_id="req-OLD")
	assert out is None
	assert r.pending_count == 1, "the live entry must remain pending"
	assert not fut.done(), "the live future must NOT be resolved with stale text"


@pytest.mark.asyncio
async def test_resolve_with_none_request_id_keeps_legacy_behavior():
	from server.registry import Registry
	r = Registry()
	fut = _add(r, "conv-1", "Claude", "req-A")
	out = r.resolve("conv-1", "Claude", "answer")  # no request_id -> no guard
	assert out == "req-A"
	assert fut.result() == "answer"


@pytest.mark.asyncio
async def test_remove_with_mismatched_request_id_is_noop():
	from server.registry import Registry
	r = Registry()
	fut = _add(r, "conv-1", "Claude", "req-NEW")  # the live entry
	out = r.remove("conv-1", "Claude", request_id="req-OLD")
	assert out is None
	assert r.pending_count == 1, "the live entry must remain pending"
	assert not fut.cancelled(), "the live future must NOT be cancelled"


@pytest.mark.asyncio
async def test_remove_with_matching_request_id_cancels():
	from server.registry import Registry
	r = Registry()
	fut = _add(r, "conv-1", "Claude", "req-A")
	out = r.remove("conv-1", "Claude", request_id="req-A")
	assert out == "req-A"
	assert r.pending_count == 0
	assert fut.cancelled()
