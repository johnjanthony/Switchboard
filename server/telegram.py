"""Telegram MessengerBackend implementation using raw httpx."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

import httpx

from server.logging_jsonl import JsonlLogger
from server.messenger import CorrelationToken, IncomingResponse, MessengerBackend


class TelegramError(RuntimeError):
	"""Raised by TelegramBackend when a Telegram API call fails.

	The message has been sanitized to remove the bot token. Callers
	can log `str(exc)` without leaking credentials.
	"""


def _redact_token(text: str, token: str) -> str:
	if not token:
		return text
	return text.replace(f"bot{token}", "bot<REDACTED>")


def _html_escape(text: str) -> str:
	return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class TelegramBackend(MessengerBackend):
	BASE_URL = "https://api.telegram.org"

	def __init__(
		self,
		token: str,
		chat_id: str,
		http_client: httpx.AsyncClient | None = None,
		logger: JsonlLogger | None = None,
	) -> None:
		self._token = token
		self._chat_id = chat_id
		self._client = http_client or httpx.AsyncClient(
			timeout=httpx.Timeout(35.0)
		)
		self._owns_client = http_client is None
		self._offset: int | None = None
		self._logger = logger
		self._command_queue: asyncio.Queue[str] = asyncio.Queue()

	@property
	def _base(self) -> str:
		return f"{self.BASE_URL}/bot{self._token}"

	async def aclose(self) -> None:
		if self._owns_client:
			await self._client.aclose()

	def _sanitize(self, exc: BaseException) -> str:
		return _redact_token(str(exc), self._token)

	async def _post_send_message(self, payload: dict) -> dict:
		payload = {"chat_id": self._chat_id, **payload}
		try:
			resp = await self._client.post(
				f"{self._base}/sendMessage", json=payload
			)
			resp.raise_for_status()
			return resp.json()["result"]
		except httpx.HTTPError as exc:
			raise TelegramError(self._sanitize(exc)) from None

	async def preflight(self) -> None:
		"""Verify the bot token by calling getMe. Raises TelegramError on failure (sanitized)."""
		try:
			resp = await self._client.get(f"{self._base}/getMe")
			resp.raise_for_status()
		except httpx.HTTPError as exc:
			raise TelegramError(self._sanitize(exc)) from None

	async def send_question(
		self, request_id: str, agent_id: str, question: str, format: str = "plain"
	) -> CorrelationToken:
		if format == "html":
			text = (
				f"[{_html_escape(agent_id)} | {_html_escape(request_id)}] {question}\n\n"
				"Reply to this message to answer."
			)
			payload: dict = {"text": text, "reply_markup": {"force_reply": True}, "parse_mode": "HTML"}
		else:
			text = (
				f"[{agent_id} | {request_id}] {question}\n\n"
				"Reply to this message to answer."
			)
			payload = {"text": text, "reply_markup": {"force_reply": True}}
		result = await self._post_send_message(payload)
		return int(result["message_id"])

	async def send_notification(self, agent_id: str, message: str, format: str = "plain") -> None:
		if format == "html":
			text = f"ℹ️ [{_html_escape(agent_id)}] {message}"
			await self._post_send_message({"text": text, "parse_mode": "HTML"})
		else:
			text = f"ℹ️ [{agent_id}] {message}"
			await self._post_send_message({"text": text})

	async def send_timeout_followup(
		self,
		request_id: str,
		agent_id: str,
		timeout_seconds: int,
		correlation: CorrelationToken,
	) -> None:
		hours = max(1, timeout_seconds // 3600)
		text = (
			f"⏱️ [{agent_id} | {request_id}] timed out after {hours}h. "
			"Agent received timeout signal."
		)
		await self._post_send_message({
			"text": text,
			"reply_to_message_id": int(correlation),
		})

	async def send_resolution_confirmation(
		self,
		request_id: str,
		agent_id: str,
		correlation: CorrelationToken,
	) -> None:
		text = f"✅ [{agent_id} | {request_id}] answered"
		await self._post_send_message({
			"text": text,
			"reply_to_message_id": int(correlation),
		})

	async def poll_responses(self) -> AsyncIterator[IncomingResponse]:
		while True:
			try:
				params: dict[str, int | str] = {"timeout": 30}
				if self._offset is not None:
					params["offset"] = self._offset
				resp = await self._client.get(
					f"{self._base}/getUpdates",
					params=params,
					timeout=35.0,
				)
				if resp.status_code == 429:
					try:
						retry_after = int(
							resp.json()
							.get("parameters", {})
							.get("retry_after", 2)
						)
					except (ValueError, KeyError):
						retry_after = 2
					retry_after = max(1, min(retry_after, 300))
					if self._logger is not None:
						self._logger.surface_error(
							f"telegram_rate_limited: retry_after={retry_after}s"
						)
					await asyncio.sleep(retry_after)
					continue
				resp.raise_for_status()
				data = resp.json()
				for update in data.get("result", []):
					self._offset = int(update["update_id"]) + 1
					msg = update.get("message")
					if not msg:
						continue
					text_val = msg.get("text", "")
					if text_val and (text_val == "/spawn" or text_val.startswith("/spawn ")):
						if str(msg.get("chat", {}).get("id")) == self._chat_id:
							await self._command_queue.put(text_val)
						continue
					reply = msg.get("reply_to_message")
					if not reply:
						continue
					yield IncomingResponse(
						correlation=int(reply["message_id"]),
						text=text_val,
					)
			except asyncio.CancelledError:
				raise
			except (httpx.HTTPError, KeyError, ValueError) as exc:
				if self._logger is not None:
					self._logger.surface_error(
						f"telegram_poll_error: {self._sanitize(exc)}"
					)
				await asyncio.sleep(2.0)

	async def poll_commands(self) -> AsyncIterator[str]:
		while True:
			yield await self._command_queue.get()

	async def send_spawn_ack(
		self, project_key: str, prompt_preview: str | None
	) -> None:
		if prompt_preview is None:
			text = (
				f"Spawning {project_key} \u2014 agent will ask what to work on. "
				"Check Windows Terminal."
			)
		else:
			truncated = (
				(prompt_preview[:47] + "...") if len(prompt_preview) > 47 else prompt_preview
			)
			text = f"Spawning {project_key} with task '{truncated}'. Check Windows Terminal."
		await self._post_send_message({"text": text})

	async def send_document(
		self, agent_id: str, path: Path, caption: str | None
	) -> None:
		data: dict = {"chat_id": self._chat_id}
		if caption is not None:
			data["caption"] = caption
		try:
			resp = await self._client.post(
				f"{self._base}/sendDocument",
				data=data,
				files={"document": (path.name, path.read_bytes(), "application/octet-stream")},
			)
			resp.raise_for_status()
		except httpx.HTTPError as exc:
			raise TelegramError(self._sanitize(exc)) from None

	async def send_text(self, text: str) -> None:
		await self._post_send_message({"text": text})
