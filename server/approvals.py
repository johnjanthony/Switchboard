"""Open permission dialogs, held while a session's PermissionRequest hook waits
on POST /approvals (plugin/scripts/permission-request.sh).

An approval opens when the hook connects and ends exactly once, through
close() (bar one the terminal answered before the hook arrived, which open()
returns already released): John answers its card, the terminal answers (the mod's report), the
hook goes away, the session or its main-loop turn ends, away mode turns off
while the approval is one the desk would not hold (a background agent's dialog
draws only after its hook returns, and a headless run draws none, so it is
released), or the service stops.
In away mode an open approval has a question card in the session's
conversation, with Allow and Deny, and the cards follow away mode as it turns
on and off. Each card post gets its own id, because mark_question_cancelled
marks only the first card of a request_id. Kept apart from Registry, so the ask
paths (supersession, parking, the away-exit drain, the free-form message
ladder) never meet an approval."""

from __future__ import annotations

import asyncio
import collections
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from server.approval_card import APPROVAL_SUGGESTIONS, decide_reply, input_digest, render_card
from server.gateway.bg_tasks import _spawn_bg

# The mod's AskUserQuestion bridge owns that tool's dialog, and plan approval
# chooses how to leave plan mode, which allow or deny cannot say.
EXCLUDED_TOOLS = frozenset({"AskUserQuestion", "ExitPlanMode"})

_RECENT_CARDS_MAX = 256

# Every card id this registry mints starts with this; an ask's request id is
# eight hex digits, so the prefix tells a card's answer apart after a restart.
_CARD_PREFIX = "apr-"

# The hook's start-up is about 0.5 s; ten seconds is generous and still far
# shorter than any second identical dialog in practice.
_EARLY_SETTLE_SECONDS = 10.0


@dataclass(frozen=True)
class Outcome:
	"""How an approval ended, as its waiting route reads it: "decided" carries
	the PermissionRequest decision for the hook to print; "released" answers
	nothing, which leaves the dialog to the terminal."""
	kind: str
	decision: dict | None = None
	reason: str = ""


@dataclass(eq=False)
class Approval:
	id: str
	cli_session_id: str
	agent_id: str | None
	tool: str
	tool_input: object
	digest: str
	cwd: str
	opened_at: datetime
	future: asyncio.Future
	card_id: str | None = None
	conversation_id: str | None = None
	msg_id: str | None = None
	card_live: bool = False
	closed: bool = False


ConversationResolver = Callable[[str, str], Awaitable[tuple[str, str]]]


async def resolve_card_conversation(registry, backend, cli_session_id: str, cwd: str) -> tuple[str, str]:
	"""(conversation_id, sender) for a session's approval card: its own
	conversation as ask_human resolves it, minting one for an unbound session,
	after the same repair of a binding to an ended conversation."""
	from server.conversation_ops import _resolve_conversation_and_member
	from server.gateway.handlers import _canonical_sender
	from server.session_fallback import apply_fallback
	bound_id = registry.session_to_conversation_id.get(cli_session_id)
	bound = registry.conversations.get(bound_id) if bound_id is not None else None
	if bound is not None and bound.state == "ended":
		apply_fallback(registry, cli_session_id, backend=backend)
	conversation_id = await _resolve_conversation_and_member(registry, cli_session_id, cwd, "Claude", backend=backend)
	return conversation_id, _canonical_sender(registry, conversation_id, cli_session_id, "Claude")


class ApprovalRegistry:
	def __init__(
		self,
		registry,
		session_registry,
		backend,
		logger,
		*,
		resolve_conversation: ConversationResolver | None = None,
		now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
		mono: Callable[[], float] = time.monotonic,
	) -> None:
		self._registry = registry
		self._sessions = session_registry
		self._backend = backend
		self._logger = logger
		self._resolve = resolve_conversation or (
			lambda sid, cwd: resolve_card_conversation(registry, backend, sid, cwd)
		)
		self._now = now
		self._mono = mono
		# Terminal answers that matched no open approval, in case the hook that
		# opens the approval reaches the server a moment later.
		self._early: collections.deque = collections.deque(maxlen=64)
		self._open: dict[str, Approval] = {}
		self._by_card: dict[str, Approval] = {}
		self._recent_cards: collections.OrderedDict[str, None] = collections.OrderedDict()
		# Card posts and withdrawals run one at a time, so no card is posted for
		# an approval that is closing, and two quick away flips cannot post twice.
		self._cards = asyncio.Lock()

	# -- reads ------------------------------------------------------------

	def open_approvals(self) -> list[Approval]:
		return list(self._open.values())

	def holds_at_desk(self, cli_session_id: str, agent_id: str | None) -> bool:
		"""A request is held at the desk only when it is a main-loop dialog of a
		session not known to be non-interactive: a background agent's dialog is
		drawn only after the hook returns, and a headless run draws none. Every
		subagent request is answered 204 at the desk, foreground ones included,
		because the hook input cannot tell a background agent from a foreground
		one."""
		if agent_id is not None:
			return False
		rec = self._sessions.get(cli_session_id) if self._sessions is not None else None
		return rec is None or rec.interactive is not False

	def should_hold(self, cli_session_id: str, agent_id: str | None) -> bool:
		return self._registry.global_away_mode or self.holds_at_desk(cli_session_id, agent_id)

	def owns(self, conversation_id: str, request_id: str) -> bool:
		"""True for an answer keyed by a card this registry posted, live or
		recently ended, or by any id carrying the card prefix, which a restart
		leaves unindexed. Card ids are unique, so the conversation is not needed."""
		return (
			request_id in self._by_card
			or request_id in self._recent_cards
			or request_id.startswith(_CARD_PREFIX)
		)

	# -- lifecycle --------------------------------------------------------

	async def open(
		self, *, cli_session_id: str, agent_id: str | None, tool: str, tool_input: object, cwd: str,
	) -> Approval:
		"""Opens an approval for a dialog that just opened. In away mode its card
		is up before this returns, unless the terminal already answered, which
		returns an approval already released and with no card."""
		digest = input_digest(tool_input)
		early = self._take_early(cli_session_id, agent_id, tool, digest)
		approval = Approval(
			id=uuid.uuid4().hex,
			cli_session_id=cli_session_id,
			agent_id=agent_id,
			tool=tool,
			tool_input=tool_input,
			digest=digest,
			cwd=cwd,
			opened_at=self._now(),
			future=asyncio.get_running_loop().create_future(),
		)
		if early is not None:
			# The terminal answered before the hook arrived: nothing to hold.
			source, decision = early
			reason = f"terminal_before_hook:{source}:{decision}"
			approval.closed = True
			approval.future.set_result(Outcome("released", reason=reason))
			await self._logger.info(
				f"approval_opened: id={approval.id} session={cli_session_id} agent={agent_id} tool={tool}"
			)
			await self._logger.info(
				f"approval_closed: id={approval.id} session={cli_session_id} outcome=released reason={reason}"
			)
			return approval
		self._open[approval.id] = approval
		self._mark_session(cli_session_id)
		await self._logger.info(
			f"approval_opened: id={approval.id} session={cli_session_id} agent={agent_id} tool={tool}"
		)
		if self._registry.global_away_mode:
			async with self._cards:
				await self._post_card(approval)
		return approval

	async def close(self, approval: Approval, outcome: Outcome, *, withdraw: bool = True) -> bool:
		"""The one way an approval ends: settles the route's future, withdraws a
		live card (not on a service stop, when hydration withdraws it after the
		restart), and clears the session's mark with its last approval. False if
		it had already ended."""
		if approval.closed:
			return False
		approval.closed = True
		self._open.pop(approval.id, None)
		if not approval.future.done():
			approval.future.set_result(outcome)
		self._mark_session(approval.cli_session_id)
		if withdraw:
			async with self._cards:
				await self._withdraw(approval)
		await self._logger.info(
			f"approval_closed: id={approval.id} session={approval.cli_session_id} "
			f"outcome={outcome.kind} reason={outcome.reason}"
		)
		return True

	async def answer(self, conversation_id: str, card_id: str, text: str, slot: str | None) -> None:
		"""John's reply to a card (dispatch_responses' branch) decides its
		approval. A reply to a card that has already ended is a benign replay."""
		if slot:
			_spawn_bg(self._backend.delete_response_slot(slot), label=f"approval_slot_cleanup:{slot}")
		approval = self._by_card.get(card_id)
		if approval is None or approval.closed:
			await self._logger.info(
				f"approval_replay_ignored: conversation_id={conversation_id} card_id={card_id}"
			)
			return
		msg_id = approval.msg_id
		async with self._cards:
			# The wait for the lock can outlast the card or the approval.
			if approval.closed or approval.card_id != card_id:
				stale = True
			else:
				stale = False
				# Answered, not withdrawn: the card stays, with the reply under it.
				self._retire_card(approval)
		if stale:
			await self._logger.info(
				f"approval_replay_ignored: conversation_id={conversation_id} card_id={card_id}"
			)
			return
		_spawn_bg(
			self._backend.remove_pending_question_record(conversation_id, card_id),
			label=f"approval_record_cleanup:{conversation_id}:{card_id}",
		)
		_spawn_bg(self._write_reply(conversation_id, msg_id, text), label=f"approval_reply:{conversation_id}")
		await self.close(approval, Outcome("decided", decision=decide_reply(text), reason="phone"))

	async def settle_from_terminal(
		self, cli_session_id: str, *, agent_id: str | None, tool: str, tool_input: object, decision: str, source: str,
	) -> bool:
		"""The mod's report that the terminal answered a dialog. Releases the
		approval with the same session, agent, tool and input, else the oldest
		with the same session, agent and tool, so its hook exits quietly. An
		answer that matches nothing is remembered briefly, for a hook that has
		not reached the server yet."""
		if tool in EXCLUDED_TOOLS:
			return False
		candidates = [
			a for a in self._open.values()
			if a.cli_session_id == cli_session_id and a.agent_id == agent_id and a.tool == tool
		]
		if not candidates:
			await self._logger.info(
				f"approval_settle_unmatched: session={cli_session_id} tool={tool} source={source}"
			)
			self._early.append(
				(cli_session_id, agent_id, tool, input_digest(tool_input), self._mono(), source, decision)
			)
			return False
		digest = input_digest(tool_input)
		exact = [a for a in candidates if a.digest == digest]
		match = min(exact or candidates, key=lambda a: a.opened_at)
		await self._logger.info(
			f"approval_settled_at_terminal: id={match.id} session={cli_session_id} agent={agent_id} tool={tool} "
			f"source={source} decision={decision} exact={bool(exact)}"
		)
		await self.close(match, Outcome("released", reason=f"terminal:{source}:{decision}"))
		return True

	def _take_early(self, cli_session_id: str, agent_id: str | None, tool: str, digest: str) -> tuple[str, str] | None:
		"""Drops remembered terminal answers past the window and consumes the
		one matching a dialog that is just opening, as (source, decision)."""
		now = self._mono()
		fresh = [e for e in self._early if now - e[4] <= _EARLY_SETTLE_SECONDS]
		self._early.clear()
		self._early.extend(fresh)
		for entry in fresh:
			if entry[:4] == (cli_session_id, agent_id, tool, digest):
				self._early.remove(entry)
				return entry[5], entry[6]
		return None

	async def close_session(self, cli_session_id: str, reason: str) -> int:
		matches = [a for a in self._open.values() if a.cli_session_id == cli_session_id]
		for approval in matches:
			await self.close(approval, Outcome("released", reason=reason))
		return len(matches)

	async def close_main_loop(self, cli_session_id: str, reason: str) -> int:
		"""A main-loop dialog cannot outlive its turn; a subagent's can."""
		matches = [a for a in self._open.values() if a.cli_session_id == cli_session_id and a.agent_id is None]
		for approval in matches:
			await self.close(approval, Outcome("released", reason=reason))
		return len(matches)

	# -- away mode --------------------------------------------------------

	def on_away_change(self, active: bool) -> None:
		"""Registry's away observer: the cards follow away mode, and turning it
		off releases the approvals the desk would not hold."""
		if self._open:
			_spawn_bg(self.sync_cards(), label="approvals_away_sync")

	async def sync_cards(self) -> None:
		"""Posts a card for every open approval without one while away, and
		withdraws every live card at the desk, then releases (desk_release) each
		open approval the desk would not hold, so its dialog draws or a headless
		run refuses. Reads the flag under the lock, so the last of several quick
		flips wins, and again before each release."""
		async with self._cards:
			away = self._registry.global_away_mode
			for approval in list(self._open.values()):
				if away:
					await self._post_card(approval)
				else:
					await self._withdraw(approval)
			releasable = [
				a for a in self._open.values() if not self.holds_at_desk(a.cli_session_id, a.agent_id)
			] if not away else []
		# close() takes the card lock itself. A quick off/on flip must not release
		# a request John can answer from the phone again.
		if self._registry.global_away_mode:
			return
		for approval in releasable:
			# Each close awaits, so a flip back on can land between two releases;
			# close() settles the future before its first await, so this check and
			# the settle are atomic.
			if self._registry.global_away_mode:
				return
			await self.close(approval, Outcome("released", reason="desk_release"))

	# -- cards (callers hold self._cards) ---------------------------------

	async def _post_card(self, approval: Approval) -> None:
		"""Posts the approval's card unless it has ended or already has one, and
		withdraws it again if the approval ended while it was being written."""
		if approval.closed or approval.card_live:
			return
		card_id = f"{_CARD_PREFIX}{uuid.uuid4().hex[:12]}"
		try:
			conversation_id, sender = await self._resolve(approval.cli_session_id, approval.cwd)
			title, text = render_card(approval.tool, approval.tool_input, in_subagent=approval.agent_id is not None)
			_correlation, msg_id = await self._backend.write_conversation_message(
				conversation_id, sender, "question", text,
				request_id=card_id, format="markdown", suggestions=APPROVAL_SUGGESTIONS, title=title,
			)
		except Exception as exc:
			await self._logger.surface_error(f"approval_card_post_failed: id={approval.id} {exc}")
			return
		approval.card_id = card_id
		approval.conversation_id = conversation_id
		approval.msg_id = msg_id
		approval.card_live = True
		self._by_card[card_id] = approval
		self._registry.fire_pending_mirror(conversation_id, 1)
		try:
			await self._backend.add_pending_question_record(
				conversation_id, card_id,
				sender=sender, msg_id=msg_id, question_text=text, suggestions=APPROVAL_SUGGESTIONS,
				cli_session_id=approval.cli_session_id, asked_at=approval.opened_at.isoformat(), approval=True,
			)
		except Exception as exc:
			await self._logger.surface_error(f"approval_record_write_failed: id={approval.id} {exc}")
		if approval.closed:
			await self._withdraw(approval)

	async def _withdraw(self, approval: Approval) -> None:
		"""Cancels a live card, which also clears its phone notification and its
		pending_questions record."""
		if not approval.card_live:
			return
		conversation_id, card_id = approval.conversation_id, approval.card_id
		self._retire_card(approval)
		try:
			await self._backend.mark_question_cancelled(conversation_id, card_id)
		except Exception as exc:
			await self._logger.surface_error(f"approval_card_withdraw_failed: id={approval.id} {exc}")

	def _retire_card(self, approval: Approval) -> None:
		"""Takes a live card out of the pending count and the answer index; its
		id stays known, so a late answer to it is a benign replay."""
		if not approval.card_live:
			return
		approval.card_live = False
		self._by_card.pop(approval.card_id, None)
		self._recent_cards[approval.card_id] = None
		while len(self._recent_cards) > _RECENT_CARDS_MAX:
			self._recent_cards.popitem(last=False)
		self._registry.fire_pending_mirror(approval.conversation_id, -1)
		approval.card_id = None

	async def _write_reply(self, conversation_id: str, msg_id: str | None, text: str) -> None:
		try:
			await self._backend.write_conversation_message(
				conversation_id, "John", "human", text, attached_to_msg_id=msg_id,
			)
		except Exception as exc:
			await self._logger.surface_error(f"approval_reply_write_failed: conversation_id={conversation_id} {exc}")

	def _mark_session(self, cli_session_id: str) -> None:
		opened = [a.opened_at for a in self._open.values() if a.cli_session_id == cli_session_id]
		if self._sessions is not None:
			self._sessions.set_awaiting_approval(cli_session_id, min(opened).isoformat() if opened else None)
