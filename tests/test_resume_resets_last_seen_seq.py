"""T-149: handle_resume must reset each moved member's last_seen_seq to 0 so a
dormant member carrying a high last_seen_seq from the source conversation does
not wake to empty context in the fresh continuation conversation (whose only
message is the resume system message at seq 0). Mirrors the reset that
_perform_combine already does for combine-resume."""
import json
import pytest

from server.config import Config
from server.logging_jsonl import JsonlLogger
from server.registry import Registry, Conversation, ConversationMember
from server.spawn import SpawnHandler


class _StubBackend:
	"""Minimal backend: every ConversationStore write is a no-op AsyncMock-free stub."""
	async def write_conversation_meta(self, *a, **k): pass
	async def write_conversation_message(self, *a, **k): return ""
	async def write_conversation_member(self, *a, **k): pass
	async def remove_conversation_member(self, *a, **k): pass
	async def set_conversation_state(self, *a, **k): pass
	async def send_text(self, *a, **k): pass
	async def set_global_away_mode(self, *a, **k): pass


@pytest.mark.asyncio
async def test_handle_resume_resets_last_seen_seq(tmp_path, monkeypatch):
	cfg = Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
		windows_spawn_root=tmp_path,
	)
	registry = Registry()
	source = Conversation(id="conv-src", title="Old work")
	# A dormant member that had seen 50 messages in the source conversation.
	member = ConversationMember(
		cli_session_id="sess-1", sender="Claude", cwd="C:/Work/X",
		surface="windows", joined_at=0.0, alive=False,
		session_ended_at="2026-06-13T00:00:00+00:00", last_seen_seq=50,
	)
	source.members_active["sess-1"] = member
	registry.conversations["conv-src"] = source

	handler = SpawnHandler(cfg, _StubBackend(), JsonlLogger(cfg.log_path), registry)
	# Do not actually launch a terminal; stub the launcher and the login gate.
	async def _no_launch(): pass
	async def _logged_in(): return True
	monkeypatch.setattr(handler, "_invoke_launcher", _no_launch)
	monkeypatch.setattr(handler, "_user_has_interactive_session", _logged_in)

	await handler.handle_resume({"type": "resume", "source_conversation_id": "conv-src"})

	# The member moved into the new continuation conversation.
	new_ids = [cid for cid in registry.conversations if cid != "conv-src"]
	assert len(new_ids) == 1
	new_conv = registry.conversations[new_ids[0]]
	moved = new_conv.members_active["sess-1"]
	assert moved.last_seen_seq == 0, (
		f"expected last_seen_seq reset to 0, got {moved.last_seen_seq} - "
		"the resumed member would wake to empty context"
	)
