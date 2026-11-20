import asyncio

from server.main import SessionMirrorWriter


async def _pump(n=10):
	for _ in range(n):
		await asyncio.sleep(0)


class FakeBackend:
	def __init__(self):
		self.calls = []
		self.gates = {}
		self.held = {}
		self.fail_first = False

	def hold(self, sid):
		# Holds only the next call for this session; later calls run freely.
		self.gates[sid] = asyncio.Event()

	def release(self, sid):
		self.held.pop(sid).set()

	async def _record(self, kind, sid, payload):
		gate = self.gates.pop(sid, None)
		if gate is not None:
			self.held[sid] = gate
			await gate.wait()
		if self.fail_first:
			self.fail_first = False
			raise RuntimeError("boom")
		self.calls.append((kind, sid, payload))

	async def write_session_record(self, sid, payload):
		await self._record("write", sid, payload)

	async def delete_session_record(self, sid):
		await self._record("delete", sid, None)


class FakeLogger:
	def __init__(self):
		self.errors = []

	async def surface_error(self, detail, correlation=None):
		self.errors.append(detail)


def _make():
	backend = FakeBackend()
	logger = FakeLogger()
	return backend, logger, SessionMirrorWriter(lambda: backend, logger)


async def test_two_submits_while_first_held_last_payload_lands_last():
	backend, _, writer = _make()
	backend.hold("s1")
	writer.submit("s1", {"state": "idle"})
	await _pump()
	writer.submit("s1", {"state": "idle", "conversation_id": "conv-1"})
	await _pump()
	backend.release("s1")
	await _pump()
	assert backend.calls[-1] == ("write", "s1", {"state": "idle", "conversation_id": "conv-1"})


async def test_submits_while_held_coalesce_to_one_more_write():
	backend, _, writer = _make()
	backend.hold("s1")
	writer.submit("s1", {"n": 0})
	await _pump()
	for n in (1, 2, 3):
		writer.submit("s1", {"n": n})
	await _pump()
	backend.release("s1")
	await _pump()
	assert backend.calls == [("write", "s1", {"n": 0}), ("write", "s1", {"n": 3})]


async def test_delete_after_write_is_the_final_call():
	backend, _, writer = _make()
	backend.hold("s1")
	writer.submit("s1", {"n": 0})
	await _pump()
	writer.submit("s1", None)
	await _pump()
	backend.release("s1")
	await _pump()
	assert backend.calls[-1] == ("delete", "s1", None)


async def test_other_session_is_not_blocked_by_a_held_write():
	backend, _, writer = _make()
	backend.hold("s1")
	writer.submit("s1", {"n": 1})
	await _pump()
	writer.submit("s2", {"n": 2})
	await _pump()
	assert backend.calls == [("write", "s2", {"n": 2})]
	backend.release("s1")
	await _pump()


async def test_failing_write_is_logged_and_next_payload_still_written():
	backend, logger, writer = _make()
	backend.hold("s1")
	backend.fail_first = True
	writer.submit("s1", {"n": 1})
	await _pump()
	writer.submit("s1", {"n": 2})
	await _pump()
	backend.release("s1")
	await _pump()
	assert len(logger.errors) == 1
	assert logger.errors[0].startswith("session_mirror_write_failed: s1 ")
	assert backend.calls == [("write", "s1", {"n": 2})]
