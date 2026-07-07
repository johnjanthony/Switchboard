"""Tests for conversation-mutation helpers."""

import asyncio

import pytest

from server.conversation_ops import _create_active_conversation_for
from server.registry import Registry
from tests.conftest import make_registry_with_loopback


def test_create_active_conversation_for_windows():
	async def run():
		r = Registry()
		conv_id = await _create_active_conversation_for(
			r, cli_session_id="s-1", cwd="C:/Work/X", sender="Claude",
		)
		conv = r.conversations[conv_id]
		assert conv.state == "active"
		assert len(conv.members_active) == 1
		member = next(iter(conv.members_active.values()))
		assert member.cli_session_id == "s-1"
		assert member.sender == "Claude"
		assert member.surface == "windows"  # C:/Work/X infers windows
		assert r.session_to_conversation_id["s-1"] == conv_id
		assert r.session_home_conversation_id["s-1"] == conv_id
	asyncio.run(run())


def test_create_active_conversation_for_wsl_mnt():
	async def run():
		r = Registry()
		conv_id = await _create_active_conversation_for(
			r, cli_session_id="s-2", cwd="/mnt/c/work", sender="Claude-WSL",
		)
		member = next(iter(r.conversations[conv_id].members_active.values()))
		assert member.surface == "wsl"
	asyncio.run(run())


def test_create_active_conversation_for_wsl_home():
	async def run():
		r = Registry()
		conv_id = await _create_active_conversation_for(
			r, cli_session_id="s-3", cwd="/home/john/work/switchboard", sender="Claude",
		)
		member = next(iter(r.conversations[conv_id].members_active.values()))
		assert member.surface == "wsl"
	asyncio.run(run())


def test_create_active_conversation_for_windows_backslash():
	"""Cwd with Windows backslashes also infers windows."""
	async def run():
		r = Registry()
		conv_id = await _create_active_conversation_for(
			r, cli_session_id="s-4", cwd=r"C:\Work\X", sender="Claude",
		)
		member = next(iter(r.conversations[conv_id].members_active.values()))
		assert member.surface == "windows"
	asyncio.run(run())


def test_does_not_overwrite_existing_home():
	"""If home pointer already exists for this session, leave it alone."""
	async def run():
		r = Registry()
		r.set_session_home("s-1", "conv-existing-home")
		conv_id = await _create_active_conversation_for(
			r, cli_session_id="s-1", cwd="C:/X", sender="Claude",
		)
		# Binding goes to new conv; home pointer stays
		assert r.session_to_conversation_id["s-1"] == conv_id
		assert r.session_home_conversation_id["s-1"] == "conv-existing-home"
	asyncio.run(run())


def test_create_active_conversation_for_same_session_race():
	"""5 concurrent calls with the same cli_session_id must produce exactly one
	conversation and all return the same conv_id (per-session lock + double-check)."""
	async def run():
		r = Registry()
		results = await asyncio.gather(*[
			_create_active_conversation_for(r, cli_session_id="s-race", cwd="C:/Work/X", sender="Claude")
			for _ in range(5)
		])
		# All calls return the same conv_id
		assert len(set(results)) == 1
		# Exactly one conversation was created
		assert len(r.conversations) == 1
		conv_id = results[0]
		assert r.session_to_conversation_id["s-race"] == conv_id
	asyncio.run(run())


def test_add_member_sender_collision_disambiguates():
	"""When two sessions join the same conversation with the same sender name,
	the second gets an auto-numbered space-suffix (e.g. 'Claude 2')."""
	from server.conversation_ops import _add_member

	async def run():
		r = Registry()
		# Create a conversation first (session s-a joins as "Claude")
		conv_id = await _create_active_conversation_for(
			r, cli_session_id="s-a", cwd="C:/Work/X", sender="Claude",
		)
		conv = r.conversations[conv_id]
		assert "s-a" in conv.members_active

		# Add a second member with the same desired sender name
		await _add_member(r, conv_id, "s-b", "Claude", "C:/Work/X")

		# Both members exist, both alive, distinct session ids
		assert "s-a" in conv.members_active
		assert "s-b" in conv.members_active
		assert conv.members_active["s-a"].sender == "Claude"
		assert conv.members_active["s-b"].sender == "Claude 2"
	asyncio.run(run())


@pytest.mark.anyio
async def test_members_active_keyed_by_session_id():
	registry = Registry()
	conv_id = await _create_active_conversation_for(registry, "sess-A", "C:/Work/X", "Claude")
	conv = registry.conversations[conv_id]
	assert "sess-A" in conv.members_active
	assert conv.members_active["sess-A"].sender == "Claude"


@pytest.mark.anyio
async def test_same_sender_two_sessions_disambiguates_display_only():
	from server.conversation_ops import _add_member

	registry = Registry()
	conv_id = await _create_active_conversation_for(registry, "sess-A", "C:/Work/X", "Claude")
	conv = registry.conversations[conv_id]
	await _add_member(registry, conv_id, "sess-B", "Claude", "C:/Work/Y")
	assert set(conv.members_active.keys()) == {"sess-A", "sess-B"}
	assert conv.members_active["sess-A"].sender == "Claude"
	assert conv.members_active["sess-B"].sender == "Claude 2"


async def test_create_active_conversation_default_origin_is_fallback():
	registry = make_registry_with_loopback()
	conv_id = await _create_active_conversation_for(registry, "s-1", "C:/Work/X", "Claude")
	assert registry.conversations[conv_id].origin == "fallback"


async def test_create_active_conversation_origin_join():
	registry = make_registry_with_loopback()
	conv_id = await _create_active_conversation_for(registry, "s-1", "C:/Work/X", "Claude", origin="join")
	assert registry.conversations[conv_id].origin == "join"
