"""Contract tests for the trait surfaces (Backend, MessageWriter, ResponsePoller,
AwayModeMirror).
"""

import asyncio
import inspect

import pytest

from server.messenger import (
	Backend,
	MessageWriter,
	ResponsePoller,
	AwayModeMirror,
	IncomingResponse,
)


def test_incoming_response_is_simple_dataclass():
	r = IncomingResponse(correlation=42, text="yes")
	assert r.correlation == 42
	assert r.text == "yes"


class _StubBackend(MessageWriter, ResponsePoller, AwayModeMirror, Backend):
	"""Minimal concrete subclass implementing only the abstract methods."""

	async def send_timeout_followup(self, *a, **k):
		return None

	async def poll_responses(self):
		if False:
			yield None

	async def aclose(self):
		pass


class TestBackendContract:
	"""Contract tests for the Backend(aclose) base."""

	def test_aclose_is_abstract(self):
		# Backend is an ABC with one abstract method; instantiating directly fails.
		with pytest.raises(TypeError):
			Backend()  # type: ignore[abstract]

		# `aclose` is declared abstract.
		assert getattr(Backend.aclose, "__isabstractmethod__", False)


class TestMessageWriterContract:
	"""Contract tests for MessageWriter (send_timeout_followup,
	send_text, send_stale_reply_notice,
	mark_question_cancelled).

	write_channel_message has been retired; write_conversation_message is the
	canonical write method and lives on ConversationStore."""

	def test_required_abstracts_declared(self):
		expected = {"send_timeout_followup"}
		declared = {
			name
			for name, member in inspect.getmembers(MessageWriter)
			if getattr(member, "__isabstractmethod__", False)
		}
		assert expected <= declared

	def test_no_op_methods_exist(self):
		for method_name in (
			"send_text",
			"send_stale_reply_notice",
			"mark_question_cancelled",
		):
			assert hasattr(MessageWriter, method_name), f"Missing: {method_name}"

	def test_write_conversation_message_in_conversation_store(self):
		"""write_conversation_message is declared on ConversationStore (not MessageWriter)."""
		from server.messenger import ConversationStore
		assert hasattr(ConversationStore, "write_conversation_message")

	def test_write_conversation_message_accepts_rejected_kwarg(self):
		"""write_conversation_message must accept rejected=True so stale-reply notices
		can be rendered as transient toasts on Android."""
		from server.messenger import ConversationStore
		sig = inspect.signature(ConversationStore.write_conversation_message)
		assert "rejected" in sig.parameters

	def test_mark_question_cancelled_signature(self):
		sig = inspect.signature(MessageWriter.mark_question_cancelled)
		assert list(sig.parameters) == ["self", "conversation_id", "request_id"]

	def test_send_stale_reply_notice_signature(self):
		sig = inspect.signature(MessageWriter.send_stale_reply_notice)
		assert list(sig.parameters) == ["self", "conversation_id", "sender"]

	@pytest.mark.asyncio
	async def test_mark_question_cancelled_default_is_noop(self):
		backend = _StubBackend()
		await backend.mark_question_cancelled("c:/work/sw", "abc12345")
		await backend.mark_question_cancelled("c:/work/other", "00000000")

	@pytest.mark.asyncio
	async def test_send_stale_reply_notice_default_is_noop(self):
		backend = _StubBackend()
		await backend.send_stale_reply_notice("c:/work/sw", "Claude")


class TestResponsePollerContract:
	"""Contract tests for ResponsePoller (poll_responses,
	poll_away_mode_commands, delete_response_slot, reset_all_pending_responses)."""

	def test_required_abstracts_declared(self):
		expected = {"poll_responses"}
		declared = {
			name
			for name, member in inspect.getmembers(ResponsePoller)
			if getattr(member, "__isabstractmethod__", False)
		}
		assert expected <= declared

	def test_no_op_methods_exist(self):
		for method_name in (
			"poll_away_mode_commands",
			"poll_status_request_commands",
			"delete_response_slot",
			"reset_all_pending_responses",
		):
			assert hasattr(ResponsePoller, method_name), f"Missing: {method_name}"


class TestAwayModeMirrorContract:
	"""Contract tests for AwayModeMirror (load_away_mode_snapshot,
	start_away_mode_listeners, reset_all_away_mode, delete_legacy_away_mode_node).
	The legacy per-cwd mirror was retired in the conversations redesign — the
	global flag is the only away-mode signal."""

	def test_methods_exist(self):
		# AwayModeMirror has no @abstractmethod; all are no-op defaults.
		for method_name in (
			"load_away_mode_snapshot",
			"start_away_mode_listeners",
			"reset_all_away_mode",
			"delete_legacy_away_mode_node",
		):
			assert hasattr(AwayModeMirror, method_name), f"Missing: {method_name}"

