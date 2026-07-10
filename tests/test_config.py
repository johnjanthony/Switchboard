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
		"SWITCHBOARD_WINDOWS_SPAWN_ROOT",
		"SWITCHBOARD_WSL_SPAWN_ROOT_SEGMENT",
		"SWITCHBOARD_RATE_LIMIT",
		"SWITCHBOARD_SESSION_LOST_AFTER_SECONDS",
		"SWITCHBOARD_SESSION_RETENTION_HOURS",
		"SWITCHBOARD_TOKEN",
		"SWITCHBOARD_ROUTE_RATE_LIMIT",
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


def test_windows_spawn_root_defaults_to_none(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	cfg = load_config(dotenv_path=tmp_path / "no.env")
	assert cfg.windows_spawn_root is None


def test_windows_spawn_root_loaded_from_new_env_var(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	monkeypatch.setenv("SWITCHBOARD_WINDOWS_SPAWN_ROOT", str(tmp_path))
	cfg = load_config(dotenv_path=tmp_path / "no.env")
	assert cfg.windows_spawn_root == tmp_path


def test_windows_spawn_root_backcompat_legacy_env_var(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	monkeypatch.setenv("SWITCHBOARD_SPAWN_ROOT", str(tmp_path))
	cfg = load_config(dotenv_path=tmp_path / "no.env")
	assert cfg.windows_spawn_root == tmp_path


def test_windows_spawn_root_new_env_wins_over_legacy(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	new_path = tmp_path / "new"
	new_path.mkdir()
	old_path = tmp_path / "old"
	old_path.mkdir()
	monkeypatch.setenv("SWITCHBOARD_WINDOWS_SPAWN_ROOT", str(new_path))
	monkeypatch.setenv("SWITCHBOARD_SPAWN_ROOT", str(old_path))
	cfg = load_config(dotenv_path=tmp_path / "no.env")
	assert cfg.windows_spawn_root == new_path


def test_wsl_spawn_root_segment_default(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	cfg = load_config(dotenv_path=tmp_path / "no.env")
	assert cfg.wsl_spawn_root_segment == "work"


def test_wsl_spawn_root_segment_from_env(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	monkeypatch.setenv("SWITCHBOARD_WSL_SPAWN_ROOT_SEGMENT", "code")
	cfg = load_config(dotenv_path=tmp_path / "no.env")
	assert cfg.wsl_spawn_root_segment == "code"


def test_wsl_home_resolved_defaults_to_none(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	cfg = load_config(dotenv_path=tmp_path / "no.env")
	assert cfg.wsl_home_resolved is None


def test_rate_limit_defaults_to_30(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	monkeypatch.delenv("SWITCHBOARD_RATE_LIMIT", raising=False)
	cfg = load_config(dotenv_path=tmp_path / "no.env")
	assert cfg.rate_limit == 30


def test_rate_limit_zero_disables_limiting(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	monkeypatch.setenv("SWITCHBOARD_RATE_LIMIT", "0")
	cfg = load_config(dotenv_path=tmp_path / "no.env")
	assert cfg.rate_limit == 0


def test_rate_limit_configurable_via_env(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	monkeypatch.setenv("SWITCHBOARD_RATE_LIMIT", "60")
	cfg = load_config(dotenv_path=tmp_path / "no.env")
	assert cfg.rate_limit == 60


def test_session_lost_after_seconds_defaults_to_900(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	cfg = load_config(dotenv_path=tmp_path / "no.env")
	assert cfg.session_lost_after_seconds == 900


def test_session_lost_after_seconds_configurable_via_env(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	monkeypatch.setenv("SWITCHBOARD_SESSION_LOST_AFTER_SECONDS", "120")
	cfg = load_config(dotenv_path=tmp_path / "no.env")
	assert cfg.session_lost_after_seconds == 120


def test_session_retention_hours_defaults_to_72(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	cfg = load_config(dotenv_path=tmp_path / "no.env")
	assert cfg.session_retention_hours == 72


def test_session_retention_hours_configurable_via_env(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	monkeypatch.setenv("SWITCHBOARD_SESSION_RETENTION_HOURS", "24")
	cfg = load_config(dotenv_path=tmp_path / "no.env")
	assert cfg.session_retention_hours == 24


def test_auth_token_default_none(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	cfg = load_config(dotenv_path=tmp_path / "does-not-exist.env")
	assert cfg.auth_token is None


def test_auth_token_via_env(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	monkeypatch.setenv("SWITCHBOARD_TOKEN", "sekrit-abc")
	cfg = load_config(dotenv_path=tmp_path / "does-not-exist.env")
	assert cfg.auth_token == "sekrit-abc"


def test_route_rate_limit_default(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	cfg = load_config(dotenv_path=tmp_path / "does-not-exist.env")
	assert cfg.route_rate_limit == 600


def test_route_rate_limit_via_env(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	monkeypatch.setenv("SWITCHBOARD_ROUTE_RATE_LIMIT", "0")
	cfg = load_config(dotenv_path=tmp_path / "does-not-exist.env")
	assert cfg.route_rate_limit == 0


def test_nonloopback_host_without_token_raises(monkeypatch, tmp_path):
	# REV-003: a non-loopback bind exposes /mcp and the HTTP routes to the
	# network - the shared-secret gate is a hard prerequisite there.
	from server.config import ConfigError
	_clear_env(monkeypatch)
	monkeypatch.setenv("SWITCHBOARD_HOST", "0.0.0.0")
	with pytest.raises(ConfigError, match="SWITCHBOARD_TOKEN"):
		load_config(dotenv_path=tmp_path / "does-not-exist.env")


def test_nonloopback_host_with_token_ok(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	monkeypatch.setenv("SWITCHBOARD_HOST", "0.0.0.0")
	monkeypatch.setenv("SWITCHBOARD_TOKEN", "sekrit-abc")
	cfg = load_config(dotenv_path=tmp_path / "does-not-exist.env")
	assert cfg.host == "0.0.0.0"
	assert cfg.auth_token == "sekrit-abc"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_hosts_do_not_require_token(monkeypatch, tmp_path, host):
	_clear_env(monkeypatch)
	monkeypatch.setenv("SWITCHBOARD_HOST", host)
	cfg = load_config(dotenv_path=tmp_path / "does-not-exist.env")
	assert cfg.auth_token is None
