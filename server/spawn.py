"""Claude Code session spawner."""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.canonicalization import canonicalize_cwd
from server.config import Config
from server.logging_jsonl import JsonlLogger
from server.registry import Registry

RATE_LIMIT_SECONDS = 60
_TASK_NAME = "SwitchboardSpawn"

_BASE_INSTRUCTION = (
	"John is currently away. All communications MUST go through the switchboard "
	"using one or more of its tools. "
	"Your `cwd` for switchboard tool calls is your current working directory — "
	"read it via $PWD or from your system prompt's 'Primary working directory' field. "
	"sender is REQUIRED on every messaging tool call (notify_human, ask_human, "
	"send_document_human, message_and_await_agent, end_collab). Use '{sender_default}' "
	"unless you were given a different name. "
	"Title: optional on every messaging tool. SET ONE on your first call — "
	"synthesize from your task or use the leaf folder name if fresh. Update when scope "
	"materially changes; otherwise omit."
)
_DEFAULT_PROMPT = "Ask John what he'd like you to work on."
_DEFAULT_COLLAB_PROMPT = (
	"Perform a comprehensive technical review of this codebase. Identify architectural "
	"weaknesses, potential bugs, and high-to-medium priority areas for improvement. Debate "
	"these points critically with your partner until you reach consensus on what needs to "
	"change, then implement those changes and verify them."
)
_COLLAB_INSTRUCTION = (
	"John is currently away. All communications MUST go through the Switchboard MCP. "
	"Follow the \"Collab mode\" protocol in skill/SKILL.md for every collab rule, "
	"including how to terminate via end_collab and how the designated reporter "
	"reaches John.\n\n"
	"Your `cwd` is the shared session key — both agents in this collab use the same cwd. "
	"Read it from $PWD or your system prompt's 'Primary working directory' field. "
	"Use your own agent name as the `sender` for every Switchboard tool call.\n\n"
	"Both agents start in parallel. Do your initial research / analysis, then send "
	"your opening position via message_and_await_agent. You will receive your "
	"partner's independent opening as your first delivery — treat it as their "
	"opening position and respond to it.\n\n"
	"TASK:\n"
	"{task}"
)


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
			backends.extend(["claude", "gemini"])
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

	async def _cancel_prior_pending(self, canonical_cwd: str) -> None:
		"""Cancel any pending ask_human requests left over for this cwd before launching
		a new agent. Without this, a prior agent that died without reaching its tool-handler
		cancellation path (common with MCP streamable-HTTP transport) leaves stale questions
		hanging on phone and server until the 24h timeout."""
		cancelled = self._registry.cancel_pending_for_cwd(canonical_cwd)
		if not cancelled:
			return
		for request_id in cancelled:
			try:
				await self._backend.mark_question_cancelled(canonical_cwd, request_id)
			except Exception as exc:
				await self._logger.surface_error(
					f"mark_cancelled_failed_on_spawn: cwd={canonical_cwd} req={request_id} {exc}"
				)
		await self._logger.pending_cancelled_on_spawn(canonical_cwd, cancelled)

	async def handle(self, raw: str) -> None:
		stripped = raw.strip()
		if stripped.startswith("/spawn"):
			await self._handle_spawn(stripped[len("/spawn"):].strip())
		elif stripped.startswith("/away-mode"):
			await self._handle_away_mode_command(stripped[len("/away-mode"):].strip())
		# Unknown command: silently ignore. The command watcher surfaces its
		# own errors, and we don't want a typo in the Android app to crash
		# the dispatcher.

	async def _handle_away_mode_command(self, arg: str) -> None:
		arg = arg.strip().lower()
		if arg == "on":
			self._registry.set_global_away(True)
			await self._logger.away_mode_entered(reason="android")
		elif arg == "off":
			self._registry.set_global_away(False)
			await self._logger.away_mode_exited(reason="android")
		else:
			await self._logger.surface_error(f"away_mode_unknown_subcommand: {arg!r}")


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
				# tokens[0] doesn't resolve as a direct child. Search one level down for
				# matching subdirs (e.g. "develop" → rpdm/develop, rpg-one/develop).
				# If exactly one match: use it. Multiple: ambiguous, error with suggestions.
				# None: preserve the terminal-form fallback (treat full input as prompt).
				try:
					nested = [p for p in self._spawn_root.glob(f"*/{tokens[0]}") if p.is_dir()]
				except OSError:
					nested = []
				if len(nested) == 1:
					project_path = nested[0]
					project_key = nested[0].relative_to(self._spawn_root).as_posix()
					prompt = tokens[1] if len(tokens) > 1 else None
				elif len(nested) > 1:
					suggestions = ", ".join(
						p.relative_to(self._spawn_root).as_posix() for p in nested
					)
					await self._logger.spawn_invalid_path(tokens[0], f"ambiguous: {suggestions}")
					await self._backend.send_text(
						f"Ambiguous project '{tokens[0]}'. Multiple matches: {suggestions}. "
						f"Use the full relative path."
					)
					return
				else:
					project_path = self._spawn_root
					project_key = self._spawn_root.name
					prompt = remaining_text or None

		try:
			project_path.resolve().relative_to(self._spawn_root.resolve())
		except ValueError:
			await self._logger.spawn_invalid_path(project_key, str(project_path.resolve()))
			await self._backend.send_text(f"Unknown project: {project_key}.")
			return

		if not project_path.is_dir():
			await self._logger.spawn_invalid_path(project_key, str(project_path.resolve()))
			await self._backend.send_text(f"Unknown project: {project_key}.")
			return

		canonical_cwd = canonicalize_cwd(str(project_path))
		collision_outcome = await self._maybe_handle_spawn_collision(canonical_cwd)
		if collision_outcome == "cancel":
			# User cancelled at the collision dialog — don't launch and don't bump rate-limit.
			return

		if len(backends) > 1:
			await self._handle_collab_spawn(project_path, project_key, prompt, backends[:2])
		else:
			await self._handle_single_spawn(project_path, project_key, prompt, backends[0])

	async def _maybe_handle_spawn_collision(self, canonical_cwd: str) -> str | None:
		"""Detect a channel-content collision and surface the spawn-collision dialog.

		Returns:
			- None — no collision, proceed normally
			- "continue" — user kept the existing channel; spawn proceeds
			- "clear" — user wiped the channel (already done here); spawn proceeds
			- "cancel" — user cancelled; caller must not launch

		Best-effort: if any step of the collision check fails (Firebase blip, missing
		listener support, etc.), fall through to spawn rather than blocking the user."""
		import uuid

		try:
			has_msgs = await self._backend.has_messages(canonical_cwd)
		except Exception as exc:
			# Includes the test-double case where has_messages isn't a real coroutine.
			await self._logger.surface_error(f"spawn_collision_check_failed: {exc}")
			return None

		if not has_msgs:
			return None

		spawn_id = str(uuid.uuid4())

		try:
			meta = await self._backend.read_channel_meta(canonical_cwd)
		except Exception as exc:
			await self._logger.surface_error(f"spawn_collision_meta_read_failed: {exc}")
			meta = {"title": None, "last_activity_at": None, "hidden": False}

		try:
			await self._backend.write_spawn_collision_prompt(
				spawn_id=spawn_id,
				cwd=canonical_cwd,
				channel_title=meta.get("title"),
				last_activity_at=meta.get("last_activity_at"),
				hidden=meta.get("hidden", False),
			)
		except Exception as exc:
			await self._logger.surface_error(f"spawn_collision_dialog_write_failed: {exc}")
			return None

		await self._logger.spawn_collision_detected(canonical_cwd, spawn_id)

		try:
			decision = await self._backend.poll_spawn_collision_decision(spawn_id)
		except NotImplementedError:
			# No listener support (e.g., local-only backend). Fall through to spawn.
			await self._safe_clear_spawn_collision_prompt(spawn_id)
			return None
		except Exception as exc:
			await self._logger.surface_error(f"spawn_collision_decision_poll_failed: {exc}")
			await self._safe_clear_spawn_collision_prompt(spawn_id)
			return None

		await self._safe_clear_spawn_collision_prompt(spawn_id)

		action = (decision or {}).get("action", "cancel")
		if action == "cancel":
			return "cancel"
		if action == "clear":
			try:
				await self._backend.wipe_channel(canonical_cwd)
				await self._backend.set_channel_hidden(canonical_cwd, False)
			except Exception as exc:
				await self._logger.surface_error(f"spawn_collision_wipe_failed: {exc}")
				# Wipe failed but user wanted to proceed — fall through as continue.
				return "continue"
			return "clear"
		if action == "continue":
			return "continue"
		await self._logger.surface_error(f"spawn_collision_unknown_action: {action!r}")
		return "cancel"

	async def _safe_clear_spawn_collision_prompt(self, spawn_id: str) -> None:
		try:
			await self._backend.clear_spawn_collision_prompt(spawn_id)
		except Exception as exc:
			await self._logger.surface_error(f"spawn_collision_clear_failed: {exc}")

	async def _handle_single_spawn(
		self, project_path: Path, project_key: str, prompt: str | None, backend_type: str
	) -> None:
		channel_id = canonicalize_cwd(str(project_path))
		sender = _get_backend_name(backend_type)
		base = _BASE_INSTRUCTION.format(sender_default=sender)
		user_prompt = prompt or _DEFAULT_PROMPT
		effective_prompt = f"{base} {user_prompt}"

		await self._cancel_prior_pending(channel_id)

		pending = {
			"channel_id": channel_id,
			"backend": backend_type,
			"prompt": effective_prompt,
			"project_path": str(project_path),
		}
		try:
			self._pending_path.write_text(json.dumps(pending), encoding="utf-8")
			proc = await asyncio.create_subprocess_exec(
				"schtasks", "/run", "/tn", _TASK_NAME,
				stdout=asyncio.subprocess.PIPE,
				stderr=asyncio.subprocess.PIPE,
			)
			stdout, stderr = await proc.communicate()
			if proc.returncode != 0:
				error_msg = stderr.decode().strip() or f"exit code {proc.returncode}"
				raise RuntimeError(error_msg)
		except Exception as exc:
			self._pending_path.unlink(missing_ok=True)
			await self._logger.spawn_failed(project_key, str(project_path), [_TASK_NAME], str(exc))
			await self._backend.send_text(f"Failed to spawn: {exc}.")
			return

		self._last_spawn_time = datetime.now(timezone.utc)
		self._registry.set_cwd_override(channel_id, True)
		await self._logger.away_mode_cwd_changed(channel_id, True)

		try:
			await self._backend.write_session_meta(
				channel_id, "single", project_key,
			)
		except Exception as exc:
			await self._logger.surface_error(f"single_meta_write_error: {exc}")

		spawn_id = secrets.token_hex(4)
		await self._logger.spawn_started(
			spawn_id, project_key, str(project_path),
			prompt if prompt is not None else "(ask on start)",
		)
		await self._backend.send_spawn_ack(channel_id, prompt)

	async def _handle_collab_spawn(
		self, project_path: Path, project_key: str, prompt: str | None, backends: list[str]
	) -> None:
		from server.collab import CollabSession
		task = prompt or _DEFAULT_COLLAB_PROMPT
		channel_id = canonicalize_cwd(str(project_path))

		await self._cancel_prior_pending(channel_id)

		# `--collab` defaults to claude+gemini, so agent_senders are distinct
		# by construction. Explicit `--claude --claude` is allowed but a
		# sharp edge — the duplicate-sender error path is documented in the
		# SKILL's BYO caveat for that case.
		agent_senders = [_get_backend_name(b) for b in backends]
		s1, s2 = agent_senders
		prompt_text = _COLLAB_INSTRUCTION.format(task=task)

		pending = {
			"channel_id": channel_id,
			"agents": [
				{
					"backend": backends[0],
					"sender": s1,
					"prompt": prompt_text,
					"project_path": str(project_path),
				},
				{
					"backend": backends[1],
					"sender": s2,
					"prompt": prompt_text,
					"project_path": str(project_path),
				},
			],
		}

		existing: list = []
		if self._sidecar_path.exists():
			try:
				existing = json.loads(self._sidecar_path.read_text(encoding="utf-8"))
			except Exception as exc:
				await self._logger.surface_error(f"collab_sidecar_read_error: {exc}")
		existing.append({
			"channel_id": channel_id,
			"agent_senders": agent_senders,
			"task": task,
			"created_at": datetime.now(timezone.utc).isoformat(),
		})
		self._sidecar_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")

		try:
			self._pending_path.write_text(json.dumps(pending), encoding="utf-8")
			proc = await asyncio.create_subprocess_exec(
				"schtasks", "/run", "/tn", _TASK_NAME,
				stdout=asyncio.subprocess.PIPE,
				stderr=asyncio.subprocess.PIPE,
			)
			stdout, stderr = await proc.communicate()
			if proc.returncode != 0:
				error_msg = stderr.decode().strip() or f"exit code {proc.returncode}"
				raise RuntimeError(error_msg)
		except Exception as exc:
			self._pending_path.unlink(missing_ok=True)
			# Roll back the sidecar entry we appended at line 397; without this,
			# a failed spawn leaves a phantom session record that survives until
			# the next service restart and triggers a "session was lost" notice
			# for a session that never existed.
			try:
				current = json.loads(self._sidecar_path.read_text(encoding="utf-8"))
				current = [e for e in current if e.get("channel_id") != channel_id]
				self._sidecar_path.write_text(json.dumps(current, indent=2), encoding="utf-8")
			except Exception as rb_exc:
				await self._logger.surface_error(f"collab_sidecar_rollback_error: {rb_exc}")
			await self._logger.spawn_failed(project_key, str(project_path), [_TASK_NAME], str(exc))
			await self._backend.send_text(f"Failed to spawn collab: {exc}.")
			return

		self._last_spawn_time = datetime.now(timezone.utc)
		self._registry.set_cwd_override(channel_id, True)
		await self._logger.away_mode_cwd_changed(channel_id, True)

		session = CollabSession(
			cwd=channel_id,
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
			await self._logger.surface_error(f"collab_meta_write_error: {exc}")

		try:
			await self._backend.start_inject_listener(channel_id)
		except Exception as exc:
			await self._logger.surface_error(f"collab_inject_listener_error: {exc}")

		spawn_id = secrets.token_hex(4)
		await self._logger.spawn_started(spawn_id, project_key, str(project_path), f"[collab] {task[:60]}")
		await self._backend.send_spawn_ack(channel_id, f"[collab] {task[:60]}")
