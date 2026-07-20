"""Single owner of the terminal side of a pending ask_human (DT-1).

Every terminal path - ask_human's own cancel/timeout/error arms, force-end,
combine, session-end, spawn's stale-pending cleanup, the parked TTL sweep -
ends a PendingRequest through terminate_pending. It owns the triple those
sites used to hand-roll and forget steps of (REV-001, REV-102): pop the
registry record, settle the future (parked records have none), and perform
the Firebase cancel plus benign-replay bookkeeping.

Resolving (resolve_text=...) vs cancelling (resolve_text=None) a live future
matters (T-145): a cancelled future surfaces on the agent's MCP client as a
transport error, which the agent retries - re-stranding it or minting orphan
state. A resolved future hands the agent a semantic terminal sentinel it
returns normally, so it stops. Cancel is reserved for dead awaiters: the
asker's own already-settled arms and spawn's cleanup of a vanished agent.
"""

from __future__ import annotations


async def terminate_pending(
	registry,
	backend,
	logger,
	record,
	*,
	resolve_text: str | None = None,
	mark_cancelled: bool = True,
	remember_resolved: bool = False,
) -> bool:
	"""Terminally end one PendingRequest. Returns True if this call popped the
	record; False if it was already gone or superseded (then: NO side effects).

	- Pops the registry entry (identity-guarded via pop_record) and fires the
	  pending-mirror decrement. The pop is synchronous and MUST stay before any
	  await: ask_human's timeout arm relies on pop-before-suspension to close
	  the window where a just-landed answer is consumed against a future that
	  already timed out (REV-108).
	- Settles the future: resolve_text=None cancels it; a string resolves it
	  with that terminal sentinel. Parked records (future=None) skip this.
	- mark_cancelled=True writes the Firebase cancelled flag (which also
	  removes the pending_questions record). Pass False when the awaiting
	  coroutine's own exception arm performs the Firebase cleanup.
	- remember_resolved=True feeds the benign-replay memory so a late answer
	  logs replayed_answer_ignored instead of firing the phone's alarming
	  "reply withdrawn" notice.
	"""
	if not registry.pop_record(record):
		return False
	if record.future is not None and not record.future.done():
		if resolve_text is None:
			record.future.cancel()
		else:
			record.future.set_result(resolve_text)
	if remember_resolved:
		registry.remember_resolved(record.conversation_id, record.request_id)
	if mark_cancelled and backend is not None:
		try:
			await backend.mark_question_cancelled(record.conversation_id, record.request_id)
		except Exception as exc:
			if logger is not None:
				await logger.surface_error(
					f"terminate_pending_mark_cancelled_failed: conv={record.conversation_id} req={record.request_id} {exc}"
				)
	return True
