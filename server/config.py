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


@dataclass(frozen=True)
class Config:
	host: str
	port: int
	timeout_seconds: int
	log_path: str
	enable_android: bool = False
	firebase_service_account_json: str | None = None
	firebase_database_url: str | None = None
	firebase_storage_bucket: str | None = None
	spawn_root: Path | None = None


def _require(name: str) -> str:
	value = os.environ.get(name)
	if not value:
		raise ConfigError(f"Missing required env var: {name}")
	return value


def load_config(dotenv_path: str | Path | None = None) -> Config:
	if dotenv_path is None:
		dotenv_path = Path.cwd() / ".env"
	dotenv_path = Path(dotenv_path)
	if dotenv_path.exists():
		load_dotenv(dotenv_path, override=False)

	spawn_root_raw = os.environ.get("SWITCHBOARD_SPAWN_ROOT")

	return Config(
		host=os.environ.get("SWITCHBOARD_HOST", "127.0.0.1"),
		port=int(os.environ.get("SWITCHBOARD_PORT", "9876")),
		timeout_seconds=int(
			os.environ.get("SWITCHBOARD_TIMEOUT_SECONDS", "86400")
		),
		log_path=os.environ.get(
			"SWITCHBOARD_LOG_PATH", "./logs/switchboard.jsonl"
		),
		enable_android=os.environ.get("SWITCHBOARD_ENABLE_ANDROID", "false").lower() == "true",
		firebase_service_account_json=os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON"),
		firebase_database_url=os.environ.get("FIREBASE_DATABASE_URL"),
		firebase_storage_bucket=os.environ.get("FIREBASE_STORAGE_BUCKET"),
		spawn_root=Path(spawn_root_raw) if spawn_root_raw else None,
	)
