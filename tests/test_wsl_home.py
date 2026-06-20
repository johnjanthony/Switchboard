"""Tests for resolve_wsl_home() in server.main."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_resolve_wsl_home_success():
	async def run():
		from server.main import resolve_wsl_home
		mock_proc = MagicMock()
		mock_proc.returncode = 0
		mock_proc.communicate = AsyncMock(return_value=(b"/home/john\n", b""))
		with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
			result = await resolve_wsl_home()
		assert result == "/home/john"
	asyncio.run(run())


def test_resolve_wsl_home_returns_none_on_nonzero_exit():
	async def run():
		from server.main import resolve_wsl_home
		mock_proc = MagicMock()
		mock_proc.returncode = 1
		mock_proc.communicate = AsyncMock(return_value=(b"", b"wsl not found"))
		with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
			result = await resolve_wsl_home()
		assert result is None
	asyncio.run(run())


def test_resolve_wsl_home_returns_none_on_exception():
	async def run():
		from server.main import resolve_wsl_home
		with patch("asyncio.create_subprocess_exec", side_effect=Exception("wsl missing")):
			result = await resolve_wsl_home()
		assert result is None
	asyncio.run(run())


def test_resolve_wsl_home_returns_none_on_empty_output():
	async def run():
		from server.main import resolve_wsl_home
		mock_proc = MagicMock()
		mock_proc.returncode = 0
		mock_proc.communicate = AsyncMock(return_value=(b"\n", b""))
		with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
			result = await resolve_wsl_home()
		assert result is None
	asyncio.run(run())


def test_resolve_wsl_home_strips_trailing_whitespace():
	async def run():
		from server.main import resolve_wsl_home
		mock_proc = MagicMock()
		mock_proc.returncode = 0
		mock_proc.communicate = AsyncMock(return_value=(b"/home/alice\r\n", b""))
		with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
			result = await resolve_wsl_home()
		assert result == "/home/alice"
	asyncio.run(run())
