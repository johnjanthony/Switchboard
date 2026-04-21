"""JSONL audit logger for Switchboard."""

from __future__ import annotations

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

	def _write(self, event: dict[str, Any]) -> None:
		event["ts"] = datetime.now(timezone.utc).isoformat()
		line = json.dumps(event, ensure_ascii=False)
		with self._path.open("a", encoding="utf-8") as fh:
			fh.write(line + "\n")
		_stderr_logger.info(line)

	def request_created(
		self, request_id: str, agent_id: str, question: str
	) -> None:
		self._write({
			"event": "request_created",
			"request_id": request_id,
			"agent_id": agent_id,
			"question_preview": _preview(question),
		})

	def request_resolved(
		self,
		request_id: str,
		agent_id: str,
		response_text: str,
		source: str,
		duration_ms: int,
	) -> None:
		self._write({
			"event": "request_resolved",
			"request_id": request_id,
			"agent_id": agent_id,
			"response_preview": _preview(response_text),
			"source": source,
			"duration_ms": duration_ms,
		})

	def notify_sent(self, agent_id: str, message: str) -> None:
		self._write({
			"event": "notify_sent",
			"agent_id": agent_id,
			"message_preview": _preview(message),
		})

	def timeout(
		self, request_id: str, agent_id: str, timeout_seconds: int
	) -> None:
		self._write({
			"event": "timeout",
			"request_id": request_id,
			"agent_id": agent_id,
			"timeout_seconds": timeout_seconds,
		})

	def tool_error(
		self, request_id: str | None, agent_id: str | None, error: str
	) -> None:
		self._write({
			"event": "tool_error",
			"request_id": request_id,
			"agent_id": agent_id,
			"error": error,
		})

	def surface_error(self, detail: str, correlation: str | None = None) -> None:
		self._write({
			"event": "surface_error",
			"detail": detail,
			"correlation": correlation,
		})

	def spawn_started(
		self,
		spawn_id: str,
		project_key: str,
		project_path: str,
		prompt_preview: str,
	) -> None:
		self._write({
			"event": "spawn_started",
			"spawn_id": spawn_id,
			"project_key": project_key,
			"project_path": project_path,
			"prompt_preview": prompt_preview,
		})

	def spawn_invalid_path(self, project_key: str, resolved_path: str) -> None:
		self._write({
			"event": "spawn_invalid_path",
			"project_key": project_key,
			"resolved_path": resolved_path,
		})

	def spawn_failed(
		self,
		project_key: str,
		project_path: str,
		argv: list[str],
		error: str,
	) -> None:
		self._write({
			"event": "spawn_failed",
			"project_key": project_key,
			"project_path": project_path,
			"argv": argv,
			"error": error,
		})

	def document_sent(
		self,
		agent_id: str,
		resolved_path: str,
		size_bytes: int,
		sha256_hex: str,
		caption: str | None,
	) -> None:
		event: dict = {
			"event": "document_sent",
			"agent_id": agent_id,
			"path": resolved_path,
			"size_bytes": size_bytes,
			"sha256": sha256_hex,
		}
		if caption is not None:
			event["caption_preview"] = _preview(caption)
		self._write(event)
