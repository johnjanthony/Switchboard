"""Telegram-triggered Claude Code session spawner."""

from __future__ import annotations

import json
import re
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.config import Config
from server.logging_jsonl import JsonlLogger
from server.registry import Registry

RATE_LIMIT_SECONDS = 60
_TASK_NAME = "SwitchboardSpawn"
_BASE_INSTRUCTION = (
	"John is currently away. All communications MUST go through the "
	"switchboard using one or more of its tools with agent_id='{project_key}'."
)
_DEFAULT_PROMPT = (
	"Ask John what he'd like you to work on."
)
_DEFAULT_COLLAB_PROMPT = (
	"Perform a comprehensive technical review of this codebase. Identify architectural "
	"weaknesses, potential bugs, and high-to-medium priority areas for improvement. Debate "
	"these points critically with your partner until you reach consensus on what needs to "
	"change, then implement those changes and verify them."
)
_COLLAB_INSTRUCTION = (
	"John is currently away. All communications MUST go through the switchboard "
	"using one or more of its tools with agent_id='{agent_id}'.\n\n"
	"You are Agent {agent_num} in a two-agent collaborative session.\n"
	"Session ID: {session_id}\n"
	"Your agent ID: {agent_id}\n\n"
	"COLLABORATION RULES:\n"
	"1. Use message_and_await_agent(session_id=\"{session_id}\", agent_id=\"{agent_id}\", message=\"...\") "
	"to communicate with your partner. Always pass your own agent_id.\n"
	"2. Speak only to your partner — not to John — unless using ask_human or notify_human.\n"
	"3. No meta-commentary. Respond with content directly.\n"
	"4. Critically review your partner's proposals. Be specific.\n"
	"5. Your goal is to reach consensus. When you believe consensus is reached, call "
	"ask_human(question, agent_id=\"{agent_id}\") to confirm with John before proceeding.\n"
	"6. If debate becomes unproductive, call ask_human to report the deadlock.\n"
	"7. After making changes, verify them with appropriate tools before claiming completion.\n"
	"8. If message_and_await_agent returns \"__TIMEOUT__\", call ask_human to check in with John.\n"
	"9. If message_and_await_agent returns an error, call ask_human immediately.\n"
	"{listener_note}\n"
	"TASK:\n"
	"{task}"
)
_LISTENER_NOTE = (
	"\nYour partner will send the first message. Begin by calling "
	"`message_and_await_agent(session_id=\"{session_id}\", agent_id=\"{agent_id}\")` "
	"with no message argument to listen.\n"
)


def _parse_spawn_flags(text: str) -> tuple[str, int, bool]:
	"""Extract --agents=N and --relay flags; return remaining text and parsed values."""
	agents = 1
	relay = False
	parts: list[str] = []
	for part in text.split():
		m = re.match(r"--agents=(\d+)$", part)
		if m:
			agents = int(m.group(1))
		elif part == "--relay":
			relay = True
		else:
			parts.append(part)
	return " ".join(parts), agents, relay


class SpawnHandler:
	def __init__(
		self, config: Config, backend: Any, logger: JsonlLogger, registry: Registry
	) -> None:
		self._spawn_root = config.spawn_root
		self._pending_path = Path(config.log_path).parent / "spawn-pending.json"
		self._sidecar_path = Path(config.log_path).parent / "collab-sessions.json"
		self._backend = backend
		self._logger = logger
		self._registry = registry
		self._last_spawn_time: datetime | None = None

	async def handle(self, raw: str) -> None:
		text = raw[len("/spawn"):].strip()
		await self._handle_spawn(text)

	async def _handle_spawn(self, text: str) -> None:
		if self._spawn_root is None:
			await self._backend.send_text("Spawn not configured.")
			return

		now = datetime.now(timezone.utc)
		if self._last_spawn_time is not None:
			elapsed = (now - self._last_spawn_time).total_seconds()
			if elapsed < RATE_LIMIT_SECONDS:
				remaining = int(RATE_LIMIT_SECONDS - elapsed)
				await self._backend.send_text(f"Rate limited. Try again in {remaining}s.")
				return

		remaining_text, agents, relay = _parse_spawn_flags(text)
		tokens = remaining_text.split(None, 1)
		if not tokens:
			project_path = self._spawn_root
			project_key = self._spawn_root.name
			prompt: str | None = None
		else:
			candidate = self._spawn_root / tokens[0]
			if candidate.is_dir():
				project_path = candidate
				project_key = tokens[0]
				prompt = tokens[1] if len(tokens) > 1 else None
			else:
				project_path = self._spawn_root
				project_key = self._spawn_root.name
				prompt = remaining_text or None

		try:
			project_path.resolve().relative_to(self._spawn_root.resolve())
		except ValueError:
			self._logger.spawn_invalid_path(project_key, str(project_path.resolve()))
			await self._backend.send_text(f"Unknown project: {project_key}.")
			return

		if not project_path.is_dir():
			self._logger.spawn_invalid_path(project_key, str(project_path.resolve()))
			await self._backend.send_text(f"Unknown project: {project_key}.")
			return

		if agents == 2:
			await self._handle_collab_spawn(project_path, project_key, relay, prompt)
		elif agents == 1:
			await self._handle_single_spawn(project_path, project_key, prompt)
		else:
			await self._backend.send_text(f"Unsupported --agents={agents}. Only --agents=2 is supported.")

	async def _handle_single_spawn(
		self, project_path: Path, project_key: str, prompt: str | None
	) -> None:
		base = _BASE_INSTRUCTION.format(project_key=project_key)
		user_prompt = prompt or _DEFAULT_PROMPT
		effective_prompt = f"{base} {user_prompt}"

		pending = {"prompt": effective_prompt, "project_path": str(project_path)}
		try:
			self._pending_path.write_text(json.dumps(pending), encoding="utf-8")
			subprocess.run(
				["schtasks", "/run", "/tn", _TASK_NAME],
				check=True, capture_output=True,
			)
		except Exception as exc:
			self._pending_path.unlink(missing_ok=True)
			self._logger.spawn_failed(
				project_key, str(project_path), [_TASK_NAME], str(exc)
			)
			await self._backend.send_text(f"Failed to spawn: {exc}.")
			return

		self._last_spawn_time = datetime.now(timezone.utc)
		spawn_id = secrets.token_hex(4)
		self._logger.spawn_started(
			spawn_id, project_key, str(project_path),
			prompt if prompt is not None else "(ask on start)",
		)
		await self._backend.send_spawn_ack(project_key, prompt)

	async def _handle_collab_spawn(
		self, project_path: Path, project_key: str, relay: bool, prompt: str | None
	) -> None:
		from server.collab import CollabSession
		task = prompt or _DEFAULT_COLLAB_PROMPT
		session_id = f"{project_key}-{secrets.token_hex(4)}"
		agent_1_id = f"{session_id}-1"
		agent_2_id = f"{session_id}-2"

		def _make_prompt(agent_num: int, agent_id: str, listener: bool) -> str:
			note = _LISTENER_NOTE.format(session_id=session_id, agent_id=agent_id) if listener else ""
			return _COLLAB_INSTRUCTION.format(
				agent_num=agent_num,
				session_id=session_id,
				agent_id=agent_id,
				listener_note=note,
				task=task,
			)

		pending = {
			"session_id": session_id,
			"relay": relay,
			"agents": [
				{"agent_id": agent_1_id, "prompt": _make_prompt(1, agent_1_id, False),
				 "project_path": str(project_path)},
				{"agent_id": agent_2_id, "prompt": _make_prompt(2, agent_2_id, True),
				 "project_path": str(project_path)},
			],
		}

		existing: list = []
		if self._sidecar_path.exists():
			try:
				existing = json.loads(self._sidecar_path.read_text(encoding="utf-8"))
			except Exception as exc:
				self._logger.surface_error(f"collab_sidecar_read_error: {exc}")
		existing.append({
			"session_id": session_id,
			"agent_ids": [agent_1_id, agent_2_id],
			"task": task,
			"created_at": datetime.now(timezone.utc).isoformat(),
		})
		self._sidecar_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")

		try:
			self._pending_path.write_text(json.dumps(pending), encoding="utf-8")
			subprocess.run(["schtasks", "/run", "/tn", _TASK_NAME], check=True, capture_output=True)
		except Exception as exc:
			self._pending_path.unlink(missing_ok=True)
			self._logger.spawn_failed(project_key, str(project_path), [_TASK_NAME], str(exc))
			await self._backend.send_text(f"Failed to spawn collab: {exc}.")
			return

		self._last_spawn_time = datetime.now(timezone.utc)

		session = CollabSession(
			session_id=session_id,
			agent_ids=(agent_1_id, agent_2_id),
			task=task,
			relay=relay,
		)
		self._registry.add_session(session)

		try:
			await self._backend.write_session_meta(session_id, [agent_1_id, agent_2_id], task)
		except Exception as exc:
			self._logger.surface_error(f"collab_meta_write_error: {exc}")

		try:
			await self._backend.start_inject_listener(session_id)
		except Exception as exc:
			self._logger.surface_error(f"collab_inject_listener_error: {exc}")

		spawn_id = secrets.token_hex(4)
		self._logger.spawn_started(spawn_id, project_key, str(project_path), f"[collab] {task[:60]}")
		await self._backend.send_spawn_ack(project_key, f"[collab] {task[:60]}")
