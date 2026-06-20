"""Tests for conversation-mutation helpers."""

import asyncio

from server.conversation_ops import _create_active_conversation_for
from server.registry import Registry


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
		assert "Claude" in conv.members_active

		# Add a second member with the same desired sender name
		await _add_member(r, conv_id, "s-b", "Claude", "C:/Work/X")

		# Both members exist, both alive, distinct session ids
		assert "Claude" in conv.members_active
		assert "Claude 2" in conv.members_active
		assert conv.members_active["Claude"].cli_session_id == "s-a"
		assert conv.members_active["Claude 2"].cli_session_id == "s-b"
	asyncio.run(run())


def test_add_member_resolves_open_peer_future():
	"""Regression lock: when a peer is added to a conv whose open_peer_future
	is set, the future resolves with a 'Peer X joined' payload. This is the
	bootstrap-and-lobby wake protocol — re-asserted as a unit test here so
	any future refactor of _add_member catches the break."""
	from server.conversation_ops import _add_member

	async def run():
		r = Registry()
		conv_id = await _create_active_conversation_for(
			r, cli_session_id="s-opener", cwd="C:/Work/X", sender="Opener",
		)
		conv = r.conversations[conv_id]
		# Simulate the opener being blocked on the future
		future = asyncio.get_event_loop().create_future()
		conv.open_peer_future = future

		await _add_member(r, conv_id, "s-joiner", "Joiner", "/home/joiner")

		assert future.done(), "_add_member must resolve open_peer_future"
		result = future.result()
		assert "Joiner" in result
		assert "open_conversation" in result
		assert conv.open_peer_future is None, "future slot should be cleared after resolution"
	asyncio.run(run())


def test_migrate_member_resolves_target_open_peer_future():
	"""When a member migrates INTO a target conv whose open_peer_future is set
	(blocked opener — either mint-path bootstrap or sole-alive lobby-hold), the
	migrate must wake the opener. Without this the opener would block until
	timeout while a peer effectively joined via combine or enter_conversation
	Branch 3."""
	from server.conversation_ops import _add_member, _migrate_member

	async def run():
		r = Registry()
		# Source conv: the migrating peer's current home
		source_id = await _create_active_conversation_for(
			r, cli_session_id="s-mover", cwd="C:/Work/Src", sender="Mover",
		)
		# Target conv: separate conv with a blocked opener
		target_id = await _create_active_conversation_for(
			r, cli_session_id="s-opener", cwd="C:/Work/Tgt", sender="Opener",
		)
		target = r.conversations[target_id]
		future = asyncio.get_event_loop().create_future()
		target.open_peer_future = future

		await _migrate_member(
			r, source_id=source_id, target_id=target_id,
			cli_session_id="s-mover", sender="Mover", cwd="/home/mover",
		)

		assert future.done(), "_migrate_member must resolve target's open_peer_future"
		result = future.result()
		assert "Mover" in result
		assert "open_conversation" in result
		assert target.open_peer_future is None
	asyncio.run(run())
