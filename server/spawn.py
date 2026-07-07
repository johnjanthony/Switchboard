"""Claude Code session spawner."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from server.config import Config
from server.logging_jsonl import JsonlLogger
from server.messenger import ChannelLifecycle, MessageWriter, ConversationStore
from server.registry import Registry

_TASK_NAME = "SwitchboardSpawn"


def _validate_project(project: str) -> str | None:
	"""Return an error string if the phone-supplied project name is unsafe, else None.

	`project` is joined to the spawn root and handed to the launcher, so it must be a
	simple relative segment: no absolute paths (pathlib silently drops the root when
	the right operand is absolute, escaping the spawn root entirely), no drive letters,
	no leading separator, and no parent traversal. Checked against both path flavours
	because the same string is joined via pathlib on Windows and by string concat on WSL."""
	if not project or not project.strip():
		return "empty project"
	pw, pp = PureWindowsPath(project), PurePosixPath(project)
	if pw.is_absolute() or pp.is_absolute() or pw.drive or project[:1] in ("/", "\\"):
		return f"absolute project path rejected: {project!r}"
	if ".." in pw.parts or ".." in pp.parts:
		return f"parent traversal rejected: {project!r}"
	return None


async def invoke_spawn_launcher(logger) -> None:
	"""Trigger spawn-launcher.ps1 via the SwitchboardSpawn scheduled task.

	Module-level so non-SpawnHandler paths (combine's dormant-member relaunch
	in conversation_ops) can fire the launcher without a handler instance.
	logger may be None; launcher failure then raises instead of logging."""
	try:
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
		# Always propagate after logging: a launcher failure must reach the
		# caller so it can surface a phone-visible notice (B4). It previously
		# log-and-returned when a logger was present, silently leaving John in
		# away mode with a conversation no agent ever joined.
		if logger is not None:
			await logger.surface_error(f"spawn_invoke_launcher_failed: {exc}")
		raise


async def user_has_interactive_session() -> bool:
	"""Return True if any user has an interactive (Active or Disconnected)
	session on this host. Used as a precondition before any launcher
	invocation — the scheduled task that launches `wt` requires a desktop
	session to write into, and `schtasks /run` reports success even when no
	session exists, landing a Firebase channel with no agent behind it.

	Disconnected (`Disc`) sessions count: an agent spawned now becomes
	visible whenever the user reconnects (e.g. via RDP).

	Degrades open: if `quser` is missing or fails to launch, return True
	so the existing schtasks failure path stays the source of truth for
	Windows-toolchain problems."""
	try:
		proc = await asyncio.create_subprocess_exec(
			"quser",
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE,
		)
		stdout, _ = await proc.communicate()
	except Exception:
		return True

	if proc.returncode != 0:
		# quser exits non-zero when no users are logged on.
		return False

	text = stdout.decode("utf-8", errors="replace")
	# Header line first; data rows after. Token-scan is robust to the
	# column-shift that happens when SESSIONNAME is blank for Disc sessions.
	for line in text.splitlines()[1:]:
		tokens = line.split()
		if any(t in ("Active", "Disc") for t in tokens):
			return True
	return False


class _SpawnBackend(MessageWriter, ChannelLifecycle, ConversationStore):
	"""Backend surface used by SpawnHandler."""


class SpawnHandler:
	def __init__(
		self, config: Config, backend: _SpawnBackend, logger: JsonlLogger, registry: Registry
	) -> None:
		self._config = config
		self._spawn_root = config.windows_spawn_root
		self._pending_dir = Path(config.log_path).parent
		self._backend = backend
		self._logger = logger
		self._registry = registry

	async def _cancel_prior_pending(self, conversation_id: str) -> None:
		"""Cancel any pending ask_human requests left over for this conversation before launching
		a new agent. Without this, a prior agent that died without reaching its tool-handler
		cancellation path (common with MCP streamable-HTTP transport) leaves stale questions
		hanging on phone and server until the 24h timeout.

		After Phase 3 rename: takes conversation_id (not a filesystem cwd).

		Only cancels pending owned by a NON-live session: a live member's in-flight
		ask_human must survive a spawn into the same conversation (a phone
		'add into' or 'resume' while a peer is blocked would otherwise silently
		kill that peer's question).
		"""
		conv = self._registry.conversations.get(conversation_id)
		alive_session_ids = (
			{m.cli_session_id for m in conv.members_active.values() if m.alive}
			if conv else set()
		)
		cancelled = self._registry.cancel_stale_pending_for_conversation(conversation_id, alive_session_ids)
		if not cancelled:
			return
		for request_id in cancelled:
			try:
				await self._backend.mark_question_cancelled(conversation_id, request_id)
			except Exception as exc:
				await self._logger.surface_error(
					f"mark_cancelled_failed_on_spawn: conv={conversation_id} req={request_id} {exc}"
				)
		await self._logger.pending_cancelled_on_spawn(conversation_id, cancelled)

	# ------------------------------------------------------------------
	# Structured-command handlers (Tasks 25 & 26)
	# ------------------------------------------------------------------

	async def _invoke_launcher(self) -> None:
		"""Trigger spawn-launcher.ps1 via the SwitchboardSpawn scheduled task."""
		await invoke_spawn_launcher(self._logger)

	def _write_pending_file(self, payload: dict) -> Path:
		"""Write a spawn-pending JSON file for the launcher script to claim and consume.
		Shared by handle_fresh, handle_resume, and launch_resume_agent so there is
		exactly one place that defines the pending-file naming and serialization."""
		import uuid
		spawn_id = uuid.uuid4().hex
		pending_path = self._pending_dir / f"spawn-pending-{spawn_id}.json"
		pending_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
		return pending_path

	def _format_fresh_prompt(self, cmd: dict, conv, join_existing: bool) -> str:
		"""Build the initial prompt for a fresh-spawn agent.

		conv: the Conversation the agent is joining (whether newly minted or
		pre-existing). For the join-existing branch the prompt surfaces title,
		roster, and a short recent-message window so the agent has context
		before its first peer wakes it.
		"""
		base = (
			"John is currently away. All communications MUST go through the switchboard MCP. "
			"Tool calls auto-inject your cli_session_id; you don't need to provide it manually. "
		)
		if join_existing:
			roster_parts: list[str] = []
			for m in conv.members_active.values():
				status = "alive" if m.alive else "dormant"
				roster_parts.append(f"{m.sender} ({status})")
			roster = ", ".join(roster_parts) if roster_parts else "(none)"

			# Recent context: last few non-system messages so the joining agent
			# has grounding even if no alive peer is around to wake them via
			# enter_conversation.
			recent_msgs = [m for m in conv.messages if m.get("type") != "system"][-5:]
			recent_block = ""
			if recent_msgs:
				lines: list[str] = []
				for m in recent_msgs:
					text = (m.get("text", "") or "").strip().replace("\n", " ")
					if len(text) > 200:
						text = text[:200] + "…"
					lines.append(f"  [{m.get('sender', '?')}] {text}")
				recent_block = "\n\nRecent messages:\n" + "\n".join(lines)

			base += (
				f"You're joining the conversation \"{conv.title}\". "
				f"Current members: {roster}. "
				"Pick a short human-readable sender name distinct from those — surface labels "
				"(e.g. 'Claude Win', 'Claude WSL') or role labels (e.g. 'Reviewer', 'Implementer') "
				"both work. If you pick a name already in use, the server appends a numeric suffix "
				"(e.g. 'Claude Win 2'). "
				f"Call join_conversation(sender='<your_name>', ref='{conv.id}') as your first "
				"switchboard action to collect the recent log right away. After you have that "
				"context, introduce yourself via message_and_await_agent, which blocks until a "
				"peer speaks."
				+ recent_block
			)
		else:
			base += (
				"Pick a short human-readable sender name (e.g. 'Claude Win', 'Implementer'). "
				"Your first switchboard tool call registers you as a member of this conversation. "
			)
			user_prompt = cmd.get("prompt")
			if user_prompt:
				base += f"\n\nINITIAL TASK:\n{user_prompt}"
			else:
				base += "\n\nWait for John's first message via ask_human or notify_human."
		return base

	def _format_resume_prompt(self, cmd: dict, member, new_conv_id: str, solo: bool) -> str:
		"""Build the resume prompt for a returning agent.

		solo: True when this member is the only resumable member, so the
		continuation conversation has no other alive peer. A solo agent must
		come online via ask_human / notify_human; enter_conversation would park
		it in the wait queue waiting for a peer that will never speak (T-147).
		"""
		base = (
			f"You are resuming as '{member.sender}' in conversation '{new_conv_id}' "
			f"(continued from {cmd.get('source_conversation_id')}). "
			"Tool calls auto-inject your cli_session_id. "
		)
		if solo:
			base += (
				"You are the only agent in this conversation, so do NOT call enter_conversation "
				"(it would block in the wait queue waiting for a peer that will never speak). "
				"Come online to John directly: use ask_human to report your status and ask what's "
				"next, or notify_human for a non-blocking status update."
			)
		else:
			base += (
				f"Call join_conversation(sender='{member.sender}', ref='{new_conv_id}') to collect "
				"the conversation's new context: the recent history since your session ended."
			)
		user_prompt = cmd.get("prompt")
		if user_prompt:
			base += f"\n\nADDITIONAL CONTEXT FROM JOHN:\n{user_prompt}"
		return base

	async def handle_fresh(self, cmd: dict) -> None:
		"""Handle a 'fresh' spawn command from Firebase.

		cmd shape:
		{
			"type": "fresh",
			"surface": "windows" | "wsl",
			"project": "<project name>",  # relative to surface's spawn root
			"prompt": "<optional prompt text>" | None,
			"target_conversation_id": "<conv-id>" | None,  # if set, join existing conv
			"issued_at": "<ISO-8601>",
		}
		"""
		import uuid
		from server.registry import Conversation
		from server.conversation_ops import _now_iso

		surface = cmd.get("surface", "windows")
		project = cmd.get("project")
		if not project:
			await self._logger.surface_error("spawn_fresh: missing project")
			return
		if err := _validate_project(project):
			await self._logger.surface_error(f"spawn_fresh: {err}")
			return

		# Validate WSL availability if surface is wsl
		if surface == "wsl" and not getattr(self._config, "wsl_home_resolved", None):
			await self._logger.surface_error(
				"spawn_fresh: WSL spawn requested but WSL is not available on this host."
			)
			return

		if not await self._user_has_interactive_session():
			await self._backend.send_text(
				"Cannot spawn: no one is logged in to the desktop. Sign in (locally or via RDP) and try again."
			)
			return

		# Auto-enable away mode if currently off
		if not self._registry.global_away_mode:
			self._registry.global_away_mode = True
			try:
				if hasattr(self._backend, "set_global_away_mode"):
					await self._backend.set_global_away_mode(True)
			except Exception as exc:
				await self._logger.surface_error(f"spawn_fresh_away_mode_persist_failed: {exc}")

		# Resolve project path per surface
		if surface == "windows":
			if self._config.windows_spawn_root is None:
				await self._logger.surface_error("spawn_fresh: windows_spawn_root not configured")
				return
			root = Path(self._config.windows_spawn_root).resolve()
			candidate = (root / project).resolve()
			try:
				candidate.relative_to(root)
			except ValueError:
				await self._logger.surface_error(f"spawn_fresh: project escapes spawn root: {project!r}")
				return
			project_path = str(candidate)
		else:  # wsl
			segment = getattr(self._config, "wsl_spawn_root_segment", "work")
			wsl_home = getattr(self._config, "wsl_home_resolved", None)
			project_path = f"{wsl_home}/{segment}/{project}"

		# Determine conversation: join existing OR mint new
		target_conv_id = cmd.get("target_conversation_id")
		if target_conv_id:
			conv = self._registry.conversations.get(target_conv_id)
			if not conv or conv.state != "active":
				await self._logger.surface_error(
					f"spawn_fresh: target_conversation_id {target_conv_id} not Active"
				)
				return
			# Cancel any stale pending requests from a prior agent that died without cleanup
			await self._cancel_prior_pending(target_conv_id)
			conv_id = target_conv_id
			join_existing = True
		else:
			conv_id = "conv-" + uuid.uuid4().hex
			conv = Conversation(id=conv_id, title=f"{project} ({surface})")
			spawn_msg = {
				"seq": 0,
				"sender": "<system>",
				"type": "system",
				"text": f"Spawning Claude in {project} ({surface})",
				"timestamp": _now_iso(),
			}
			conv.messages.append(spawn_msg)
			conv.created_at = datetime.now(timezone.utc).timestamp()
			conv.last_activity_at = conv.created_at
			self._registry.conversations[conv_id] = conv
			join_existing = False

			# Firebase: write new conv meta + spawn message
			from server.gateway.bg_tasks import _spawn_bg as _sbg
			_sbg(
				self._backend.write_conversation_meta(
					conv_id,
					title=conv.title,
					state="active",
					continued_from=None,
					created_at=conv.created_at,
					last_activity_at=conv.last_activity_at,
					ended_at=None,
					hidden=False,
				),
				label=f"fb_write_conv_meta:{conv_id}",
			)
			_sbg(
				self._backend.write_conversation_message(conv_id, spawn_msg),
				label=f"fb_write_spawn_msg:{conv_id}",
			)

		# Pre-generate session_id, bind
		new_session_id = str(uuid.uuid4())
		self._registry.bind_session(new_session_id, conv_id)
		home_newly_set = new_session_id not in self._registry.session_home_conversation_id
		if home_newly_set:
			self._registry.set_session_home(new_session_id, conv_id)

		# Firebase: set session home
		if not join_existing:
			from server.gateway.bg_tasks import _spawn_bg as _sbg
			if home_newly_set:
				_sbg(
					self._backend.set_session_home(new_session_id, conv_id),
					label=f"fb_set_session_home:{new_session_id}:{conv_id}",
				)

		# Build prompt
		prompt = self._format_fresh_prompt(cmd, conv, join_existing=join_existing)

		# Write spawn-pending file
		pending = {
			"type": "fresh",
			"conversation_id": conv_id,
			"agents": [{
				"surface": surface,
				"cli_session_id": new_session_id,
				"prompt": prompt,
				"project_path": project_path,
				"join_existing": join_existing,
			}],
		}
		self._write_pending_file(pending)

		# Trigger launcher. On failure, tell John on the phone (consistent with
		# the quser gate) rather than silently leaving a conversation that no
		# agent ever joins while away mode is on (B4).
		try:
			await self._invoke_launcher()
		except Exception as exc:
			await self._backend.send_text(
				f"Spawn failed to launch '{project}' ({surface}): {exc}. "
				"No agent started; end the conversation from the phone and retry."
			)

	async def handle_resume(self, cmd: dict) -> None:
		"""Handle a 'resume' spawn command from Firebase.

		cmd shape:
		{
			"type": "resume",
			"source_conversation_id": "<conv-id>",
			"prompt": "<optional prompt>" | None,
			"issued_at": "<ISO-8601>",
		}
		"""
		import uuid

		source_id = cmd.get("source_conversation_id")
		if not source_id:
			await self._logger.surface_error("spawn_resume: missing source_conversation_id")
			return
		source = self._registry.conversations.get(source_id)
		if not source:
			await self._logger.surface_error(f"spawn_resume: source {source_id} not found")
			return

		if not await self._user_has_interactive_session():
			await self._backend.send_text(
				"Cannot resume: no one is logged in to the desktop. Sign in (locally or via RDP) and try again."
			)
			return

		# Auto-enable away mode if currently off
		if not self._registry.global_away_mode:
			self._registry.global_away_mode = True
			try:
				if hasattr(self._backend, "set_global_away_mode"):
					await self._backend.set_global_away_mode(True)
			except Exception as exc:
				await self._logger.surface_error(f"spawn_resume_away_mode_persist_failed: {exc}")

		# Cancel any stale pending requests in the source before minting the resume conversation
		await self._cancel_prior_pending(source_id)

		# Identify resumable members. Member state is the single source of
		# truth (decided 2026-06-11): resumable = dormant (not alive) and not
		# permanently lost. The binding map is derived routing state and must
		# agree (dormant = unbound); if it doesn't, log loudly and trust
		# member state: a stale binding must not silently disable phone
		# Resume (the post-restart hydration trap, H03/M21).
		resumable = []
		for m in source.members_active.values():
			if m.alive or m.session_lost_permanently:
				continue
			bound_to = self._registry.session_to_conversation_id.get(m.cli_session_id)
			if bound_to is not None:
				await self._logger.surface_error(
					f"resume_eligibility_drift: dormant member '{m.sender}' ({m.cli_session_id}) "
					f"unexpectedly bound to {bound_to}; resuming anyway (member state wins)"
				)
			resumable.append(m)
		if not resumable:
			await self._logger.surface_error(f"spawn_resume: no resumable members in source {source_id}")
			return

		# Mint new conversation with continued_from
		from server.registry import Conversation
		from server.conversation_ops import _now_iso
		from server.gateway.bg_tasks import _spawn_bg as _sbg
		new_id = "conv-" + uuid.uuid4().hex
		new_conv = Conversation(id=new_id, title=source.title, continued_from=source_id)
		resume_msg = {
			"seq": 0,
			"sender": "<system>",
			"type": "system",
			"text": f"Resuming '{source.title}' (continued from {source_id}).",
			"timestamp": _now_iso(),
		}
		new_conv.messages.append(resume_msg)
		new_conv.created_at = datetime.now(timezone.utc).timestamp()
		new_conv.last_activity_at = new_conv.created_at
		self._registry.conversations[new_id] = new_conv

		# Firebase: write new conv meta + resume system message
		_sbg(
			self._backend.write_conversation_meta(
				new_id,
				title=new_conv.title,
				state="active",
				continued_from=source_id,
				created_at=new_conv.created_at,
				last_activity_at=new_conv.last_activity_at,
				ended_at=None,
				hidden=False,
			),
			label=f"fb_write_conv_meta:{new_id}",
		)
		_sbg(
			self._backend.write_conversation_message(new_id, resume_msg),
			label=f"fb_write_resume_msg:{new_id}",
		)

		# Pre-bind each resumable session, move member entry to new conv.
		# Flip alive=True (and clear dormancy fields) so message_and_await_agent's
		# alive-peer count includes these resumed members. Without this, a two-agent
		# resume yields __CONVERSATION_EMPTY__ on the first speak attempt.
		# A solo resume (one resumable member) has no alive peer to wake an
		# enter_conversation wait, so the prompt must direct it to ask_human /
		# notify_human instead (T-147).
		solo_resume = len(resumable) == 1
		agents = []
		for m in resumable:
			m.alive = True
			m.session_ended_at = None
			m.session_end_reason = None
			m.left_at = None
			# Reset so the resumed member sees the continuation conversation from
			# its start (the resume system message at seq 0) rather than slicing
			# past the end of a fresh message list with a stale source-conv
			# last_seen_seq (T-149). Mirrors _perform_combine's combine-resume reset.
			m.last_seen_seq = 0
			self._registry.bind_session(m.cli_session_id, new_id)
			new_conv.members_active[m.cli_session_id] = m
			del source.members_active[m.cli_session_id]
			agents.append({
				"surface": m.surface,
				"cli_session_id": m.cli_session_id,
				"prompt": self._format_resume_prompt(cmd, m, new_id, solo_resume),
				"project_path": m.cwd,
				"prior_sender": m.sender,
			})
			# Firebase: move member from source to new conv
			_sbg(
				self._backend.remove_conversation_member(source_id, m.sender),
				label=f"fb_remove_member:{source_id}:{m.sender}",
			)
			_sbg(
				self._backend.write_conversation_member(new_id, m),
				label=f"fb_write_member:{new_id}:{m.sender}",
			)

		# If source has no remaining members (all were resumable), end it
		source_ended = False
		open_cleared = False
		if not source.members_active:
			source.state = "ended"
			source.ended_at = datetime.now(timezone.utc).timestamp()
			source_ended = True
			if self._registry.open_conversation_id == source_id:
				self._registry.open_conversation_id = None
				open_cleared = True
		if source_ended:
			_sbg(
				self._backend.set_conversation_state(source_id, "ended"),
				label=f"fb_set_state:{source_id}:ended",
			)
		if open_cleared:
			# Persist the open-pointer clear so the phone's open badge does not
			# stick on the Ended source until restart (F-69(g)). Mirrors the
			# set_open_conversation_id(None) calls in handlers.py and dispatch.py.
			_sbg(
				self._backend.set_open_conversation_id(None),
				label=f"fb_clear_open_id:{source_id}",
			)

		# Write spawn-pending file (type "resume")
		pending = {
			"type": "resume",
			"conversation_id": new_id,
			"continued_from": source_id,
			"agents": agents,
		}
		self._write_pending_file(pending)
		# On launcher failure, surface a phone-visible notice rather than leaving
		# the resumed members marked alive with no process behind them (B4).
		try:
			await self._invoke_launcher()
		except Exception as exc:
			await self._backend.send_text(
				f"Resume failed to launch (continued from {source_id}): {exc}. "
				"No agent started; end the conversation from the phone and retry."
			)

	async def launch_resume_agent(
		self, *, session_id: str, surface: str, cwd: str, prompt: str, prior_sender: str | None
	) -> bool:
		"""Queue one claude --resume launch. No away-mode side effect - the caller
		owns that policy (resume paths enable it; convene never does)."""
		if not await self._user_has_interactive_session():
			return False
		pending = {
			"type": "resume_session",
			"agents": [{
				"surface": surface,
				"cli_session_id": session_id,
				"prompt": prompt,
				"project_path": cwd,
				"prior_sender": prior_sender,
			}],
		}
		self._write_pending_file(pending)
		try:
			await self._invoke_launcher()
		except Exception as exc:
			await self._logger.surface_error(f"resume_session_launch_failed: {exc}")
			return False
		return True

	async def handle_resume_session(self, cmd: dict) -> None:
		"""Resume a registry session (board resume): standalone, or into an existing
		conversation. Session ids are stable across --resume (verified 2026-07-07),
		so membership is pre-added at spawn time under the same id."""
		session_id = cmd.get("session_id")
		if not isinstance(session_id, str) or not session_id:
			await self._logger.surface_error(f"resume_session: missing session_id: {cmd}")
			return
		sessions = getattr(self._registry, "sessions", None)
		rec = sessions.get(session_id) if sessions is not None else None
		if rec is None:
			await self._backend.send_text(f"Cannot resume {session_id[:8]}: session not found in the registry (pruned?).")
			return
		if rec.state not in ("ended", "lost"):
			await self._backend.send_text(f"Cannot resume {session_id[:8]}: the session is still live ({rec.state}).")
			return
		if not rec.cwd:
			await self._backend.send_text(f"Cannot resume {session_id[:8]}: no working directory recorded.")
			return

		target_id = cmd.get("target_conversation_id")
		target = None
		if isinstance(target_id, str) and target_id:
			target = self._registry.conversations.get(target_id)
			if target is None or target.state != "active":
				await self._backend.send_text(f"Cannot resume into {target_id}: conversation not found or not Active.")
				return

		if not await self._user_has_interactive_session():
			await self._backend.send_text(
				"Cannot resume: no one is logged in to the desktop. Sign in (locally or via RDP) and try again."
			)
			return

		# Auto-enable away mode if currently off
		if not self._registry.global_away_mode:
			self._registry.global_away_mode = True
			try:
				if hasattr(self._backend, "set_global_away_mode"):
					await self._backend.set_global_away_mode(True)
			except Exception as exc:
				await self._logger.surface_error(f"resume_session_away_mode_persist_failed: {exc}")

		from server.conversation_ops import _add_member, _convene_sender_for, _now_iso
		from server.gateway.bg_tasks import _spawn_bg as _sbg
		sender = _convene_sender_for(self._registry, sessions, session_id)
		other_alive = 0
		if target is not None:
			async with target.lock:
				if session_id not in target.members_active:
					await _add_member(self._registry, target.id, session_id, sender, rec.cwd, backend=self._backend)
			sender = target.members_active[session_id].sender
			other_alive = sum(1 for k, m in target.members_active.items() if k != session_id and m.alive)
			msg = {
				"seq": len(target.messages),
				"sender": "<system>",
				"type": "system",
				"text": f"John resumed {sender} into this conversation.",
				"timestamp": _now_iso(),
			}
			target.messages.append(msg)
			if self._backend is not None:
				_sbg(self._backend.write_conversation_message(target.id, msg), label=f"fb_resume_msg:{target.id}")

		if sessions is not None:
			sessions.note_spawn_resume(session_id, rec.cwd)
		prompt = self._format_resume_session_prompt(cmd, rec, sender, target, other_alive)
		ok = await self.launch_resume_agent(
			session_id=session_id, surface=rec.surface, cwd=rec.cwd, prompt=prompt, prior_sender=sender
		)
		if not ok:
			await self._backend.send_text(f"Resume of {sender} did not launch (no desktop session or launcher failure).")

	def _format_resume_session_prompt(self, cmd: dict, rec, sender: str, target, other_alive: int) -> str:
		if target is None:
			base = (
				f"You are resuming your previous session in {rec.cwd}. "
				"Tool calls auto-inject your cli_session_id. Come online to John directly: "
				"use ask_human to report your status and ask what's next, or notify_human for a "
				"non-blocking status update."
			)
		elif other_alive == 0:
			base = (
				f"You are '{sender}', resumed into conversation '{target.id}'. "
				"Tool calls auto-inject your cli_session_id. You are currently the only alive agent "
				"there, so do NOT block in message_and_await_agent - come online via ask_human or notify_human."
			)
		else:
			base = (
				f"You are '{sender}', resumed into conversation '{target.id}'. "
				"Tool calls auto-inject your cli_session_id. "
				f"Call join_conversation(sender='{sender}', ref='{target.id}') to collect the history, "
				"then message_and_await_agent to speak."
			)
		user_prompt = cmd.get("prompt")
		if user_prompt:
			base += f"\n\nADDITIONAL CONTEXT FROM JOHN:\n{user_prompt}"
		return base

	async def _user_has_interactive_session(self) -> bool:
		"""See module-level user_has_interactive_session(). Kept as a method so
		existing tests and call sites can patch per-handler."""
		return await user_has_interactive_session()

