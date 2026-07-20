"""Unit tests for scripts/_hook_common.py (loaded via importlib - scripts/ is
not a package; the hook scripts import it as a sys.path[0] sibling)."""
from __future__ import annotations

import importlib.util
import io
import types
from pathlib import Path

_MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "_hook_common.py"


def _load():
	spec = importlib.util.spec_from_file_location("_hook_common", _MOD_PATH)
	mod = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(mod)
	return mod


def _with_stdin(monkeypatch, mod, data: bytes):
	fake = types.SimpleNamespace(buffer=io.BytesIO(data))
	monkeypatch.setattr(mod.sys, "stdin", fake)


def test_read_stdin_json_parses_utf8_bytes(monkeypatch):
	mod = _load()
	_with_stdin(monkeypatch, mod, '{"msg": "café"}'.encode("utf-8"))
	assert mod.read_stdin_json() == {"msg": "café"}


def test_read_stdin_json_empty_stdin_returns_empty_dict(monkeypatch):
	mod = _load()
	_with_stdin(monkeypatch, mod, b"")
	assert mod.read_stdin_json() == {}


def test_read_stdin_json_garbage_returns_empty_dict(monkeypatch):
	mod = _load()
	_with_stdin(monkeypatch, mod, b"not json at all")
	assert mod.read_stdin_json() == {}


def test_read_stdin_json_non_dict_returns_empty_dict(monkeypatch):
	mod = _load()
	_with_stdin(monkeypatch, mod, b"[1, 2, 3]")
	assert mod.read_stdin_json() == {}


def test_base_url_env_and_default(monkeypatch):
	mod = _load()
	monkeypatch.delenv("SWITCHBOARD_BASE_URL", raising=False)
	assert mod.base_url() == "http://127.0.0.1:9876"
	monkeypatch.setenv("SWITCHBOARD_BASE_URL", "http://10.0.0.5:9876")
	assert mod.base_url() == "http://10.0.0.5:9876"


def test_auth_headers_with_and_without_token(monkeypatch):
	mod = _load()
	monkeypatch.delenv("SWITCHBOARD_TOKEN", raising=False)
	assert mod.auth_headers() == {}
	monkeypatch.setenv("SWITCHBOARD_TOKEN", "sekrit-1")
	assert mod.auth_headers() == {"Authorization": "Bearer sekrit-1"}
