"""POST /sessions/{sid}/telemetry: the Claude Code mod's session telemetry."""

from starlette.applications import Starlette
from starlette.testclient import TestClient

from server.quota_store import QuotaStore
from server.registry import Registry
from server.session_registry import SessionRegistry
from tests.conftest import make_active_conversation


class _Backend:
	def __init__(self, fail_quota: bool = False) -> None:
		self.quota_writes: list = []
		self.titles: list = []
		self.fail_quota = fail_quota

	async def write_quota(self, quota):
		if self.fail_quota:
			raise RuntimeError("firebase down")
		self.quota_writes.append(quota)

	async def write_conversation_title(self, conv_id, title, title_source=None):
		self.titles.append((conv_id, title, title_source))


class _Logger:
	def __init__(self) -> None:
		self.errors: list[str] = []

	async def surface_error(self, msg):
		self.errors.append(msg)


def _app(sessions, quota, backend, logger, registry=None, clock=None):
	from server.main import _build_telemetry_route
	app = Starlette()
	app.add_route(
		"/sessions/{sid}/telemetry",
		_build_telemetry_route(sessions, quota, backend, logger, registry, clock=clock),
		methods=["POST"],
	)
	return app


FULL = {
	"context": {"tokens": 412000, "window": 1000000},
	"rate_limits": [{"kind": "five_hour", "percent_used": 23.5, "resets_at": "2026-10-10T18:00:00Z"}],
	"model": "claude-opus-5-5",
	"effort": "high",
	"title": {"name": "Watchtower metrics reads", "source": "ai-title"},
}


def test_a_report_writes_the_session_record_and_the_quota():
	sessions, quota, backend = SessionRegistry(), QuotaStore(), _Backend()
	with TestClient(_app(sessions, quota, backend, _Logger())) as client:
		resp = client.post("/sessions/s1/telemetry", json=FULL)
	assert resp.status_code == 200
	rec = sessions.get("s1")
	assert (rec.context_window, rec.model, rec.effort, rec.name) == (1000000, "claude-opus-5-5", "high", "Watchtower metrics reads")
	assert len(backend.quota_writes) == 1
	assert backend.quota_writes[0]["five_hour"] == {"percent_used": 23.5, "resets_at": "2026-10-10T18:00:00Z"}


def test_an_unchanged_quota_is_not_rewritten():
	sessions, quota, backend = SessionRegistry(), QuotaStore(), _Backend()
	with TestClient(_app(sessions, quota, backend, _Logger())) as client:
		client.post("/sessions/s1/telemetry", json=FULL)
		client.post("/sessions/s2/telemetry", json={"rate_limits": FULL["rate_limits"]})
	assert len(backend.quota_writes) == 1


def test_one_bad_field_does_not_sink_the_rest():
	sessions, quota, backend = SessionRegistry(), QuotaStore(), _Backend()
	body = {**FULL, "rate_limits": [{"kind": "five_hour", "percent_used": 150}]}
	with TestClient(_app(sessions, quota, backend, _Logger())) as client:
		assert client.post("/sessions/s1/telemetry", json=body).status_code == 200
	assert sessions.get("s1").model == "claude-opus-5-5"
	assert backend.quota_writes == []


def test_malformed_json_and_empty_reports_answer_200_and_change_nothing():
	sessions, quota, backend = SessionRegistry(), QuotaStore(), _Backend()
	with TestClient(_app(sessions, quota, backend, _Logger())) as client:
		assert client.post("/sessions/s1/telemetry", content=b"not json").status_code == 200
		assert client.post("/sessions/s1/telemetry", json={"bogus": 1}).status_code == 200
	assert sessions.get("s1") is None


def test_a_title_retitles_the_sessions_solo_conversation():
	sessions, quota, backend = SessionRegistry(), QuotaStore(), _Backend()
	registry = Registry()
	conv = make_active_conversation(conversation_id="conv-1", member_session_id="s1")
	conv.title_source = "default"
	registry.conversations["conv-1"] = conv
	registry.bind_session("s1", "conv-1")
	with TestClient(_app(sessions, quota, backend, _Logger(), registry)) as client:
		client.post("/sessions/s1/telemetry", json={"title": {"name": "Fix undo", "source": "custom-title"}})
	assert conv.title == "Fix undo"
	assert backend.titles == [("conv-1", "Fix undo", "session")]


def test_a_failed_quota_write_is_logged_and_the_record_still_lands():
	sessions, quota, backend, logger = SessionRegistry(), QuotaStore(), _Backend(fail_quota=True), _Logger()
	with TestClient(_app(sessions, quota, backend, logger)) as client:
		assert client.post("/sessions/s1/telemetry", json=FULL).status_code == 200
	assert sessions.get("s1").model == "claude-opus-5-5"
	assert any("quota_write_error" in e for e in logger.errors)


class _Clock:
	def __init__(self) -> None:
		self.t = 1000.0

	def __call__(self) -> float:
		return self.t


def test_a_context_report_confirms_the_quota_reading():
	# A post-turn report without rate limits means they had not moved a whole
	# point, so the stored reading is current as of that report.
	sessions, quota, backend, clock = SessionRegistry(), QuotaStore(), _Backend(), _Clock()
	with TestClient(_app(sessions, quota, backend, _Logger(), clock=clock)) as client:
		client.post("/sessions/s1/telemetry", json=FULL)
		first = backend.quota_writes[-1]["received_at"]
		clock.t += 61
		client.post("/sessions/s1/telemetry", json={"context": {"tokens": 5, "window": 100}})
	assert len(backend.quota_writes) == 2
	assert backend.quota_writes[-1]["received_at"] != first
	assert backend.quota_writes[-1]["five_hour"] == backend.quota_writes[0]["five_hour"]


def test_confirmations_publish_at_most_once_a_minute():
	sessions, quota, backend, clock = SessionRegistry(), QuotaStore(), _Backend(), _Clock()
	with TestClient(_app(sessions, quota, backend, _Logger(), clock=clock)) as client:
		client.post("/sessions/s1/telemetry", json=FULL)
		clock.t += 30
		client.post("/sessions/s1/telemetry", json={"context": {"tokens": 5, "window": 100}})
	assert len(backend.quota_writes) == 1


def test_a_report_without_context_or_rate_limits_does_not_confirm():
	sessions, quota, backend, clock = SessionRegistry(), QuotaStore(), _Backend(), _Clock()
	with TestClient(_app(sessions, quota, backend, _Logger(), clock=clock)) as client:
		client.post("/sessions/s1/telemetry", json=FULL)
		clock.t += 120
		client.post("/sessions/s1/telemetry", json={"model": "claude-opus-5-5", "effort": "high"})
	assert len(backend.quota_writes) == 1
