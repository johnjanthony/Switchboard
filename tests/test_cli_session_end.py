"""Tests for the cli-session/end handler that marks members dormant on SessionEnd."""

import pytest

from server.cli_session_end import handle_session_end
from server.registry import Registry


def _fixed_now():
	return "2026-05-20T00:00:00Z"


@pytest.mark.asyncio
async def test_dormant_mark():
	"""SessionEnd with reason='logout' marks member dormant but not permanently lost."""
	from tests.conftest import make_active_conversation
	registry = Registry()
	conv = make_active_conversation(conversation_id="conv-1", member_session_id="s-1", sender="Claude")
	registry.conversations["conv-1"] = conv
	registry.bind_session("s-1", "conv-1")

	await handle_session_end(
		registry=registry,
		session_id="s-1",
		reason="logout",
		now=_fixed_now,
	)

	member = conv.members_active["Claude"]
	assert member.alive is False
	assert member.session_ended_at == "2026-05-20T00:00:00Z"
	assert member.session_end_reason == "logout"
	assert member.session_lost_permanently is False
	# binding cleared
	assert "s-1" not in registry.session_to_conversation_id


@pytest.mark.asyncio
async def test_permanently_lost_on_clear_or_compact():
	"""SessionEnd with reason='compact' (or 'clear') sets session_lost_permanently."""
	from tests.conftest import make_active_conversation
	registry = Registry()
	conv = make_active_conversation(conversation_id="conv-1", member_session_id="s-1", sender="Claude")
	registry.conversations["conv-1"] = conv
	registry.bind_session("s-1", "conv-1")

	await handle_session_end(registry=registry, session_id="s-1", reason="compact", now=_fixed_now)
	assert conv.members_active["Claude"].session_lost_permanently is True


@pytest.mark.asyncio
async def test_unknown_session_noop():
	"""No binding exists for the session_id; handler returns cleanly."""
	registry = Registry()
	await handle_session_end(registry=registry, session_id="s-unknown", reason="logout", now=_fixed_now)
	# No assertions — just that it didn't raise.
