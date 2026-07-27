from datetime import datetime, timezone
import pytest
from server.antigravity_status import (
	AntigravityStatus,
	AntigravityStatusService,
	AntigravityStatusWatch,
	parse_gcp_incidents,
	unknown_antigravity_status,
)


def test_parse_gcp_incidents_empty_and_operational():
	now = datetime.now(timezone.utc)
	res = parse_gcp_incidents("[]", now)
	assert res is not None
	assert res.level == "operational"
	assert res.description == "All Systems Operational"
	assert res.incidents == []
	assert res.local_lsp_healthy is True


def test_parse_gcp_incidents_irrelevant_filtered():
	now = datetime.now(timezone.utc)
	json_text = """[
		{"service_name": "Compute Engine", "summary": "VM instance issue", "status_impact": "SERVICE_DISRUPTION"}
	]"""
	res = parse_gcp_incidents(json_text, now)
	assert res is not None
	assert res.level == "operational"
	assert res.incidents == []


def test_parse_gcp_incidents_active_ai_incident():
	now = datetime.now(timezone.utc)
	json_text = """[
		{
			"service_name": "Vertex AI",
			"external_desc": "Vertex AI API Degraded Performance",
			"status_impact": "SERVICE_DISRUPTION"
		}
	]"""
	res = parse_gcp_incidents(json_text, now)
	assert res is not None
	assert res.level == "major"
	assert res.description == "Google Cloud: 1 active incident(s)"
	assert res.incidents == ["Vertex AI API Degraded Performance"]


def test_parse_gcp_incidents_local_lsp_unhealthy():
	now = datetime.now(timezone.utc)
	res = parse_gcp_incidents("[]", now, local_lsp_healthy=False)
	assert res is not None
	assert res.level == "minor"
	assert res.description == "Local LSP unreachable"
	assert res.incidents == ["Local Antigravity Language Server unreachable"]
	assert res.local_lsp_healthy is False


def test_watch_state_machine():
	watch = AntigravityStatusWatch()
	now = datetime.now(timezone.utc)
	assert watch.state == "idle"

	# Operational status -> stays idle
	op = AntigravityStatus("operational", "Operational", [], now)
	assert watch.apply_fetch(op, now) == "none"
	assert watch.state == "idle"

	# Degraded status -> enters watching
	deg = AntigravityStatus("minor", "Degraded", ["Issue"], now)
	assert watch.apply_fetch(deg, now) == "start_polling"
	assert watch.state == "watching"

	snap = watch.snapshot()
	assert snap["dot_visible"] is True
	assert snap["button"] == "stop"

	# Back to operational -> resolved_unacked
	assert watch.apply_fetch(op, now) == "stop_polling"
	assert watch.state == "resolved_unacked"

	snap2 = watch.snapshot()
	assert snap2["button"] == "clear"

	# Acknowledge -> idle
	assert watch.acknowledge() == "stop_polling"
	assert watch.state == "idle"


@pytest.mark.asyncio
async def test_service_check_and_stop():
	published = []

	async def pub(data):
		published.append(data)

	now = datetime.now(timezone.utc)

	async def mock_fetch():
		return AntigravityStatus("minor", "Minor Issue", ["Problem"], now)

	svc = AntigravityStatusService(publish=pub, fetch=mock_fetch)
	view = await svc.check()
	assert view["watch_state"] == "watching"
	assert len(published) > 0

	view_stop = await svc.stop()
	assert view_stop["watch_state"] == "idle"
