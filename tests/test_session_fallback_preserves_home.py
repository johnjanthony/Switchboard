"""P5-4 (M01/M34): the away-off unbind must NOT delete the durable home
pointer. apply_fallback's unbind arm stops calling remove_session_binding so
an away-on rebind has a durable target across restarts. The only deleter of
home pointers is the deliberate stale-cleanup path (set_session_home(None))."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from server.registry import Registry
from server.session_fallback import apply_fallback
from tests.conftest import make_active_conversation


@pytest.mark.asyncio
async def test_away_off_unbind_does_not_remove_home_pointer():
	registry = Registry()
	registry.global_away_mode = False  # away OFF -> compute_fallback returns ("unbind", None)
	conv = make_active_conversation(conversation_id="conv-h1", member_session_id="s-h1", sender="Claude")
	registry.conversations["conv-h1"] = conv
	registry.bind_session("s-h1", "conv-h1")
	registry.set_session_home("s-h1", "conv-h1")

	backend = MagicMock()
	backend.set_session_home = AsyncMock()

	apply_fallback(registry, "s-h1", backend=backend)

	# Allow scheduled background coroutines to run.
	import asyncio
	await asyncio.sleep(0.05)

	# The in-memory home pointer survives for a later away-on rebind.
	assert registry.session_home_conversation_id.get("s-h1") == "conv-h1", \
		"home pointer must survive the away-off unbind"
	# The real M01/M34 guard: the unbind arm must NOT issue the Firebase
	# home-pointer delete. The only legitimate deleter is set_session_home(None)
	# on the stale-cleanup path (home conv missing/ended), which an active home
	# must not hit. The in-memory check above is not sufficient on its own:
	# unbind_session never touched the home pointer, so it survived even the old
	# buggy code that deleted only the Firebase side.
	backend.set_session_home.assert_not_awaited()
