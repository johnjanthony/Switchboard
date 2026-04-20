"""Telegram-triggered Claude Code session spawner."""

from __future__ import annotations

import secrets
import subprocess
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from server.config import Config
from server.logging_jsonl import JsonlLogger

if TYPE_CHECKING:
	from server.telegram import TelegramBackend

RATE_LIMIT_SECONDS = 60
_DEFAULT_PROMPT = (
	"You've been spawned in {project_key}. Use ask_human to ask the developer "
	"what they'd like you to work on, with agent_id='{project_key}'."
)


class SpawnHandler:
	def __init__(
		self, config: Config, backend: "TelegramBackend", logger: JsonlLogger
	) -> None:
		self._spawn_root = config.spawn_root
		self._backend = backend
		self._logger = logger
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

		tokens = text.split(None, 1)
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
				prompt = text or None

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

		effective_prompt = prompt or _DEFAULT_PROMPT.format(project_key=project_key)
		prompt_preview = prompt

		try:
			subprocess.Popen(
				[
					"wt", "new-tab", "--",
					"claude", "-p", effective_prompt,
					"--dangerously-skip-permissions",
				],
				cwd=str(project_path),
			)
		except Exception as exc:
			await self._backend.send_text(f"Failed to spawn: {exc}.")
			return

		self._last_spawn_time = now

		spawn_id = secrets.token_hex(4)
		self._logger.spawn_started(
			spawn_id,
			project_key,
			str(project_path),
			prompt_preview if prompt_preview is not None else "(ask on start)",
		)
		await self._backend.send_spawn_ack(project_key, prompt_preview)
