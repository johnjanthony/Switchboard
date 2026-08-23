"""Session-title sync: Watchtower reports a Claude Code session title (the
transcript's AI-generated summary or a custom /title) as ring `name`. For a
SINGLE-member Active conversation whose title is still the creation default (or
a prior session-title write), that name becomes the conversation title so the
Operator list and phone Page A show something meaningful instead of
"Sender · cwd".

The guard is meta/title_source: "default" and "session" are overwritable,
"explicit" (an agent-supplied title, convene) is never touched again.
"""

from __future__ import annotations

import pytest

from server.conversation_ops import sync_session_titles
from server.registry import ConversationMember, Registry
from tests.conftest import make_active_conversation


class TitleBackend:
	def __init__(self) -> None:
		self.titles_written: list[tuple[str, str, str | None]] = []

	async def write_conversation_title(self, conv_id: str, title: str, title_source: str | None = None) -> None:
		self.titles_written.append((conv_id, title, title_source))


def _registry_with_conv(conv):
	registry = Registry()
	registry.conversations[conv.id] = conv
	for session_id in conv.members_active:
		registry.bind_session(session_id, conv.id)
	return registry


@pytest.mark.asyncio
async def test_default_title_is_overwritten_by_session_name():
	conv = make_active_conversation(conversation_id="conv-1", member_session_id="s-1", sender="Claude")
	conv.title_source = "default"
	registry = _registry_with_conv(conv)
	backend = TitleBackend()

	updated = await sync_session_titles(registry, backend, {"s-1": {"name": "Fix undo in grouped steps"}})

	assert conv.title == "Fix undo in grouped steps"
	assert conv.title_source == "session"
	assert updated == ["conv-1"]
	assert backend.titles_written == [("conv-1", "Fix undo in grouped steps", "session")]


@pytest.mark.asyncio
async def test_explicit_title_is_never_overwritten():
	conv = make_active_conversation(conversation_id="conv-1", member_session_id="s-1")
	conv.title = "Deliberate Scope"
	conv.title_source = "explicit"
	registry = _registry_with_conv(conv)
	backend = TitleBackend()

	updated = await sync_session_titles(registry, backend, {"s-1": {"name": "Some session title"}})

	assert conv.title == "Deliberate Scope"
	assert conv.title_source == "explicit"
	assert updated == []
	assert backend.titles_written == []


@pytest.mark.asyncio
async def test_previously_synced_title_is_refreshed():
	"""A session title is not a one-shot: Claude Code re-titles a session as its
	subject shifts, and a conversation already carrying a synced title follows."""
	conv = make_active_conversation(conversation_id="conv-1", member_session_id="s-1")
	conv.title = "Old session title"
	conv.title_source = "session"
	registry = _registry_with_conv(conv)
	backend = TitleBackend()

	updated = await sync_session_titles(registry, backend, {"s-1": {"name": "New session title"}})

	assert conv.title == "New session title"
	assert updated == ["conv-1"]


@pytest.mark.asyncio
async def test_unchanged_title_writes_nothing():
	"""Rings arrive every few seconds; re-asserting an identical title would be a
	continuous RTDB write storm."""
	conv = make_active_conversation(conversation_id="conv-1", member_session_id="s-1")
	conv.title = "Steady title"
	conv.title_source = "session"
	registry = _registry_with_conv(conv)
	backend = TitleBackend()

	updated = await sync_session_titles(registry, backend, {"s-1": {"name": "Steady title"}})

	assert updated == []
	assert backend.titles_written == []


@pytest.mark.asyncio
async def test_multi_member_conversation_is_skipped():
	conv = make_active_conversation(conversation_id="conv-1", member_session_id="s-1", sender="Claude")
	conv.title_source = "default"
	conv.members_active["s-2"] = ConversationMember(
		cli_session_id="s-2", sender="Peer", cwd="C:/Work/X", surface="windows", joined_at=0.0,
	)
	registry = _registry_with_conv(conv)
	backend = TitleBackend()

	updated = await sync_session_titles(registry, backend, {"s-1": {"name": "Solo-agent title"}})

	assert conv.title == "test"
	assert updated == []
	assert backend.titles_written == []


@pytest.mark.asyncio
async def test_unbound_session_is_skipped():
	conv = make_active_conversation(conversation_id="conv-1", member_session_id="s-1")
	conv.title_source = "default"
	registry = Registry()
	registry.conversations["conv-1"] = conv  # deliberately NOT bound

	updated = await sync_session_titles(registry, TitleBackend(), {"s-1": {"name": "Orphan title"}})

	assert updated == []


@pytest.mark.asyncio
async def test_ended_conversation_is_skipped():
	conv = make_active_conversation(conversation_id="conv-1", member_session_id="s-1")
	conv.title_source = "default"
	conv.state = "ended"
	registry = _registry_with_conv(conv)

	updated = await sync_session_titles(registry, TitleBackend(), {"s-1": {"name": "Too late"}})

	assert updated == []


@pytest.mark.asyncio
async def test_blank_or_missing_name_is_skipped():
	conv = make_active_conversation(conversation_id="conv-1", member_session_id="s-1")
	conv.title_source = "default"
	registry = _registry_with_conv(conv)

	assert await sync_session_titles(registry, TitleBackend(), {"s-1": {"name": "   "}}) == []
	assert await sync_session_titles(registry, TitleBackend(), {"s-1": {"name": None}}) == []
	assert await sync_session_titles(registry, TitleBackend(), {"s-1": {"pct": 0.4}}) == []
	assert conv.title == "test"


@pytest.mark.asyncio
async def test_title_is_truncated_to_the_firebase_write_limit():
	"""write_conversation_title truncates at 80; truncate in memory too so the
	in-memory title and the persisted one cannot disagree."""
	conv = make_active_conversation(conversation_id="conv-1", member_session_id="s-1")
	conv.title_source = "default"
	registry = _registry_with_conv(conv)
	backend = TitleBackend()

	await sync_session_titles(registry, backend, {"s-1": {"name": "T" * 200}})

	assert conv.title == "T" * 80
	assert backend.titles_written == [("conv-1", "T" * 80, "session")]


@pytest.mark.asyncio
async def test_legacy_record_with_placeholder_title_is_treated_as_default():
	"""Conversations hydrated from a pre-title_source Firebase node carry
	title_source=None. A still-placeholder title is safe to adopt."""
	conv = make_active_conversation(
		conversation_id="conv-1", member_session_id="s-1", sender="Claude", cwd="C:/Work/Switchboard",
	)
	conv.title = "Claude · C:/Work/Switchboard"
	conv.title_source = None
	registry = _registry_with_conv(conv)

	updated = await sync_session_titles(registry, TitleBackend(), {"s-1": {"name": "Adopted title"}})

	assert conv.title == "Adopted title"
	assert updated == ["conv-1"]


@pytest.mark.asyncio
async def test_legacy_record_with_chosen_title_is_treated_as_explicit():
	conv = make_active_conversation(conversation_id="conv-1", member_session_id="s-1", sender="Claude")
	conv.title = "A title someone chose"
	conv.title_source = None
	registry = _registry_with_conv(conv)

	updated = await sync_session_titles(registry, TitleBackend(), {"s-1": {"name": "Should not win"}})

	assert conv.title == "A title someone chose"
	assert updated == []
