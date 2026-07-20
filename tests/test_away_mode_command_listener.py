"""away_mode_commands and widget/status_request are routed through the shared
_start_command_listener. These tests drive the
UNIFIED path: firing synthetic events through a real SupervisedListener's raw
_user_callback and reading results back off the poller's own async-generator
task (the same task that primed the listener is the one waiting on the
queue.get(), so awaiting it later resolves whatever lands there)."""
import asyncio
import contextlib
import threading
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


class _Event:
	def __init__(self, event_type, path, data):
		self.event_type = event_type
		self.path = path
		self.data = data


class _FakeLogger:
	"""Records surface_error calls so the stale-drop tests can assert on them."""
	def __init__(self):
		self.calls: list[str] = []

	async def surface_error(self, message: str) -> None:
		self.calls.append(message)


def _make_backend(monkeypatch, loop, logger=None):
	"""Construct a FirebaseBackend shell via __new__ (bypassing __init__, which
	touches real Firebase), wired for the unified command-listener path.

	Patches server.firebase.db (the delete paths: _handle_then_delete and
	_schedule_command_delete both call db.reference(...) from inside
	firebase.py) and server.firebase_supervisor.db (SupervisedListener's own
	db.reference(path).listen(...) call in _open_registration) so priming a
	REAL SupervisedListener never touches the network -- a plain MagicMock
	stands in for both, mirroring the mock_db fixture in test_firebase_paths.py."""
	from server import firebase as fb_module
	import server.firebase_supervisor as fbsup_module

	monkeypatch.setattr(fb_module, "db", MagicMock())
	monkeypatch.setattr(fbsup_module, "db", MagicMock())

	be = fb_module.FirebaseBackend.__new__(fb_module.FirebaseBackend)
	be._loop = loop
	be._supervised = {}
	be._away_mode_cmd_queue = asyncio.Queue()
	be._status_request_queue = asyncio.Queue()
	be._logger = logger
	be.send_text = AsyncMock()
	return be


async def _prime(poll_bound_method):
	"""Drive one step of the poller as a task: the async-generator body runs
	_start_command_listener synchronously (registering be._supervised[...])
	then suspends on the queue get. The returned task IS that queue.get(), so
	awaiting it later (with a timeout) resolves to whatever lands on the queue."""
	gen = poll_bound_method()
	task = asyncio.ensure_future(gen.__anext__())
	for _ in range(5):
		await asyncio.sleep(0)
	return task


async def _cleanup(be, *pending_tasks):
	for task in pending_tasks:
		task.cancel()
		with contextlib.suppress(asyncio.CancelledError):
			await task
	for sup in be._supervised.values():
		await sup.stop()


def _fresh_issued_at() -> str:
	return datetime.now(timezone.utc).isoformat()


@pytest.mark.asyncio
async def test_away_command_bounces_from_foreign_thread(monkeypatch):
	"""The callback runs on the Firebase SDK thread and must bounce enqueue
	work onto the event loop; firing it from a real thread proves the bounce."""
	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop)

	task = await _prime(be.poll_away_mode_commands)
	sup = be._supervised["away_mode_commands"]

	entry = {"type": "enter_global", "issued_at": _fresh_issued_at()}

	def fire():
		sup._user_callback(_Event("put", "/cmd-1", entry))

	t = threading.Thread(target=fire)
	t.start()
	t.join()

	cmd = await asyncio.wait_for(task, timeout=1.0)
	assert cmd["type"] == "enter_global"

	await _cleanup(be)


@pytest.mark.asyncio
async def test_away_command_dedupes_redelivered_id(monkeypatch):
	"""Same cmd id delivered twice (reconnect snapshot replay) must reach the
	queue exactly once; the per-listener `processed` set dedupes redeliveries."""
	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop)

	task = await _prime(be.poll_away_mode_commands)
	sup = be._supervised["away_mode_commands"]

	entry = {"type": "enter_global", "issued_at": _fresh_issued_at()}
	sup._user_callback(_Event("put", "/cmd-dup", entry))
	sup._user_callback(_Event("put", "/cmd-dup", entry))  # redelivery, same id

	cmd = await asyncio.wait_for(task, timeout=1.0)
	assert cmd["type"] == "enter_global"

	# The redelivery must never have enqueued a second entry.
	for _ in range(5):
		await asyncio.sleep(0)
	assert be._away_mode_cmd_queue.qsize() == 0

	await _cleanup(be)


@pytest.mark.asyncio
async def test_stale_away_command_dropped_with_notice(monkeypatch):
	"""Decided 2026-06-11 gate, newly applied to away_mode_commands (the
	bespoke pair never TTL-gated): a stale command is deleted and logged,
	and (stale_notice defaults True here) the phone gets a notice."""
	loop = asyncio.get_running_loop()
	logger = _FakeLogger()
	be = _make_backend(monkeypatch, loop, logger=logger)

	task = await _prime(be.poll_away_mode_commands)
	sup = be._supervised["away_mode_commands"]

	stale = {"type": "enter_global", "issued_at": "2020-01-01T00:00:00+00:00"}
	sup._user_callback(_Event("put", "/cmd-stale", stale))
	await asyncio.sleep(0.05)  # drain call_soon_threadsafe + to_thread

	assert be._away_mode_cmd_queue.qsize() == 0
	assert not task.done(), "stale command must never reach the handler"

	be.send_text.assert_awaited_once()
	notice = be.send_text.await_args.args[0]
	assert "stale" in notice.lower()

	assert any(
		"stale_command_dropped" in c and "away_mode_commands/cmd-stale" in c
		for c in logger.calls
	)

	from server import firebase as fb_module
	calls = [str(c) for c in fb_module.db.reference.call_args_list]
	assert any("away_mode_commands/cmd-stale" in c for c in calls)
	fb_module.db.reference.return_value.delete.assert_called()

	await _cleanup(be, task)


@pytest.mark.asyncio
async def test_stale_status_request_dropped_without_notice(monkeypatch):
	"""widget/status_request registers stale_notice=False: the drop is still
	deleted and logged via surface_error, but no phone-visible notice fires."""
	loop = asyncio.get_running_loop()
	logger = _FakeLogger()
	be = _make_backend(monkeypatch, loop, logger=logger)

	task = await _prime(be.poll_status_request_commands)
	sup = be._supervised["status_request"]

	stale = {"type": "status_request", "issued_at": "2020-01-01T00:00:00+00:00"}
	sup._user_callback(_Event("put", "/cmd-stale-sr", stale))
	await asyncio.sleep(0.05)  # drain call_soon_threadsafe + to_thread

	assert be._status_request_queue.qsize() == 0
	assert not task.done(), "stale command must never reach the handler"

	be.send_text.assert_not_awaited()

	assert any(
		"stale_command_dropped" in c and "status_request/cmd-stale-sr" in c
		for c in logger.calls
	)

	from server import firebase as fb_module
	calls = [str(c) for c in fb_module.db.reference.call_args_list]
	assert any("widget/status_request/cmd-stale-sr" in c for c in calls)
	fb_module.db.reference.return_value.delete.assert_called()

	await _cleanup(be, task)


@pytest.mark.asyncio
async def test_supervised_listener_names_preserved(monkeypatch):
	"""/healthz parity: the supervised-listener keys stay away_mode_commands
	and status_request (the latter via the name= override), regardless of the
	shared _start_command_listener plumbing underneath."""
	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop)

	away_task = await _prime(be.poll_away_mode_commands)
	status_task = await _prime(be.poll_status_request_commands)

	assert set(be._supervised) >= {"away_mode_commands", "status_request"}

	await _cleanup(be, away_task, status_task)
