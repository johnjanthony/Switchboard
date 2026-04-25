"""Tracks last-delivered title per (cwd, partner) pair for collab prepend.

Used only on the partner-delivery side of message_and_await_agent — does
NOT affect Firebase writes (phone sees title via per-message subheader).
"""

from __future__ import annotations


def format_title_prepend(sender: str, title: str, message: str) -> str:
	return f'[{sender}\'s current session title: "{title}"]\n\n{message}'


class TitleTracker:
	def __init__(self) -> None:
		self._last_delivered: dict[tuple[str, str], str] = {}

	def maybe_prepend(
		self,
		cwd: str,
		sender: str,
		partner: str,
		title: str | None,
		message: str,
	) -> str:
		if not title:
			return message
		key = (cwd, partner)
		if self._last_delivered.get(key) == title:
			return message
		self._last_delivered[key] = title
		return format_title_prepend(sender, title, message)
