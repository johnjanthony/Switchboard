"""The approval card: how an open permission dialog reads on John's phone, and
how his reply becomes the dialog's answer. Pure: no I/O."""

from __future__ import annotations

import hashlib
import json
import re

APPROVAL_SUGGESTIONS = ["Allow", "Deny"]
DEFAULT_DENY_MESSAGE = "John denied this from his phone."

_ALLOW_REPLIES = frozenset({"allow", "yes", "y", "approve", "approved", "ok", "okay"})
_DENY_REPLIES = frozenset({"deny", "no", "n"})
_CUT_CHARS = 1500
_WRITE_PREVIEW_LINES = 20
_SUMMARY_CHARS = 80
_FOOTER = "Reply Allow or Deny, or tell Claude what to do instead."


def input_digest(tool_input: object) -> str:
	"""SHA-256 over a tool input's canonical JSON: the key that joins a hook's
	request to the mod's report of the same call."""
	canonical = json.dumps(tool_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
	return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def decide_reply(text: str) -> dict:
	"""The PermissionRequest decision for John's reply to a card. A few words
	allow and a few deny; anything else denies with his words as the reason the
	model reads. Never sets interrupt: the model carries on, and in away mode
	asks him what to do instead."""
	reply = (text or "").strip()
	word = reply.lower().rstrip(".!").strip()
	if word in _ALLOW_REPLIES:
		return {"behavior": "allow"}
	if not reply or word in _DENY_REPLIES:
		return {"behavior": "deny", "message": DEFAULT_DENY_MESSAGE}
	return {"behavior": "deny", "message": reply}


def hook_output(decision: dict) -> dict:
	"""The JSON a PermissionRequest command hook prints to answer its dialog."""
	return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}


def render_card(tool: str, tool_input: object, *, in_subagent: bool) -> tuple[str, str]:
	"""(title, text) for an approval card. The text's first line is plain, since
	the phone's notification shows only its first 100 characters."""
	who = f"{tool} in a subagent" if in_subagent else tool
	summary = _summary(tool, tool_input)
	first = f"Approve {who}: {summary}" if summary else f"Approve {who}"
	return f"Approve {tool}?", f"{first}\n\n{_details(tool, tool_input)}\n\n{_FOOTER}"


def _field(tool_input: object, key: str) -> str:
	value = tool_input.get(key) if isinstance(tool_input, dict) else None
	return value if isinstance(value, str) else ""


def _summary(tool: str, tool_input: object) -> str:
	if tool == "Bash":
		lines = _field(tool_input, "command").strip().splitlines()
		line = lines[0] if lines else ""
		return line if len(line) <= _SUMMARY_CHARS else line[: _SUMMARY_CHARS - 3] + "..."
	for key in ("file_path", "notebook_path", "url", "path"):
		value = _field(tool_input, key)
		if value:
			return value
	return ""


def _path_line(tool_input: object) -> str:
	"""The file path as a Markdown code span, since the plain first line is
	mangled by a Markdown renderer (underscores, backslashes)."""
	path = _field(tool_input, "file_path")
	return f"Path: {_code_span(path)}\n\n" if path else ""


def _details(tool: str, tool_input: object) -> str:
	if tool == "Bash":
		block = _fence(_cut(_field(tool_input, "command")), "sh")
		description = _field(tool_input, "description").strip()
		return f"{block}\n\n{description}" if description else block
	if tool == "Write":
		lines = _field(tool_input, "content").splitlines()
		shown = _fence(_cut("\n".join(lines[:_WRITE_PREVIEW_LINES])))
		more = len(lines) - _WRITE_PREVIEW_LINES
		tail = f"\n\n({more} more {_plural(more, 'line')})" if more > 0 else ""
		return f"{_path_line(tool_input)}{len(lines)} {_plural(len(lines), 'line')}:\n\n{shown}{tail}"
	if tool == "Edit":
		old = _fence(_cut(_field(tool_input, "old_string")))
		new = _fence(_cut(_field(tool_input, "new_string")))
		return f"{_path_line(tool_input)}Replace:\n\n{old}\n\nWith:\n\n{new}"
	rendered = json.dumps(tool_input, indent=2, ensure_ascii=False, default=str)
	return _fence(_cut(rendered), "json")


def _plural(count: int, noun: str) -> str:
	return noun if count == 1 else f"{noun}s"


def _cut(text: str) -> str:
	if len(text) <= _CUT_CHARS:
		return text
	return f"{text[:_CUT_CHARS]}\n... ({len(text) - _CUT_CHARS} more characters)"


def _fence(body: str, lang: str = "") -> str:
	"""A code block whose fence outlasts any backtick run inside it."""
	longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
	ticks = "`" * max(3, longest + 1)
	return f"{ticks}{lang}\n{body}\n{ticks}"


def _code_span(text: str) -> str:
	"""A Markdown code span whose delimiter outlasts any backtick run inside it."""
	longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
	ticks = "`" * (longest + 1)
	pad = " " if text.startswith("`") or text.endswith("`") else ""
	return f"{ticks}{pad}{text}{pad}{ticks}"
