"""The approval registry (server/approvals.py): lifecycle, cards and answers."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.approvals import ApprovalRegistry, Outcome, resolve_card_conversation
from server.gateway.bg_tasks import drain_bg_tasks
from server.registry import Registry
from server.session_registry import SessionRegistry
from tests.conftest import make_active_conversation


class _CardBackend:
	"""The Firebase calls an approval makes, recorded. write_gate holds every
	message write until set; fail_writes fails that many writes."""

	def __init__(self):
		self.messages = []
		self.records = []
		self.cancelled = []
		self.removed = []
		self.deleted_slots = []
		self.fail_writes = 0
		self.write_gate = None
		self._n = 0

	async def write_conversation_message(self, conversation_id, sender, kind, text, **kwargs):
		if self.write_gate is not None:
			await self.write_gate.wait()
		if self.fail_writes > 0:
			self.fail_writes -= 1
			raise RuntimeError("firebase down")
		self._n += 1
		self.messages.append((conversation_id, sender, kind, text, kwargs))
		return conversation_id, f"msg-{self._n}"

	async def add_pending_question_record(self, conversation_id, request_id, **kwargs):
		self.records.append((conversation_id, request_id, kwargs))

	async def mark_question_cancelled(self, conversation_id, request_id):
		self.cancelled.append((conversation_id, request_id))

	async def remove_pending_question_record(self, conversation_id, request_id):
		self.removed.append((conversation_id, request_id))

	async def delete_response_slot(self, slot):
		self.deleted_slots.append(slot)

	def cards(self):
		return [m for m in self.messages if m[2] == "question"]


def _logger():
	logger = MagicMock()
	logger.info = AsyncMock()
	logger.surface_error = AsyncMock()
	return logger


async def _resolve(_sid, _cwd):
	return "conv-1", "Claude"


def _world(away=False):
	registry = Registry()
	registry.update_global_away_cache(away)
	mirror = []
	registry.set_pending_mirror(lambda conversation_id, delta: mirror.append((conversation_id, delta)))
	sessions = SessionRegistry()
	backend = _CardBackend()
	approvals = ApprovalRegistry(registry, sessions, backend, _logger(), resolve_conversation=_resolve)
	registry.add_away_observer(approvals.on_away_change)
	return registry, sessions, backend, approvals, mirror


async def _open(approvals, sid="s1", tool="Bash", tool_input=None, agent_id=None):
	return await approvals.open(
		cli_session_id=sid, agent_id=agent_id, tool=tool,
		tool_input=tool_input if tool_input is not None else {"command": "ls"}, cwd="C:/Work/X",
	)


@pytest.mark.asyncio
async def test_at_the_desk_an_approval_marks_the_session_and_posts_no_card():
	_, sessions, backend, approvals, mirror = _world(away=False)
	approval = await _open(approvals)
	assert backend.cards() == []
	assert mirror == []
	rec = sessions.get("s1")
	assert rec.awaiting_approval is True
	assert rec.approval_since == approval.opened_at.isoformat()


@pytest.mark.asyncio
async def test_away_an_approval_posts_a_card_with_allow_and_deny():
	_, _, backend, approvals, mirror = _world(away=True)
	approval = await _open(approvals, tool_input={"command": "echo hi > x.txt"})
	((conversation_id, sender, kind, text, kwargs),) = backend.cards()
	assert (conversation_id, sender, kind) == ("conv-1", "Claude", "question")
	assert text.split("\n")[0] == "Approve Bash: echo hi > x.txt"
	assert kwargs["suggestions"] == ["Allow", "Deny"]
	assert kwargs["title"] == "Approve Bash?"
	assert kwargs["format"] == "markdown"
	assert kwargs["request_id"] == approval.card_id
	assert approval.card_id.startswith("apr-")
	((record_conv, record_id, record_kwargs),) = backend.records
	assert (record_conv, record_id) == ("conv-1", approval.card_id)
	assert record_kwargs["approval"] is True
	assert record_kwargs["cli_session_id"] == "s1"
	assert mirror == [("conv-1", 1)]


@pytest.mark.asyncio
async def test_allow_from_the_phone_decides_the_approval():
	_, sessions, backend, approvals, mirror = _world(away=True)
	approval = await _open(approvals)
	card_id, msg_id = approval.card_id, approval.msg_id
	await approvals.answer("conv-1", card_id, "Allow", f"answers/conv-1/{card_id}")
	await drain_bg_tasks(timeout=2)
	assert approval.future.result() == Outcome("decided", decision={"behavior": "allow"}, reason="phone")
	assert backend.removed == [("conv-1", card_id)]
	assert backend.deleted_slots == [f"answers/conv-1/{card_id}"]
	replies = [m for m in backend.messages if m[2] == "human"]
	assert len(replies) == 1
	assert (replies[0][1], replies[0][3]) == ("John", "Allow")
	assert replies[0][4]["attached_to_msg_id"] == msg_id
	assert backend.cancelled == [], "an answered card is not withdrawn"
	assert mirror == [("conv-1", 1), ("conv-1", -1)]
	assert sessions.get("s1").awaiting_approval is False
	assert approvals.open_approvals() == []


@pytest.mark.asyncio
async def test_free_text_from_the_phone_denies_with_johns_words():
	_, _, _, approvals, _ = _world(away=True)
	approval = await _open(approvals)
	await approvals.answer("conv-1", approval.card_id, "use the staging bucket", None)
	assert approval.future.result().decision == {"behavior": "deny", "message": "use the staging bucket"}


@pytest.mark.asyncio
async def test_a_reply_to_an_ended_card_is_a_benign_replay():
	_, _, backend, approvals, _ = _world(away=True)
	approval = await _open(approvals)
	card_id = approval.card_id
	await approvals.close(approval, Outcome("released", reason="terminal"))
	assert approvals.owns("conv-1", card_id)
	await approvals.answer("conv-1", card_id, "Allow", f"answers/conv-1/{card_id}")
	await drain_bg_tasks(timeout=2)
	assert approval.future.result().kind == "released"
	assert [m for m in backend.messages if m[2] == "human"] == []
	assert backend.deleted_slots == [f"answers/conv-1/{card_id}"]


@pytest.mark.asyncio
async def test_owns_is_false_for_an_ask():
	_, _, _, approvals, _ = _world(away=True)
	await _open(approvals)
	assert approvals.owns("conv-1", "a1b2c3d4") is False


@pytest.mark.asyncio
async def test_the_terminal_answer_releases_the_matching_approval_and_withdraws_its_card():
	_, sessions, backend, approvals, _ = _world(away=True)
	first = await _open(approvals, tool_input={"command": "echo a"})
	second = await _open(approvals, tool_input={"command": "echo b"})
	second_card = second.card_id
	assert await approvals.settle_from_terminal(
		"s1", agent_id=None, tool="Bash", tool_input={"command": "echo b"},
		decision="accept", source="user_temporary",
	)
	assert second.future.result() == Outcome("released", reason="terminal:user_temporary:accept")
	assert not first.future.done()
	assert backend.cancelled == [("conv-1", second_card)]
	assert sessions.get("s1").awaiting_approval is True, "the first dialog is still open"
	lines = [c.args[0] for c in approvals._logger.info.await_args_list]
	assert any(line.startswith("approval_settled_at_terminal:") and "exact=True" in line for line in lines)


@pytest.mark.asyncio
async def test_settle_falls_back_to_the_oldest_with_the_same_tool():
	_, _, _, approvals, _ = _world()
	first = await _open(approvals, tool_input={"command": "echo a"})
	second = await _open(approvals, tool_input={"command": "echo b"})
	assert await approvals.settle_from_terminal(
		"s1", agent_id=None, tool="Bash", tool_input={"command": "rewritten"},
		decision="accept", source="user_temporary",
	)
	assert first.future.done()
	assert not second.future.done()
	lines = [c.args[0] for c in approvals._logger.info.await_args_list]
	assert any(line.startswith("approval_settled_at_terminal:") and "exact=False" in line for line in lines)


@pytest.mark.asyncio
async def test_settle_with_no_open_approval_is_not_an_error():
	_, _, _, approvals, _ = _world()
	assert await approvals.settle_from_terminal(
		"s1", agent_id=None, tool="Bash", tool_input={}, decision="accept", source="user_temporary",
	) is False


@pytest.mark.asyncio
async def test_settle_matches_the_agent():
	_, _, _, approvals, _ = _world()
	main = await _open(approvals)
	sub = await _open(approvals, agent_id="agent-1")
	await approvals.settle_from_terminal(
		"s1", agent_id="agent-1", tool="Bash", tool_input={"command": "ls"}, decision="reject", source="user_reject",
	)
	assert sub.future.done()
	assert not main.future.done()


@pytest.mark.asyncio
async def test_a_turn_end_closes_main_loop_approvals_only():
	_, _, _, approvals, _ = _world()
	main = await _open(approvals)
	sub = await _open(approvals, agent_id="agent-1")
	assert await approvals.close_main_loop("s1", "turn_end") == 1
	assert main.future.result() == Outcome("released", reason="turn_end")
	assert not sub.future.done()


@pytest.mark.asyncio
async def test_a_session_end_closes_every_approval_of_the_session():
	_, sessions, _, approvals, _ = _world()
	a = await _open(approvals)
	b = await _open(approvals, agent_id="agent-1")
	other = await _open(approvals, sid="s2")
	assert await approvals.close_session("s1", "session_end") == 2
	assert a.future.done() and b.future.done()
	assert not other.future.done()
	assert sessions.get("s1").awaiting_approval is False
	assert sessions.get("s2").awaiting_approval is True


@pytest.mark.asyncio
async def test_cards_follow_away_mode():
	registry, _, backend, approvals, mirror = _world(away=False)
	approval = await _open(approvals)
	registry.global_away_mode = True
	await drain_bg_tasks(timeout=2)
	first_card = approval.card_id
	assert first_card is not None
	assert len(backend.cards()) == 1
	registry.global_away_mode = False
	await drain_bg_tasks(timeout=2)
	assert approval.card_id is None
	assert backend.cancelled == [("conv-1", first_card)]
	assert not approval.future.done(), "the hook keeps holding at the desk"
	registry.global_away_mode = True
	await drain_bg_tasks(timeout=2)
	assert approval.card_id not in (None, first_card), "a reposted card gets a fresh id"
	assert mirror == [("conv-1", 1), ("conv-1", -1), ("conv-1", 1)]


@pytest.mark.asyncio
async def test_quick_away_flips_leave_one_card_at_most():
	registry, _, backend, approvals, mirror = _world(away=False)
	approval = await _open(approvals)
	registry.global_away_mode = True
	registry.global_away_mode = False
	registry.global_away_mode = True
	await drain_bg_tasks(timeout=2)
	assert approval.card_live is True
	assert sum(delta for _, delta in mirror) == 1
	assert len(backend.cards()) == 1
	assert len(backend.cards()) - len(backend.cancelled) == 1


@pytest.mark.asyncio
async def test_a_failed_card_write_leaves_the_approval_open_for_the_next_sync():
	_, _, backend, approvals, mirror = _world(away=True)
	backend.fail_writes = 1
	approval = await _open(approvals)
	assert approval.card_live is False
	assert mirror == []
	approvals._logger.surface_error.assert_awaited()
	await approvals.sync_cards()
	assert approval.card_live is True
	assert mirror == [("conv-1", 1)]


@pytest.mark.asyncio
async def test_an_approval_closed_while_its_card_is_written_ends_with_no_live_card():
	_, _, backend, approvals, mirror = _world(away=True)
	backend.write_gate = asyncio.Event()
	opening = asyncio.create_task(_open(approvals))
	for _ in range(20):
		if approvals.open_approvals():
			break
		await asyncio.sleep(0)
	(approval,) = approvals.open_approvals()
	closing = asyncio.create_task(approvals.close(approval, Outcome("released", reason="hook_gone")))
	await asyncio.sleep(0)
	backend.write_gate.set()
	await opening
	await closing
	assert approval.card_live is False
	assert sum(delta for _, delta in mirror) == 0
	assert len(backend.cancelled) == 1


@pytest.mark.asyncio
async def test_closing_twice_is_a_no_op():
	_, _, _, approvals, _ = _world()
	approval = await _open(approvals)
	assert await approvals.close(approval, Outcome("released", reason="a")) is True
	assert await approvals.close(approval, Outcome("decided", decision={"behavior": "allow"}, reason="b")) is False
	assert approval.future.result().reason == "a"


@pytest.mark.asyncio
async def test_a_stop_close_leaves_the_card_for_hydration():
	_, _, backend, approvals, _ = _world(away=True)
	approval = await _open(approvals)
	await approvals.close(approval, Outcome("released", reason="service_stop"), withdraw=False)
	assert backend.cancelled == []


@pytest.mark.asyncio
async def test_the_card_goes_to_the_sessions_own_conversation_under_its_member_name():
	registry = Registry()
	registry.conversations["conv-7"] = make_active_conversation("conv-7", member_session_id="s1", sender="Claude Win 2")
	registry.bind_session("s1", "conv-7")
	assert await resolve_card_conversation(registry, None, "s1", "C:/Work/X") == ("conv-7", "Claude Win 2")


@pytest.mark.asyncio
async def test_an_unbound_session_gets_a_conversation_minted():
	registry = Registry()
	conversation_id, sender = await resolve_card_conversation(registry, None, "s9", "C:/Work/X")
	assert registry.session_to_conversation_id["s9"] == conversation_id
	assert sender == "Claude"


@pytest.mark.asyncio
async def test_a_binding_to_an_ended_conversation_is_repaired_first():
	registry = Registry()
	old = make_active_conversation("conv-old", member_session_id="s1")
	old.state = "ended"
	registry.conversations["conv-old"] = old
	registry.bind_session("s1", "conv-old")
	conversation_id, _ = await resolve_card_conversation(registry, None, "s1", "C:/Work/X")
	assert conversation_id != "conv-old"


@pytest.mark.asyncio
async def test_an_answer_that_waits_while_the_terminal_closes_its_approval_writes_no_reply():
	_, _, backend, approvals, mirror = _world(away=True)
	approval = await _open(approvals)
	card_id = approval.card_id
	async with approvals._cards:
		answering = asyncio.create_task(approvals.answer("conv-1", card_id, "Allow", None))
		await asyncio.sleep(0)
		closing = asyncio.create_task(approvals.close(approval, Outcome("released", reason="terminal")))
		await asyncio.sleep(0)
	await answering
	await closing
	await drain_bg_tasks(timeout=2)
	assert [m for m in backend.messages if m[2] == "human"] == []
	assert backend.cancelled == [("conv-1", card_id)]
	assert backend.removed == []
	assert approval.future.result() == Outcome("released", reason="terminal")
	assert sum(delta for _, delta in mirror) == 0


@pytest.mark.asyncio
async def test_an_answer_to_a_card_that_was_withdrawn_and_reposted_leaves_the_new_card_live():
	registry, _, backend, approvals, mirror = _world(away=True)
	other = await _open(approvals, tool_input={"command": "echo b"})
	approval = await _open(approvals, tool_input={"command": "echo a"})
	old_card = approval.card_id
	gate = asyncio.Event()
	cancel = backend.mark_question_cancelled

	async def gated_cancel(conversation_id, request_id):
		if request_id == other_card:
			await gate.wait()
		await cancel(conversation_id, request_id)

	other_card = other.card_id
	backend.mark_question_cancelled = gated_cancel
	registry.global_away_mode = False
	for _ in range(20):
		await asyncio.sleep(0)
	registry.global_away_mode = True
	for _ in range(20):
		await asyncio.sleep(0)
	answering = asyncio.create_task(approvals.answer("conv-1", old_card, "Allow", None))
	await asyncio.sleep(0)
	gate.set()
	await answering
	await drain_bg_tasks(timeout=2)
	new_card = approval.card_id
	assert approval.card_live is True
	assert new_card not in (None, old_card)
	assert (("conv-1", new_card)) not in backend.cancelled
	assert [m for m in backend.messages if m[2] == "human"] == []
	assert not approval.future.done()
	assert other.card_live is True and other.card_id != other_card
	assert sum(delta for _, delta in mirror) == 2
