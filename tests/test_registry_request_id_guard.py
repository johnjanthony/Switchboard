"""T-148 primitive: a superseded asker's cleanup must not remove the live entry
that superseded it. resolve looks pendings up BY request_id (no separate guard
needed - a stale request_id simply finds nothing). The old remove()'s optional
request_id guard was folded into pop_record's identity check when the bulk
Registry methods were deleted in favor of terminate_pending: pop_record takes
the exact PendingRequest object a caller was handed, so a record superseded (and
replaced in the pending map) at the same key is never the object still stored
under that key, and the pop is refused - by construction, not by comparing ids."""
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
async def test_pop_record_of_a_stale_handle_after_supersede_is_noop():
	"""A superseded asker holds its own (now-stale) PendingRequest object. After a
	second ask_human supersedes it (same conversation_id + cli_session_id), the
	stale handle's pop_record must refuse - the live entry it superseded stays
	pending and untouched."""
	from server.registry import Registry
	r = Registry()
	_add(r, "conv-1", "sess-A", "Claude", "req-OLD")
	stale_record = r.find_by_request_id("conv-1", "req-OLD")
	fut_new = _add(r, "conv-1", "sess-A", "Claude", "req-NEW")  # supersedes req-OLD

	assert r.pop_record(stale_record) is False
	assert r.pending_count == 1, "the live entry must remain pending"
	assert not fut_new.cancelled(), "the live future must NOT be cancelled"


@pytest.mark.asyncio
async def test_pop_record_of_the_current_handle_pops_it():
	from server.registry import Registry
	r = Registry()
	fut = _add(r, "conv-1", "sess-A", "Claude", "req-A")
	record = r.find_by_request_id("conv-1", "req-A")
	assert r.pop_record(record) is True
	assert r.pending_count == 0
	assert not fut.cancelled(), "pop_record never settles the future - the caller does"
