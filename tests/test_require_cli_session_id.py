"""Unit tests for the require_cli_session_id decorator."""

import asyncio

from server.gateway.handlers import require_cli_session_id


def test_decorator_rejects_missing_cli_session_id():
	@require_cli_session_id
	async def inner_tool(sender, *, cli_session_id, cwd):
		return f"ok {cli_session_id}"

	result = asyncio.run(inner_tool(sender="Claude"))
	assert "ERROR" in result
	assert "cli_session_id required" in result


def test_decorator_rejects_empty_string():
	@require_cli_session_id
	async def inner_tool(sender, *, cli_session_id, cwd):
		return f"ok {cli_session_id}"

	result = asyncio.run(inner_tool(sender="Claude", cli_session_id="", cwd="C:/X"))
	assert "ERROR" in result


def test_decorator_passes_through_when_present():
	@require_cli_session_id
	async def inner_tool(sender, *, cli_session_id, cwd):
		return f"got {cli_session_id} {cwd}"

	result = asyncio.run(inner_tool(sender="Claude", cli_session_id="s-1", cwd="C:/X"))
	assert result == "got s-1 C:/X"


def test_decorator_forwards_other_kwargs():
	@require_cli_session_id
	async def inner_tool(sender, question, *, cli_session_id, cwd):
		return f"{sender} asked {question} from {cli_session_id}"

	result = asyncio.run(inner_tool(
		sender="Claude", question="hi?",
		cli_session_id="s-1", cwd="C:/X",
	))
	assert result == "Claude asked hi? from s-1"
