"""Shared helpers for the switchboard hook scripts.

Each hook script stays a standalone executable (Claude Code runs them as
`python <script>.py`); this module rides alongside them in the plugin's
scripts/ dir and is imported as a sibling (under direct-script invocation
the script's own directory is sys.path[0], and the plugin cache ships the
whole scripts/ dir atomically per version).

Why bytes-first stdin: reading via json.load(sys.stdin) decodes through the
TextIOWrapper, which on Windows defaults to cp1252 + errors='surrogateescape'
and mangles UTF-8 multi-byte sequences (em-dash, emoji) into mojibake. Raw
bytes + json.loads sidesteps the wrapper entirely.
"""
from __future__ import annotations
import json
import os
import sys

DEFAULT_BASE_URL = "http://127.0.0.1:9876"


def read_stdin_json() -> dict:
	"""Best-effort payload read: raw stdin bytes -> UTF-8 JSON object. Returns
	{} on any failure (empty stdin, malformed JSON, non-dict payload) so
	callers keep their fail-open discipline."""
	try:
		raw = sys.stdin.buffer.read()
	except Exception:
		return {}
	try:
		payload = json.loads(raw) if raw else {}
	except Exception:
		return {}
	return payload if isinstance(payload, dict) else {}


def base_url() -> str:
	"""The switchboard gateway base URL (no trailing slash)."""
	return os.environ.get("SWITCHBOARD_BASE_URL", DEFAULT_BASE_URL)


def auth_headers() -> dict:
	"""Authorization header when SWITCHBOARD_TOKEN is set (WSL / non-loopback
	clients); empty dict otherwise (loopback peers are exempt server-side)."""
	token = os.environ.get("SWITCHBOARD_TOKEN")
	return {"Authorization": f"Bearer {token}"} if token else {}
