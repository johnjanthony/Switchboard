"""Side effects of resolving a parked (future-less) pending request (T-001).

A live resolve returns the answer through the pending's future, and the
ask_human coroutine performs the post-answer bookkeeping when it wakes. A
parked record has no coroutine, so whoever resolves it (the dispatch loop, the
bulk-respond drain) calls finish_parked_resolve to replay that bookkeeping:
delete the Firebase pending_questions record, then queue the chunk 3 session
notices - any convene notices attached while parked first, the answer notice
after - delivered at the session's next turn boundary or prompt. If the
session never returns, the notices die with its registry record at retention;
the answer itself is already in the conversation history either way.
"""

from __future__ import annotations

from server.logging_jsonl import JsonlLogger


async def finish_parked_resolve(backend, session_registry, logger: JsonlLogger, record, answer_text: str) -> None:
	try:
		await backend.remove_pending_question_record(record.conversation_id, record.request_id)
	except Exception as exc:
		await logger.surface_error(f"parked_pending_record_cleanup_failed: {exc}")
	if session_registry is not None:
		for notice in record.notices:
			session_registry.queue_notice(record.cli_session_id, notice)
		question = record.question or "(question unavailable)"
		session_registry.queue_notice(
			record.cli_session_id,
			f"John answered your earlier question '{question}': {answer_text}",
		)
	await logger.info(
		f"parked_pending_resolved: conversation_id={record.conversation_id} "
		f"request_id={record.request_id} cli_session_id={record.cli_session_id}"
	)
