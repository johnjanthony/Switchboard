"""Claude Code session spawner."""

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
	"John is currently away. All communications MUST go through the switchboard "
	"using one or more of its tools. Your channel_id is '{channel_id}'. Use it "
	"for every tool call — ask_human, notify_human, send_document_human. "
	"sender defaults to 'Claude' unless you were given a different name."
)
_DEFAULT_PROMPT = "Ask John what he'd like you to work on."
_DEFAULT_COLLAB_PROMPT = (
	"Perform a comprehensive technical review of this codebase. Identify architectural "
	"weaknesses, potential bugs, and high-to-medium priority areas for improvement. Debate "
	"these points critically with your partner until you reach consensus on what needs to "
	"change, then implement those changes and verify them."
)
_COLLAB_INSTRUCTION = (
	"John is currently away. All communications MUST go through the switchboard "
	"using one or more of its tools. Your channel_id is '{channel_id}'. Your sender "
	"is '{sender}'. Use both for every tool call — ask_human, notify_human, "
	"send_document_human, and message_and_await_agent.\n\n"
	"You are {sender} in a two-agent collaborative session.\n\n"
	"COLLABORATION RULES:\n"
	"1. Use message_and_await_agent(channel_id=\"{channel_id}\", sender=\"{sender}\", message=\"...\") "
	"to communicate with your partner. Always pass your own sender.\n"
	"2. Speak only to your partner — not to John — unless using ask_human or notify_human.\n"
	"3. No meta-commentary. Respond with content directly.\n"
	"4. Critically review your partner's proposals. Be specific.\n"
	"5. Your goal is to reach consensus. When you believe consensus is reached, call "
	"ask_human(question, channel_id=\"{channel_id}\", sender=\"{sender}\") to confirm with John.\n"
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
	"`message_and_await_agent(channel_id=\"{channel_id}\", sender=\"{sender}\")` "
	"with no message argument to listen.\n"
)


def _make_channel_id(project_key: str) -> str:
	return f"{project_key}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"


def _parse_spawn_flags(text: str) -> tuple[str, list[str]]:
	"""Extract --claude, --gemini, --collab flags; return (remaining_text, backends)."""
	backends: list[str] = []
	parts: list[str] = []
	for part in text.split():
		if part == "--claude":
			backends.append("claude")
		elif part == "--gemini":
			backends.append("gemini")
		elif part == "--collab":
			backends.extend(["claude", "claude"])
		elif re.match(r"--agents=\d+$", part):
			raise ValueError("--agents is no longer supported; use --claude, --gemini, or --collab flags")
		elif part == "--relay":
			raise ValueError("--relay is no longer supported; use --collab for relay sessions")
		else:
			parts.append(part)
	
	if not backends:
		backends = ["claude"]
	return " ".join(parts), backends


def _get_backend_name(backend: str) -> str:
	return "Gemini" if backend == "gemini" else "Claude"


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

		try:
			remaining_text, backends = _parse_spawn_flags(text)
		except ValueError as exc:
			await self._backend.send_text(str(exc))
			return

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

		if len(backends) > 1:
			await self._handle_collab_spawn(project_path, project_key, prompt, backends[:2])
		else:
			await self._handle_single_spawn(project_path, project_key, prompt, backends[0])

	async def _handle_single_spawn(
		self, project_path: Path, project_key: str, prompt: str | None, backend_type: str
	) -> None:
		channel_id = _make_channel_id(project_key)
		sender = _get_backend_name(backend_type)
		base = _BASE_INSTRUCTION.format(channel_id=channel_id).replace(
			"sender defaults to 'Claude'", f"sender defaults to '{sender}'"
		)
		user_prompt = prompt or _DEFAULT_PROMPT
		effective_prompt = f"{base} {user_prompt}"

		pending = {
			"channel_id": channel_id,
			"backend": backend_type,
			"prompt": effective_prompt,
			"project_path": str(project_path),
		}
		try:
			self._pending_path.write_text(json.dumps(pending), encoding="utf-8")
			subprocess.run(
				["schtasks", "/run", "/tn", _TASK_NAME],
				check=True, capture_output=True,
			)
		except Exception as exc:
			self._pending_path.unlink(missing_ok=True)
			self._logger.spawn_failed(project_key, str(project_path), [_TASK_NAME], str(exc))
			await self._backend.send_text(f"Failed to spawn: {exc}.")
			return

		self._last_spawn_time = datetime.now(timezone.utc)
		self._registry.set_away_mode(True)
		self._logger.away_mode_entered(reason="spawn")

		try:
			await self._backend.write_session_meta(
				channel_id, "single", project_key,
			)
		except Exception as exc:
			self._logger.surface_error(f"single_meta_write_error: {exc}")

		spawn_id = secrets.token_hex(4)
		self._logger.spawn_started(
			spawn_id, project_key, str(project_path),
			prompt if prompt is not None else "(ask on start)",
		)
		await self._backend.send_spawn_ack(channel_id, prompt)

	async def _handle_collab_spawn(
		self, project_path: Path, project_key: str, prompt: str | None, backends: list[str]
	) -> None:
		from server.collab import CollabSession
		task = prompt or _DEFAULT_COLLAB_PROMPT
		channel_id = _make_channel_id(project_key)
		
		# Sender names derived directly from backends
		s1 = _get_backend_name(backends[0])
		s2 = _get_backend_name(backends[1])
		agent_senders = [s1, s2]

		def _make_prompt(sender: str, listener: bool) -> str:
			note = _LISTENER_NOTE.format(channel_id=channel_id, sender=sender) if listener else ""
			return _COLLAB_INSTRUCTION.format(
				channel_id=channel_id,
				sender=sender,
				listener_note=note,
				task=task,
			)

		pending = {
			"channel_id": channel_id,
			"agents": [
				{
					"backend": backends[0],
					"sender": s1,
					"prompt": _make_prompt(s1, False),
					"project_path": str(project_path)
				},
				{
					"backend": backends[1],
					"sender": s2,
					"prompt": _make_prompt(s2, True),
					"project_path": str(project_path)
				},
			],
		}

		existing: list = []
		if self._sidecar_path.exists():
			try:
				existing = json.loads(self._sidecar_path.read_text(encoding="utf-8"))
			except Exception as exc:
				self._logger.surface_error(f"collab_sidecar_read_error: {exc}")
		existing.append({
			"channel_id": channel_id,
			"agent_senders": agent_senders,
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
		self._registry.set_away_mode(True)
		self._logger.away_mode_entered(reason="spawn")

		session = CollabSession(
			session_id=channel_id,
			agent_senders=list(agent_senders),
			task=task,
		)
		self._registry.add_session(session)

		try:
			await self._backend.write_session_meta(
				channel_id, "collab", project_key,
				agent_senders=agent_senders, task=task,
			)
		except Exception as exc:
			self._logger.surface_error(f"collab_meta_write_error: {exc}")

		try:
			await self._backend.start_inject_listener(channel_id)
		except Exception as exc:
			self._logger.surface_error(f"collab_inject_listener_error: {exc}")

		spawn_id = secrets.token_hex(4)
		self._logger.spawn_started(spawn_id, project_key, str(project_path), f"[collab] {task[:60]}")
		await self._backend.send_spawn_ack(channel_id, f"[collab] {task[:60]}")
