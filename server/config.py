"""Env-based configuration for Switchboard.

OS env vars are the source of truth. A .env file is loaded as a fallback —
values already in the OS env win over .env (python-dotenv's default
`override=False` behavior).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
	pass


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def host_is_loopback(host: str) -> bool:
	return host.strip().lower() in _LOOPBACK_HOSTS


def require_token_for_nonloopback(host: str, auth_token: str | None) -> None:
	"""REV-003: a non-loopback bind exposes /mcp and every HTTP route to any
	host that can route to us, so the shared-secret gate is a hard startup
	prerequisite there. Fail closed rather than serve an open control surface."""
	if not host_is_loopback(host) and not auth_token:
		raise ConfigError(
			"SWITCHBOARD_HOST is non-loopback but SWITCHBOARD_TOKEN is unset. "
			"A non-loopback bind requires the shared-secret gate: set SWITCHBOARD_TOKEN "
			"(and configure WSL/remote clients with the same token) or bind to 127.0.0.1."
		)


@dataclass(frozen=True)
class Config:
	host: str
	port: int
	timeout_seconds: int
	log_path: str
	firebase_service_account_json: str | None = None
	firebase_database_url: str | None = None
	firebase_storage_bucket: str | None = None
	# Windows path that /spawn resolves projects under.
	# Env: SWITCHBOARD_WINDOWS_SPAWN_ROOT (preferred) or SWITCHBOARD_SPAWN_ROOT (back-compat alias).
	windows_spawn_root: Path | None = None
	rate_limit: int = 30
	auth_token: str | None = None
	route_rate_limit: int = 600
	# Segment appended to the WSL home path to locate the workspace root.
	# E.g. wsl_home_resolved="/home/john" + wsl_spawn_root_segment="work" → /home/john/work
	# Env: SWITCHBOARD_WSL_SPAWN_ROOT_SEGMENT, default "work".
	wsl_spawn_root_segment: str = "work"
	# Resolved WSL home path (e.g. "/home/john"). Populated at server startup
	# by main.py: first reads SWITCHBOARD_WSL_HOME env if set (escape hatch for
	# the NSSM Session 0 case where wsl.exe -e bash fails silently); otherwise
	# calls resolve_wsl_home() which spawns wsl.exe.
	wsl_home_resolved: str | None = None
	# Silent-past-this-many-seconds threshold for the session sweeper to mark a
	# session lost. Env: SWITCHBOARD_SESSION_LOST_AFTER_SECONDS, default 900 (15m).
	session_lost_after_seconds: int = 900
	# How long a terminal (ended/lost) session record survives before the sweeper
	# prunes it. Env: SWITCHBOARD_SESSION_RETENTION_HOURS, default 72.
	session_retention_hours: int = 72
	# How long an ENDED conversation (its index card plus /messages and
	# /answers nodes) survives before the conversation sweep deletes it.
	# Env: SWITCHBOARD_CONVERSATION_RETENTION_HOURS, default 72.
	conversation_retention_hours: int = 72


def load_config(dotenv_path: str | Path | None = None) -> Config:
	if dotenv_path is None:
		dotenv_path = Path.cwd() / ".env"
	dotenv_path = Path(dotenv_path)
	if dotenv_path.exists():
		load_dotenv(dotenv_path, override=False)

	# Accept the new env var name first; fall back to the legacy name for back-compat.
	windows_spawn_root_raw = (
		os.environ.get("SWITCHBOARD_WINDOWS_SPAWN_ROOT")
		or os.environ.get("SWITCHBOARD_SPAWN_ROOT")
	)

	cfg = Config(
		host=os.environ.get("SWITCHBOARD_HOST", "127.0.0.1"),
		port=int(os.environ.get("SWITCHBOARD_PORT", "9876")),
		timeout_seconds=int(
			os.environ.get("SWITCHBOARD_TIMEOUT_SECONDS", "86400")
		),
		log_path=os.environ.get(
			"SWITCHBOARD_LOG_PATH", "./logs/switchboard.jsonl"
		),
		firebase_service_account_json=os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON"),
		firebase_database_url=os.environ.get("FIREBASE_DATABASE_URL"),
		firebase_storage_bucket=os.environ.get("FIREBASE_STORAGE_BUCKET"),
		windows_spawn_root=Path(windows_spawn_root_raw) if windows_spawn_root_raw else None,
		rate_limit=int(os.environ.get("SWITCHBOARD_RATE_LIMIT", "30")),
		auth_token=os.environ.get("SWITCHBOARD_TOKEN") or None,
		route_rate_limit=int(os.environ.get("SWITCHBOARD_ROUTE_RATE_LIMIT", "600")),
		wsl_spawn_root_segment=os.environ.get("SWITCHBOARD_WSL_SPAWN_ROOT_SEGMENT", "work"),
		session_lost_after_seconds=int(os.environ.get("SWITCHBOARD_SESSION_LOST_AFTER_SECONDS", "900")),
		session_retention_hours=int(os.environ.get("SWITCHBOARD_SESSION_RETENTION_HOURS", "72")),
		conversation_retention_hours=int(os.environ.get("SWITCHBOARD_CONVERSATION_RETENTION_HOURS", "72")),
	)
	require_token_for_nonloopback(cfg.host, cfg.auth_token)
	return cfg
