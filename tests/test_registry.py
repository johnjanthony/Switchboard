"""Tests for the pending-request registry."""

import asyncio

import pytest

from server.registry import PendingRequest, Registry


@pytest.mark.asyncio
async def test_add_returns_future_and_stores_record():
	registry = Registry()
	future = registry.add("abc123", "chan-1", correlation=42)
	assert isinstance(future, asyncio.Future)
	record = registry.get("abc123")
	assert isinstance(record, PendingRequest)
	assert record.request_id == "abc123"
	assert record.channel_id == "chan-1"
	assert record.correlation == 42
	assert record.future is future


@pytest.mark.asyncio
async def test_resolve_by_correlation_sets_future_result_and_returns_request_id():
	registry = Registry()
	future = registry.add("abc123", "chan-1", correlation=42)
	record = registry.resolve_by_correlation(42, "yes")
	assert record.request_id == "abc123"
	assert future.done()
	assert future.result() == "yes"


@pytest.mark.asyncio
async def test_resolve_by_correlation_returns_none_for_unknown_correlation():
	registry = Registry()
	registry.add("abc123", "chan-1", correlation=42)
	assert registry.resolve_by_correlation(999, "late") is None


@pytest.mark.asyncio
async def test_resolve_removes_entry_from_pending():
	registry = Registry()
	registry.add("abc123", "chan-1", correlation=42)
	registry.resolve_by_correlation(42, "yes")
	assert registry.get("abc123") is None


@pytest.mark.asyncio
async def test_remove_drops_both_indexes():
	registry = Registry()
	registry.add("abc123", "chan-1", correlation=42)
	registry.remove("abc123")
	assert registry.get("abc123") is None
	assert registry.resolve_by_correlation(42, "late") is None


@pytest.mark.asyncio
async def test_multiple_pending_are_independent():
	registry = Registry()
	f1 = registry.add("a", "chan-a", correlation=1)
	f2 = registry.add("b", "chan-b", correlation=2)
	registry.resolve_by_correlation(2, "answer-b")
	assert f2.done() and f2.result() == "answer-b"
	assert not f1.done()
	registry.resolve_by_correlation(1, "answer-a")
	assert f1.done() and f1.result() == "answer-a"
