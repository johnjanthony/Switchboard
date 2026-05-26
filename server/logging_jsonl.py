"""JSONL audit logger for Switchboard."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_stderr_logger = logging.getLogger("switchboard")
if not _stderr_logger.handlers:
	handler = logging.StreamHandler()
	handler.setFormatter(
		logging.Formatter("%(asctime)s %(levelname)s %(message)s")
	)
	_stderr_logger.addHandler(handler)
	_stderr_logger.setLevel(logging.INFO)


def _preview(text: str, limit: int = 100) -> str:
	if len(text) <= limit:
		return text
	return text[:limit]


class JsonlLogger:
	def __init__(self, path: str | Path) -> None:
		self._path = Path(path)
		self._path.parent.mkdir(parents=True, exist_ok=True)
		self._lock = asyncio.Lock()

	@property
	def log_path(self) -> str:
		return str(self._path)

	async def _write(self, event: dict[str, Any]) -> None:
		event["ts"] = datetime.now(timezone.utc).isoformat()
		line = json.dumps(event, ensure_ascii=False)

		def _do_write():
			with self._path.open("a", encoding="utf-8") as fh:
				fh.write(line + "\n")

		async with self._lock:
			await asyncio.to_thread(_do_write)
			_stderr_logger.info(line)

	async def request_created(
		self, request_id: str, conversation_id: str, question: str
	) -> None:
		await self._write({
			"event": "request_created",
			"request_id": request_id,
			"conversation_id": conversation_id,
			"question_preview": _preview(question),
		})

	async def request_resolved(
		self,
		request_id: str,
		conversation_id: str,
		response_text: str,
		source: str,
		duration_ms: int,
	) -> None:
		await self._write({
			"event": "request_resolved",
			"request_id": request_id,
			"conversation_id": conversation_id,
			"response_preview": _preview(response_text),
			"source": source,
			"duration_ms": duration_ms,
		})

	async def notify_sent(self, conversation_id: str, message: str) -> None:
		await self._write({
			"event": "notify_sent",
			"conversation_id": conversation_id,
			"message_preview": _preview(message),
		})

	async def timeout(
		self, request_id: str, conversation_id: str, timeout_seconds: int
	) -> None:
		await self._write({
			"event": "timeout",
			"request_id": request_id,
			"conversation_id": conversation_id,
			"timeout_seconds": timeout_seconds,
		})

	async def tool_error(
		self, request_id: str | None, conversation_id: str | None, error: str
	) -> None:
		await self._write({
			"event": "tool_error",
			"request_id": request_id,
			"conversation_id": conversation_id,
			"error": error,
		})

	async def surface_error(self, detail: str, correlation: str | None = None) -> None:
		await self._write({
			"event": "surface_error",
			"detail": detail,
			"correlation": correlation,
		})

	async def info(self, detail: str) -> None:
		await self._write({
			"event": "info",
			"detail": detail,
		})

	async def spawn_started(
		self,
		spawn_id: str,
		project_key: str,
		project_path: str,
		prompt_preview: str,
	) -> None:
		await self._write({
			"event": "spawn_started",
			"spawn_id": spawn_id,
			"project_key": project_key,
			"project_path": project_path,
			"prompt_preview": prompt_preview,
		})

	async def spawn_invalid_path(self, project_key: str, resolved_path: str) -> None:
		await self._write({
			"event": "spawn_invalid_path",
			"project_key": project_key,
			"resolved_path": resolved_path,
		})

	async def spawn_failed(
		self,
		project_key: str,
		project_path: str,
		argv: list[str],
		error: str,
	) -> None:
		await self._write({
			"event": "spawn_failed",
			"project_key": project_key,
			"project_path": project_path,
			"argv": argv,
			"error": error,
		})

	async def collab_message_sent(self, conversation_id: str, sender: str, message: str) -> None:
		await self._write({
			"event": "collab_message_sent",
			"conversation_id": conversation_id,
			"sender": sender,
			"message_preview": _preview(message),
		})

	async def collab_message_received(self, conversation_id: str, sender: str, result: str) -> None:
		await self._write({
			"event": "collab_message_received",
			"conversation_id": conversation_id,
			"sender": sender,
			"response_preview": _preview(result),
		})

	async def document_sent(
		self,
		conversation_id: str,
		resolved_path: str,
		size_bytes: int,
		sha256_hex: str,
		caption: str | None,
	) -> None:
		event: dict = {
			"event": "document_sent",
			"conversation_id": conversation_id,
			"path": resolved_path,
			"size_bytes": size_bytes,
			"sha256": sha256_hex,
		}
		if caption is not None:
			event["caption_preview"] = _preview(caption)
		await self._write(event)

	async def rate_limited(self, conversation_id: str, tool: str) -> None:
		await self._write({
			"event": "rate_limited",
			"conversation_id": conversation_id,
			"tool": tool,
		})

	async def away_mode_entered(self, reason: str | None = None) -> None:
		event: dict[str, Any] = {"event": "away_mode_entered"}
		if reason is not None:
			event["reason"] = reason
		await self._write(event)

	async def away_mode_exited(self, reason: str | None = None) -> None:
		event: dict[str, Any] = {"event": "away_mode_exited"}
		if reason is not None:
			event["reason"] = reason
		await self._write(event)

	async def cwd_canonicalized(self, raw: str, canonical: str) -> None:
		await self._write({"event": "cwd_canonicalized", "raw": raw, "canonical": canonical})

	async def pending_superseded(self, cwd: str, sender: str, prior_request_id: str, new_request_id: str) -> None:
		await self._write({
			"event": "pending_superseded", "cwd": cwd, "sender": sender,
			"prior_request_id": prior_request_id, "new_request_id": new_request_id,
		})

	async def away_mode_global_changed(self, active: bool) -> None:
		await self._write({"event": "away_mode_global_changed", "active": active})

	async def away_mode_cwd_changed(self, cwd: str, active: bool) -> None:
		await self._write({"event": "away_mode_cwd_changed", "cwd": cwd, "active": active})

	async def pending_cancelled_on_spawn(self, cwd: str, request_ids: list[str]) -> None:
		await self._write({
			"event": "pending_cancelled_on_spawn",
			"cwd": cwd,
			"request_ids": request_ids,
			"count": len(request_ids),
		})

	async def title_truncated(self, cwd: str, original_length: int, truncated: str) -> None:
		await self._write({
			"event": "title_truncated", "cwd": cwd,
			"original_length": original_length, "truncated": truncated,
		})
