"""T-147: a solo (single-agent) resume prompt must NOT instruct enter_conversation.

enter_conversation parks the agent in the continuation conversation's wait queue
waiting for a peer to speak. In a solo resume there is no other alive member, so
the agent blocks until timeout and never comes online to John. A solo resume must
instead direct the agent to come online via ask_human / notify_human. The
enter_conversation instruction is only correct when the resume brings back
multiple alive members who will surface context for each other."""
import glob
import json
import os

import pytest

from server.config import Config
from server.logging_jsonl import JsonlLogger
from server.registry import Registry, Conversation, ConversationMember
from server.spawn import SpawnHandler


class _StubBackend:
	"""Minimal backend: every ConversationStore write is a no-op stub."""
	async def write_conversation_meta(self, *a, **k): pass
	async def write_conversation_message(self, *a, **k): return ""
	async def write_conversation_member(self, *a, **k): pass
	async def remove_conversation_member(self, *a, **k): pass
	async def set_conversation_state(self, *a, **k): pass
	async def send_text(self, *a, **k): pass
	async def set_global_away_mode(self, *a, **k): pass


def _make_handler(tmp_path, monkeypatch):
	cfg = Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
		windows_spawn_root=tmp_path,
	)
	registry = Registry()
	handler = SpawnHandler(cfg, _StubBackend(), JsonlLogger(cfg.log_path), registry)

	async def _no_launch(): pass
	async def _logged_in(): return True
	monkeypatch.setattr(handler, "_invoke_launcher", _no_launch)
	monkeypatch.setattr(handler, "_user_has_interactive_session", _logged_in)
	return cfg, registry, handler


def _read_agent_prompts(tmp_path):
	files = glob.glob(os.path.join(str(tmp_path), "spawn-pending-*.json"))
	assert len(files) == 1, f"expected exactly one spawn-pending file, got {files}"
	pending = json.loads(open(files[0], encoding="utf-8").read())
	return [a["prompt"] for a in pending["agents"]]


@pytest.mark.asyncio
async def test_solo_resume_prompt_does_not_instruct_enter_conversation(tmp_path, monkeypatch):
	cfg, registry, handler = _make_handler(tmp_path, monkeypatch)
	source = Conversation(id="conv-src", title="Old work")
	member = ConversationMember(
		cli_session_id="sess-1", sender="Claude", cwd="C:/Work/X",
		surface="windows", joined_at=0.0, alive=False,
		session_ended_at="2026-06-13T00:00:00+00:00",
	)
	source.members_active["sess-1"] = member
	registry.conversations["conv-src"] = source

	await handler.handle_resume({"type": "resume", "source_conversation_id": "conv-src"})

	prompts = _read_agent_prompts(tmp_path)
	assert len(prompts) == 1
	prompt = prompts[0]
	# The multi-agent branch issues the imperative "Call join_conversation(...)".
	# A solo resume must not, since no peer will ever wake that wait.
	assert "Call enter_conversation" not in prompt, (
		"solo resume prompt instructed enter_conversation; the agent would block "
		"in the wait queue forever with no peer to wake it"
	)
	assert "ask_human" in prompt, (
		"solo resume prompt should direct the agent to come online via ask_human"
	)


@pytest.mark.asyncio
async def test_multi_agent_resume_prompt_still_instructs_join_conversation(tmp_path, monkeypatch):
	cfg, registry, handler = _make_handler(tmp_path, monkeypatch)
	source = Conversation(id="conv-src", title="Collab work")
	for name, sess in (("Claude Win", "sess-1"), ("Claude WSL", "sess-2")):
		source.members_active[sess] = ConversationMember(
			cli_session_id=sess, sender=name, cwd="C:/Work/X",
			surface="windows", joined_at=0.0, alive=False,
			session_ended_at="2026-06-13T00:00:00+00:00",
		)
	registry.conversations["conv-src"] = source

	await handler.handle_resume({"type": "resume", "source_conversation_id": "conv-src"})

	prompts = _read_agent_prompts(tmp_path)
	assert len(prompts) == 2
	for prompt in prompts:
		assert "Call join_conversation" in prompt, (
			"multi-agent resume prompt should still instruct join_conversation so "
			"each agent surfaces context from its alive peers"
		)
