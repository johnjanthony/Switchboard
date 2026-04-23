"""Tests for env-based config loading."""

import os
from pathlib import Path

import pytest

from server.config import Config, ConfigError, load_config


def _clear_env(monkeypatch):
	for key in [
		"SWITCHBOARD_HOST",
		"SWITCHBOARD_PORT",
		"SWITCHBOARD_TIMEOUT_SECONDS",
		"SWITCHBOARD_LOG_PATH",
		"SWITCHBOARD_SPAWN_ROOT",
	]:
		monkeypatch.delenv(key, raising=False)


def test_loads_minimum_required_fields(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	cfg = load_config(dotenv_path=tmp_path / "does-not-exist.env")
	assert isinstance(cfg, Config)
	assert cfg.host == "127.0.0.1"
	assert cfg.port == 9876
	assert cfg.timeout_seconds == 86400


def test_overrides_via_env(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	monkeypatch.setenv("SWITCHBOARD_PORT", "9000")
	monkeypatch.setenv("SWITCHBOARD_TIMEOUT_SECONDS", "60")
	cfg = load_config(dotenv_path=tmp_path / "does-not-exist.env")
	assert cfg.port == 9000
	assert cfg.timeout_seconds == 60


def test_dotenv_used_when_env_unset(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	env_file = tmp_path / ".env"
	env_file.write_text(
		"SWITCHBOARD_PORT=9999\n"
	)
	cfg = load_config(dotenv_path=env_file)
	assert cfg.port == 9999


def test_os_env_wins_over_dotenv(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	monkeypatch.setenv("SWITCHBOARD_PORT", "8888")
	env_file = tmp_path / ".env"
	env_file.write_text(
		"SWITCHBOARD_PORT=7777\n"
	)
	cfg = load_config(dotenv_path=env_file)
	assert cfg.port == 8888


def test_spawn_root_defaults_to_none(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	cfg = load_config(dotenv_path=tmp_path / "no.env")
	assert cfg.spawn_root is None


def test_spawn_root_loaded_from_env(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	monkeypatch.setenv("SWITCHBOARD_SPAWN_ROOT", str(tmp_path))
	cfg = load_config(dotenv_path=tmp_path / "no.env")
	assert cfg.spawn_root == tmp_path
