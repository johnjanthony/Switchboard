"""T-150 (c): the away_mode_commands listener callback runs on the Firebase SDK
thread and must bounce enqueue work onto the event loop. This drives the
callback from a non-loop thread and asserts the command lands on the queue."""
import asyncio
import threading
import pytest


@pytest.fixture
def cfg(tmp_path):
	from server.config import Config
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


def _make_backend(loop):
	"""Construct a FirebaseBackend shell with just the queue + loop wired,
	bypassing real Firebase init. The callback only touches self._loop and
	self._away_mode_cmd_queue / self._enqueue_away_mode_cmd."""
	from server.firebase import FirebaseBackend
	be = FirebaseBackend.__new__(FirebaseBackend)
	be._loop = loop
	be._away_mode_cmd_queue = asyncio.Queue()
	be._away_mode_processed = set()
	be._logger = None
	# _enqueue_away_mode_cmd schedules a delete via _schedule_command_delete;
	# stub it so the test does not require a live Firebase ref.
	be._schedule_command_delete = lambda node, cmd_id: None
	return be


class _Event:
	def __init__(self, event_type, path, data):
		self.event_type = event_type
		self.path = path
		self.data = data


@pytest.mark.asyncio
async def test_on_away_mode_command_bounces_from_foreign_thread(cfg):
	loop = asyncio.get_running_loop()
	be = _make_backend(loop)

	def fire():
		be._on_away_mode_command(_Event("put", "/cmd-1", {"type": "enter_global", "issued_at": "2026-06-13T00:00:00+00:00"}))

	t = threading.Thread(target=fire)
	t.start()
	t.join()

	# The enqueue was scheduled via call_soon_threadsafe; pump the loop and read.
	cmd = await asyncio.wait_for(be._away_mode_cmd_queue.get(), timeout=1.0)
	assert cmd["type"] == "enter_global"


@pytest.mark.asyncio
async def test_on_away_mode_command_dedupes_redelivered_id(cfg):
	loop = asyncio.get_running_loop()
	be = _make_backend(loop)
	entry = {"type": "enter_global", "issued_at": "2026-06-13T00:00:00+00:00"}

	# Same cmd_id delivered twice (reconnect snapshot replay).
	be._on_away_mode_command(_Event("put", "/cmd-1", entry))
	be._on_away_mode_command(_Event("put", "/cmd-1", entry))
	# Multiple yields: call_soon_threadsafe -> _enqueue_away_mode_cmd -> _spawn_bg put task
	for _ in range(5):
		await asyncio.sleep(0)

	# Only one copy should reach the queue.
	count = 0
	try:
		while True:
			be._away_mode_cmd_queue.get_nowait()
			count += 1
	except asyncio.QueueEmpty:
		pass
	assert count == 1, f"expected dedupe to 1 enqueue, got {count}"
