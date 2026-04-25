"""Tests for the JSONL audit logger."""

import json
from pathlib import Path

import pytest

from server.logging_jsonl import JsonlLogger


def read_events(path: Path) -> list[dict]:
	return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_request_created_writes_expected_fields(tmp_path):
	logger = JsonlLogger(tmp_path / "log.jsonl")
	logger.request_created("a3f1", "IR2", "Overwrite foo.java?")
	events = read_events(tmp_path / "log.jsonl")
	assert len(events) == 1
	ev = events[0]
	assert ev["event"] == "request_created"
	assert ev["request_id"] == "a3f1"
	assert ev["channel_id"] == "IR2"
	assert ev["question_preview"].startswith("Overwrite foo.java?")
	assert "ts" in ev


def test_request_resolved_records_duration_and_source(tmp_path):
	logger = JsonlLogger(tmp_path / "log.jsonl")
	logger.request_resolved(
		"a3f1", "IR2", response_text="yes", source="firebase", duration_ms=123
	)
	ev = read_events(tmp_path / "log.jsonl")[0]
	assert ev["event"] == "request_resolved"
	assert ev["response_preview"] == "yes"
	assert ev["source"] == "firebase"
	assert ev["duration_ms"] == 123


def test_timeout_event(tmp_path):
	logger = JsonlLogger(tmp_path / "log.jsonl")
	logger.timeout("a3f1", "IR2", timeout_seconds=86400)
	ev = read_events(tmp_path / "log.jsonl")[0]
	assert ev["event"] == "timeout"
	assert ev["timeout_seconds"] == 86400


def test_notify_sent_truncates_long_message(tmp_path):
	logger = JsonlLogger(tmp_path / "log.jsonl")
	long_msg = "x" * 500
	logger.notify_sent("IR2", long_msg)
	ev = read_events(tmp_path / "log.jsonl")[0]
	assert ev["event"] == "notify_sent"
	assert len(ev["message_preview"]) == 100


def test_tool_error_event(tmp_path):
	logger = JsonlLogger(tmp_path / "log.jsonl")
	logger.tool_error("a3f1", "IR2", "boom")
	ev = read_events(tmp_path / "log.jsonl")[0]
	assert ev["event"] == "tool_error"
	assert ev["error"] == "boom"


def test_creates_parent_directory(tmp_path):
	path = tmp_path / "logs" / "nested" / "log.jsonl"
	logger = JsonlLogger(path)
	logger.request_created("a3f1", "IR2", "q")
	assert path.exists()


def test_spawn_started_writes_expected_fields(tmp_path):
	logger = JsonlLogger(tmp_path / "log.jsonl")
	logger.spawn_started("a1b2c3d4", "rpdm/next-gen", "/Work/rpdm/next-gen", "fix migration")
	events = read_events(tmp_path / "log.jsonl")
	assert len(events) == 1
	ev = events[0]
	assert ev["event"] == "spawn_started"
	assert ev["spawn_id"] == "a1b2c3d4"
	assert ev["project_key"] == "rpdm/next-gen"
	assert ev["project_path"] == "/Work/rpdm/next-gen"
	assert ev["prompt_preview"] == "fix migration"
	assert "ts" in ev


def test_spawn_invalid_path_writes_expected_fields(tmp_path):
	logger = JsonlLogger(tmp_path / "log.jsonl")
	logger.spawn_invalid_path("../evil", "/Work/../evil")
	events = read_events(tmp_path / "log.jsonl")
	assert len(events) == 1
	ev = events[0]
	assert ev["event"] == "spawn_invalid_path"
	assert ev["project_key"] == "../evil"
	assert ev["resolved_path"] == "/Work/../evil"
	assert "ts" in ev


def test_document_sent_writes_required_fields(tmp_path):
	logger = JsonlLogger(tmp_path / "log.jsonl")
	logger.document_sent(
		"IR2", "/work/report.txt", 1024, "abc123def456", caption="Here's the report"
	)
	ev = read_events(tmp_path / "log.jsonl")[0]
	assert ev["event"] == "document_sent"
	assert ev["channel_id"] == "IR2"
	assert ev["path"] == "/work/report.txt"
	assert ev["size_bytes"] == 1024
	assert ev["sha256"] == "abc123def456"
	assert ev["caption_preview"] == "Here's the report"
	assert "ts" in ev


def test_document_sent_omits_caption_preview_when_none(tmp_path):
	logger = JsonlLogger(tmp_path / "log.jsonl")
	logger.document_sent("IR2", "/work/report.txt", 512, "deadbeef", caption=None)
	ev = read_events(tmp_path / "log.jsonl")[0]
	assert "caption_preview" not in ev


def test_document_sent_truncates_long_caption(tmp_path):
	logger = JsonlLogger(tmp_path / "log.jsonl")
	long_caption = "c" * 500
	logger.document_sent("IR2", "/work/report.txt", 512, "deadbeef", caption=long_caption)
	ev = read_events(tmp_path / "log.jsonl")[0]
	assert len(ev["caption_preview"]) == 100


def test_away_mode_entered_writes_event(tmp_path):
	logger = JsonlLogger(tmp_path / "log.jsonl")
	logger.away_mode_entered()
	lines = (tmp_path / "log.jsonl").read_text(encoding="utf-8").strip().splitlines()
	assert len(lines) == 1
	event = json.loads(lines[0])
	assert event["event"] == "away_mode_entered"
	assert "ts" in event
	assert "reason" not in event


def test_away_mode_entered_with_reason(tmp_path):
	logger = JsonlLogger(tmp_path / "log.jsonl")
	logger.away_mode_entered(reason="spawn")
	event = json.loads((tmp_path / "log.jsonl").read_text(encoding="utf-8").strip())
	assert event["event"] == "away_mode_entered"
	assert event["reason"] == "spawn"


def test_away_mode_exited_writes_event(tmp_path):
	logger = JsonlLogger(tmp_path / "log.jsonl")
	logger.away_mode_exited()
	event = json.loads((tmp_path / "log.jsonl").read_text(encoding="utf-8").strip())
	assert event["event"] == "away_mode_exited"
	assert "ts" in event


def test_away_mode_exited_with_reason(tmp_path):
	from server.logging_jsonl import JsonlLogger
	log = tmp_path / "audit.jsonl"
	logger = JsonlLogger(log)
	logger.away_mode_exited(reason="android")
	contents = log.read_text(encoding="utf-8").strip()
	event = json.loads(contents.splitlines()[-1])
	assert event["event"] == "away_mode_exited"
	assert event["reason"] == "android"


def test_away_mode_exited_without_reason(tmp_path):
	from server.logging_jsonl import JsonlLogger
	log = tmp_path / "audit.jsonl"
	logger = JsonlLogger(log)
	logger.away_mode_exited()
	contents = log.read_text(encoding="utf-8").strip()
	event = json.loads(contents.splitlines()[-1])
	assert event["event"] == "away_mode_exited"
	assert "reason" not in event
