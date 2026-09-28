"""Unit tests for the live smoke harness's own logic (scripts/smoke/). The
harness imports nothing from server/, so it is loaded here by file path."""

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

from server.build_info import source_fingerprint as server_fingerprint

_SMOKE_DIR = Path(__file__).resolve().parents[1] / "scripts" / "smoke"


def _load(name: str):
	spec = importlib.util.spec_from_file_location(name, _SMOKE_DIR / f"{name}.py")
	module = importlib.util.module_from_spec(spec)
	sys.modules[name] = module
	spec.loader.exec_module(module)
	return module


smoke_lib = _load("_smoke_lib")


def test_harness_fingerprint_matches_the_server_copy(tmp_path):
	"""The harness carries its own copy of source_fingerprint; if the two ever
	disagree, every preflight fails as stale. Exercise every rule at once:
	nesting, CRLF normalization, and non-.py files that must be ignored."""
	(tmp_path / "pkg").mkdir()
	(tmp_path / "a.py").write_bytes(b"x = 1\r\ny = 2\r\n")
	(tmp_path / "pkg" / "b.py").write_bytes(b"z = 3\n")
	(tmp_path / "pkg" / "c.pyc").write_bytes(b"\x00")
	(tmp_path / "notes.txt").write_bytes(b"hello")
	assert smoke_lib.source_fingerprint(tmp_path) == server_fingerprint(tmp_path)


def test_harness_fingerprint_matches_the_real_server_tree():
	"""Same pin against the actual server/ package, so a copy that only agrees
	on toy inputs cannot slip through."""
	server_root = Path(__file__).resolve().parents[1] / "server"
	assert smoke_lib.source_fingerprint(server_root) == server_fingerprint(server_root)


def _source_tree(tmp_path):
	root = tmp_path / "server"
	root.mkdir()
	(root / "main.py").write_bytes(b"x = 1\n")
	return root


def test_freshness_fails_when_the_service_runs_other_code(tmp_path):
	root = _source_tree(tmp_path)
	healthz = {"service": {
		"started_at": "2026-07-30T13:08:00+00:00",
		"source_fingerprint": "0" * 64,
		"git_head": "abcdef0123456789abcdef0123456789abcdef01",
	}}
	with pytest.raises(smoke_lib.SmokeFailure) as info:
		smoke_lib.assert_service_fresh(healthz, root)
	message = str(info.value)
	assert "2026-07-30T13:08:00+00:00" in message
	assert "abcdef0" in message
	assert "restart-service.ps1 -SkipTests" in message


def test_freshness_passes_when_the_service_runs_the_tree(tmp_path):
	root = _source_tree(tmp_path)
	healthz = {"service": {
		"started_at": "2026-09-28T14:05:06+00:00",
		"source_fingerprint": server_fingerprint(root),
		"git_head": None,
	}}
	smoke_lib.assert_service_fresh(healthz, root)


def test_freshness_names_a_service_that_predates_the_report(tmp_path):
	"""A service started before /healthz grew the block cannot prove what it
	runs, which is itself proof it predates this tree."""
	root = _source_tree(tmp_path)
	with pytest.raises(smoke_lib.SmokeFailure) as info:
		smoke_lib.assert_service_fresh({"healthy": True}, root)
	message = str(info.value)
	assert "no 'service' block" in message
	assert "restart-service.ps1 -SkipTests" in message


def test_a_failure_with_an_empty_message_still_names_its_type():
	"""str(asyncio.TimeoutError()) is empty, which once rendered a FAIL row
	with no reason at all."""
	rep = smoke_lib.Reporter()
	with pytest.raises(smoke_lib.FlowSkip):
		with rep.flow("probe"):
			raise asyncio.TimeoutError()
	assert rep.results[-1].status == "FAIL"
	assert "TimeoutError" in rep.results[-1].detail


def test_an_unexpected_exception_is_reported_with_its_type():
	"""A harness SmokeFailure reads as written; anything else is a crash the
	reader must recognize as one (str(KeyError('service')) is just "'service'")."""
	rep = smoke_lib.Reporter()
	with pytest.raises(smoke_lib.FlowSkip):
		with rep.flow("crash"):
			raise KeyError("service")
	with pytest.raises(smoke_lib.FlowSkip):
		with rep.flow("assertion"):
			raise smoke_lib.SmokeFailure("listeners not live: ['responses']")
	assert [r.detail for r in rep.results] == ["KeyError: 'service'", "listeners not live: ['responses']"]


@pytest.mark.asyncio
async def test_await_task_timeout_names_what_it_was_waiting_for():
	never = asyncio.get_running_loop().create_future()
	with pytest.raises(smoke_lib.SmokeFailure) as info:
		await smoke_lib.await_task("interjection to resolve the blocking ask", never, 0.05)
	assert str(info.value) == "timeout (0.05s) waiting for interjection to resolve the blocking ask"
	assert never.cancelled()


@pytest.mark.asyncio
async def test_await_task_returns_the_result():
	async def _answer():
		return "yes"
	assert await smoke_lib.await_task("the answer", asyncio.ensure_future(_answer()), 1) == "yes"


smoke = _load("smoke")


def _flow(log, name, fail=False):
	async def _fn(ctx, rep, args):
		log.append(name)
		if fail:
			raise smoke_lib.SmokeFailure(f"{name} broke")
	return (name, _fn)


@pytest.mark.asyncio
async def test_flows_after_a_failure_are_reported_not_run():
	"""A failure stops the run, but every registered flow still gets a summary
	row, so an absent flow can never pass for one that was never registered."""
	log = []
	flows = [_flow(log, "one"), _flow(log, "two", fail=True), _flow(log, "three"), _flow(log, "four")]
	rep = smoke_lib.Reporter()
	await smoke.run_flows(flows, None, rep, None, skips={})
	assert log == ["one", "two"]
	assert [(r.name, r.status) for r in rep.results] == [
		("one", "PASS"), ("two", "FAIL"), ("three", "NOT RUN"), ("four", "NOT RUN")]
	assert rep.results[2].detail == "stopped after 'two' failed"


@pytest.mark.asyncio
async def test_requested_skips_never_run_and_keep_their_reason():
	"""A requested skip reads as SKIP with its flag even after a failure, so
	--skip-restart never shows up as NOT RUN."""
	log = []
	flows = [_flow(log, "one", fail=True), _flow(log, "two"), _flow(log, "restart")]
	rep = smoke_lib.Reporter()
	await smoke.run_flows(flows, None, rep, None, skips={"restart": "--skip-restart"})
	assert log == ["one"]
	assert [(r.name, r.status, r.detail) for r in rep.results] == [
		("one", "FAIL", "one broke"),
		("two", "NOT RUN", "stopped after 'one' failed"),
		("restart", "SKIP", "--skip-restart"),
	]


def test_summary_exit_code_and_rows(capsys):
	rep = smoke_lib.Reporter()
	rep.skip("restart survival", "--skip-restart")
	assert rep.summary() == 0
	rep.not_run("background ask round-trip", "stopped after 'x' failed")
	with pytest.raises(smoke_lib.FlowSkip):
		with rep.flow("x"):
			raise smoke_lib.SmokeFailure("boom")
	assert rep.summary() == 1
	out = capsys.readouterr().out
	assert "background ask round-trip                NOT RUN stopped after 'x' failed" in out


@pytest.mark.asyncio
async def test_cleanup_after_an_early_preflight_failure_touches_nothing(monkeypatch, tmp_path):
	"""Preflight can fail before it records prior away state or registers the
	smoke session (a stale service fails first). Cleanup must then neither
	"restore" away mode - that would turn off John's real away session - nor
	wait on a session-end for a session that never started."""
	calls = []

	def _get(url):
		calls.append(("GET", url))
		if url.endswith("/away-mode"):
			return {"active": True}
		return {"listeners": [], "dispatch_loops": []}

	async def _mcp(ctx, tool, args, timeout=30):
		calls.append(("MCP", tool))

	def _rtdb(ctx, path):
		raise AssertionError(f"cleanup touched RTDB at {path}")

	def _marker(ctx):
		raise AssertionError("cleanup wrote a session-end marker for a session that never started")

	monkeypatch.setattr(smoke, "http_get_json", _get)
	monkeypatch.setattr(smoke, "mcp_call", _mcp)
	monkeypatch.setattr(smoke, "rtdb", _rtdb)
	monkeypatch.setattr(smoke, "write_session_end_marker", _marker)
	ctx = smoke_lib.make_context("http://127.0.0.1:9876", tmp_path)

	await smoke.cleanup(ctx, smoke_lib.Reporter(), None)
	assert not [c for c in calls if c[0] == "MCP"]
