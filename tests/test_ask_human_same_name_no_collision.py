"""C1: two distinct agents that call themselves the same raw sender must not
collide on the pending key and cancel each other's questions. The pending key
is (conversation_id, cli_session_id), so two sessions coexist regardless of
whether their raw sender strings match."""
import asyncio
import pytest

from server.gateway import build_tool_handlers
from server.registry import Conversation, ConversationMember
from tests.test_gateway_notify_human import RecordingBackend
from tests.conftest import make_registry_with_loopback

from server.config import Config
from server.logging_jsonl import JsonlLogger

_CWD = "c:/work/sw"
_CONV = "conv-collide"


@pytest.fixture
def cfg(tmp_path):
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.fixture
def logger(cfg):
	return JsonlLogger(cfg.log_path)


def _two_member_conversation(registry):
	"""One active conversation with two sessions that share the raw name 'Claude';
	the second member is disambiguated to 'Claude 2' (as _add_member would do)."""
	conv = Conversation(id=_CONV, title="collision")
	a = ConversationMember(cli_session_id="s-A", sender="Claude", cwd=_CWD, surface="windows", joined_at=0.0)
	b = ConversationMember(cli_session_id="s-B", sender="Claude 2", cwd=_CWD, surface="windows", joined_at=0.0)
	conv.members_active["s-A"] = a
	conv.members_active["s-B"] = b
	registry.conversations[_CONV] = conv
	registry.bind_session("s-A", _CONV)
	registry.bind_session("s-B", _CONV)


@pytest.mark.asyncio
async def test_same_raw_name_agents_do_not_cancel_each_other(cfg, logger):
	registry = make_registry_with_loopback()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	_two_member_conversation(registry)

	# Agent A asks and blocks. Both agents pass the SAME raw sender "Claude".
	t_a = asyncio.create_task(handlers.ask_human("QA", "Claude", cli_session_id="s-A", cwd=_CWD))
	for _ in range(3):
		await asyncio.sleep(0)
	assert registry.pending_count == 1

	# Agent B (different session, same raw name) asks. Pre-fix this superseded
	# and cancelled A's future; post-fix it keys under "Claude 2" and coexists.
	t_b = asyncio.create_task(handlers.ask_human("QB", "Claude", cli_session_id="s-B", cwd=_CWD))
	for _ in range(3):
		await asyncio.sleep(0)

	assert registry.pending_count == 2
	pending_sessions = {p.cli_session_id for p in registry.pending_for_conversation(_CONV)}
	assert pending_sessions == {"s-A", "s-B"}
	assert not t_a.done(), "Agent A's question was wrongly cancelled by a same-named peer"

	for t in (t_a, t_b):
		t.cancel()
		try:
			await t
		except asyncio.CancelledError:
			pass
