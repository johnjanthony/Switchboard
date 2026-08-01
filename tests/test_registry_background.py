"""Tests for the background pending-ask slot on Registry."""

from datetime import datetime, timezone, timedelta

import pytest

from server.registry import Registry


@pytest.mark.asyncio
async def test_add_background_create_then_append():
	registry = Registry()
	fired = []
	registry.set_pending_mirror(lambda cid, delta: fired.append((cid, delta)))
	rid, appended = registry.add_background("conv-1", "sess-1", "Agent", "req-1", question="Q1")
	assert (rid, appended) == ("req-1", False)
	assert fired == [("conv-1", 1)]
	rid2, appended2 = registry.add_background("conv-1", "sess-1", "Agent", "req-2", question="Q2")
	assert (rid2, appended2) == ("req-1", True)
	assert fired == [("conv-1", 1)]
	record = registry.find_by_request_id("conv-1", "req-1")
	assert record.question == "Q1\n\nAlso: Q2"
	assert record.background is True and record.future is None


@pytest.mark.asyncio
async def test_blocking_add_does_not_supersede_background():
	registry = Registry()
	registry.add_background("conv-1", "sess-1", "Agent", "req-bg", question="Q")
	future = registry.add("conv-1", "sess-1", "Agent", "req-block")
	assert registry.find_by_request_id("conv-1", "req-bg") is not None
	assert registry.find_by_request_id("conv-1", "req-block") is not None
	assert len(registry.all_pending()) == 2
	assert not future.done()


@pytest.mark.asyncio
async def test_background_add_does_not_supersede_blocking():
	"""The reverse direction: an add_background() call for a session that
	already has a live blocking ask must leave that blocking future untouched."""
	registry = Registry()
	future = registry.add("conv-1", "sess-1", "Agent", "req-block")
	rid, appended = registry.add_background("conv-1", "sess-1", "Agent", "req-bg", question="Q")
	assert (rid, appended) == ("req-bg", False)
	assert registry.find_by_request_id("conv-1", "req-block") is not None
	assert registry.find_by_request_id("conv-1", "req-bg") is not None
	assert not future.done()
	assert len(registry.all_pending()) == 2


@pytest.mark.asyncio
async def test_resolve_background_pops_background_map():
	registry = Registry()
	registry.add_background("conv-1", "sess-1", "Agent", "req-bg", question="Q")
	req_id = registry.resolve("conv-1", "req-bg", "the answer")
	assert req_id == "req-bg"
	assert registry.find_by_request_id("conv-1", "req-bg") is None
	assert registry.pending_count == 0


def test_resolve_background_fires_mirror_minus_one():
	registry = Registry()
	fired = []
	registry.add_background("conv-1", "sess-1", "Agent", "req-bg", question="Q")
	registry.set_pending_mirror(lambda cid, delta: fired.append((cid, delta)))
	registry.resolve("conv-1", "req-bg", "the answer")
	assert fired == [("conv-1", -1)]


@pytest.mark.asyncio
async def test_find_by_request_id_distinguishes_background_from_blocking():
	registry = Registry()
	registry.add("conv-1", "sess-1", "Agent", "req-block")
	registry.add_background("conv-1", "sess-1", "Agent", "req-bg", question="Q")
	blocking = registry.find_by_request_id("conv-1", "req-block")
	background = registry.find_by_request_id("conv-1", "req-bg")
	assert blocking is not None and blocking.background is False
	assert background is not None and background.background is True


@pytest.mark.asyncio
async def test_all_pending_includes_both_maps():
	registry = Registry()
	registry.add("conv-1", "sess-1", "Agent", "req-block")
	registry.add_background("conv-1", "sess-2", "Agent", "req-bg", question="Q")
	req_ids = sorted(p.request_id for p in registry.all_pending())
	assert req_ids == ["req-bg", "req-block"]


def test_pending_for_conversation_includes_both_maps():
	registry = Registry()
	registry.add_background("conv-1", "sess-1", "Agent", "req-bg-1", question="Q")
	registry.add_background("conv-2", "sess-1", "Agent", "req-bg-2", question="Q")
	registry.add_parked("conv-1", "sess-2", "Agent", "req-parked")
	pending = registry.pending_for_conversation("conv-1")
	req_ids = sorted(p.request_id for p in pending)
	assert req_ids == ["req-bg-1", "req-parked"]


def test_expired_parked_includes_expired_background_record():
	registry = Registry()
	registry.add_background("conv-1", "sess-1", "Agent", "req-old-bg", question="Q")
	old = registry.find_by_request_id("conv-1", "req-old-bg")
	old.started_at = datetime.now(timezone.utc) - timedelta(hours=73)
	registry.add_background("conv-2", "sess-1", "Agent", "req-fresh-bg", question="Q")
	expired = registry.expired_parked(datetime.now(timezone.utc), 72 * 3600)
	assert [e.request_id for e in expired] == ["req-old-bg"]


def test_parked_count_includes_background_records():
	"""Background records have future=None, so the (already-accepted) union
	semantics count them as parked alongside true future-less blocking records."""
	registry = Registry()
	registry.add_background("conv-1", "sess-1", "Agent", "req-bg", question="Q")
	registry.add_parked("conv-2", "sess-1", "Agent", "req-parked")
	assert registry.parked_count == 2


@pytest.mark.asyncio
async def test_pending_count_sums_both_maps():
	registry = Registry()
	registry.add("conv-1", "sess-1", "Agent", "req-block")
	registry.add_background("conv-1", "sess-2", "Agent", "req-bg", question="Q")
	assert registry.pending_count == 2


def test_oldest_pending_age_seconds_none_when_both_empty():
	registry = Registry()
	assert registry.oldest_pending_age_seconds is None


def test_oldest_pending_age_seconds_considers_background_map():
	registry = Registry()
	registry.add_background("conv-1", "sess-1", "Agent", "req-bg", question="Q")
	record = registry.find_by_request_id("conv-1", "req-bg")
	record.started_at = datetime.now(timezone.utc) - timedelta(hours=1)
	age = registry.oldest_pending_age_seconds
	assert age is not None and age >= 3600


def test_pop_record_pops_background_map():
	registry = Registry()
	fired = []
	registry.add_background("conv-1", "sess-1", "Agent", "req-bg", question="Q")
	registry.set_pending_mirror(lambda cid, delta: fired.append((cid, delta)))
	record = registry.find_by_request_id("conv-1", "req-bg")
	assert registry.pop_record(record) is True
	assert registry.find_by_request_id("conv-1", "req-bg") is None
	assert registry.pending_count == 0
	assert fired == [("conv-1", -1)]


@pytest.mark.asyncio
async def test_live_blocking_pending_returns_the_live_future_record():
	registry = Registry()
	registry.add("conv-1", "sess-1", "Agent", "req-block")
	record = registry.live_blocking_pending("conv-1", "sess-1")
	assert record is not None and record.request_id == "req-block"


def test_live_blocking_pending_none_when_no_record():
	registry = Registry()
	assert registry.live_blocking_pending("conv-1", "sess-1") is None


def test_live_blocking_pending_none_for_parked_record():
	registry = Registry()
	registry.add_parked("conv-1", "sess-1", "Agent", "req-parked")
	assert registry.live_blocking_pending("conv-1", "sess-1") is None


def test_live_blocking_pending_none_for_background_record():
	registry = Registry()
	registry.add_background("conv-1", "sess-1", "Agent", "req-bg", question="Q")
	assert registry.live_blocking_pending("conv-1", "sess-1") is None


@pytest.mark.asyncio
async def test_live_blocking_pending_none_when_future_already_done():
	registry = Registry()
	future = registry.add("conv-1", "sess-1", "Agent", "req-block")
	future.cancel()
	assert registry.live_blocking_pending("conv-1", "sess-1") is None
