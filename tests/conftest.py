"""Shared pytest fixtures."""

import pytest

from server.registry import Registry


@pytest.fixture
def anyio_backend():
	"""pytest-asyncio / anyio shim — stick to asyncio only."""
	return "asyncio"


def _make_loop_supervisor(backend, logger, name):
	"""Test helper: construct a LoopSupervisor whose error_logger forwards
	to the test logger's surface_error. Initial alert threshold is set
	high so unit tests don't trip the alert path unintentionally.

	`name` is required (not defaulted) so tests stay explicit about which
	dispatch loop they're standing in for — the supervisor's name surfaces
	in /healthz output and a stale default would silently misalign there."""
	from server.firebase_supervisor import LoopSupervisor
	return LoopSupervisor(name, backend, logger.surface_error, initial_alert_threshold=10_000)


def make_registry_with_loopback() -> Registry:
	"""Build a Registry for use in tests.

	The per-cwd override loopback (set_away_mode_callback / update_cwd_override_cache)
	was removed in the conversations redesign (Task 4). This now returns a plain
	Registry. Tests that relied on set_cwd_override / cwd_overrides / is_away_mode_active
	are marked @pytest.mark.skip(reason="legacy; retired by conversations redesign")."""
	# TODO: Task 22 will route cwd-level away mode through global_away_mode
	return Registry()


def make_active_conversation(
	conversation_id: str = "conv-1",
	member_session_id: str = "s-1",
	sender: str = "Claude",
	cwd: str = "C:/Work/X",
	surface: str = "windows",
):
	"""Factory: returns a Conversation with one alive member."""
	from server.registry import Conversation, ConversationMember
	conv = Conversation(id=conversation_id, title="test")
	member = ConversationMember(
		cli_session_id=member_session_id,
		sender=sender,
		cwd=cwd,
		surface=surface,
		joined_at=0.0,
	)
	conv.members_active[sender] = member
	return conv
