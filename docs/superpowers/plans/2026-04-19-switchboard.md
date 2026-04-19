# Switchboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Commit handling:** Per the developer's global protocol (`~/.claude/CLAUDE.md` — "Write git commands are PROHIBITED (commit, push, merge, rebase, reset, checkout, add). John will handle commits once your tasks are completed"), the `git add` / `git commit` commands shown as "Commit" steps are **suggested commit boundaries for the developer to run manually** after each task passes review. Executing agents: do not run these commands — after finishing a task, stop and report "Task N ready for commit review."

**Goal:** Build Switchboard — a localhost MCP server that lets Claude Code agents pause mid-task via an `ask_human` tool and resume when a Telegram reply arrives, with a messenger-backend abstraction so a future Android-via-Firebase client can plug in without gateway changes.

**Architecture:** Single Python asyncio process. `FastMCP` hosts the MCP HTTP/SSE endpoint on `localhost:9876`. A `MessengerBackend` ABC abstracts the mobile channel; v1 concrete impl is `TelegramBackend` over raw `httpx` long-polling. A `Registry` holds `PendingRequest` records (each with an `asyncio.Future`) keyed by short UUID, with a secondary `correlation → request_id` index so backends can resolve responses without knowing the request_id. A `dispatch_responses` task consumes `backend.poll_responses()` and resolves Futures. On timeout, `ask_human` asks the backend to send a follow-up message and returns the sentinel string `"__TIMEOUT__"`.

**Tech Stack:** Python 3.11+, `mcp[cli]>=1.2` (FastMCP + SSE transport), `httpx>=0.27`, `python-dotenv>=1.0`, `pytest>=8`, `pytest-asyncio>=0.23`, `respx>=0.21` (httpx mocking). Raw `httpx` was chosen over `python-telegram-bot` — the Telegram surface needed is tiny (`sendMessage`, `getUpdates`) and `respx` makes HTTP mocking trivial.

**Spec:** See [`docs/superpowers/specs/2026-04-19-switchboard-design.md`](../specs/2026-04-19-switchboard-design.md).

---

## File Structure

Each file has one responsibility. The gateway core (registry, messenger ABC, tool handlers) is transport-agnostic; the Telegram specifics are confined to `server/telegram.py`. Swapping in a Firebase backend in phase 2 means adding one file plus a one-line selector change.

```text
switchboard/
├── pyproject.toml                # package metadata, deps, pytest config
├── .env.example                  # template for TELEGRAM_BOT_TOKEN etc.
├── .gitignore                    # .env, logs/, __pycache__, .pytest_cache, etc.
├── README.md                     # install + run instructions
├── CLAUDE.md                     # agent orientation (already exists)
├── CLAUDE-JOURNAL.md             # session log (already exists)
├── docs/                         # (already exists — specs)
├── server/
│   ├── __init__.py               # package marker (empty)
│   ├── __main__.py               # `python -m server` → main.run()
│   ├── config.py                 # Config dataclass + load_config()
│   ├── registry.py               # PendingRequest + Registry
│   ├── messenger.py              # MessengerBackend ABC + IncomingResponse
│   ├── telegram.py               # TelegramBackend (httpx impl)
│   ├── gateway.py                # FastMCP tool handlers + dispatch loop
│   ├── logging_jsonl.py          # JsonlLogger
│   └── main.py                   # wire everything; start uvicorn + dispatch
├── skill/
│   └── SKILL.md                  # installed into ~/.claude/skills/switchboard/
├── tests/
│   ├── __init__.py               # empty
│   ├── conftest.py               # shared fixtures
│   ├── test_registry.py
│   ├── test_logging_jsonl.py
│   ├── test_messenger_contract.py
│   ├── test_config.py
│   ├── test_telegram_send.py
│   ├── test_telegram_poll.py
│   ├── test_gateway_ask_human.py
│   ├── test_gateway_notify_human.py
│   └── test_gateway_timeout.py
└── logs/                         # runtime-created, .gitignored
```

---

## Key Implementation Decisions Locked In

Reference for the executing agent — do not relitigate these during implementation.

- **Python indentation: tabs**, per the developer's global `CLAUDE.md`. Python 3 accepts pure-tab indentation (never mix with spaces in one file).
- **Line endings: CRLF** for all files (text, code, config). Convert with `unix2dos <file>` after writing if your editor defaults to LF. Verify with `file <path>`.
- **Telegram library:** raw `httpx` (not `python-telegram-bot`). Only three endpoints needed: `sendMessage`, `getUpdates`, and optionally `editMessageText` (not used in v1 — we send new messages for confirmations and timeouts).
- **Telegram polling:** long-polling via `getUpdates` with `timeout=30`. No webhook (spec §14.1).
- **Correlation token type:** `typing.Any` (opaque to gateway; hashable in practice — `int` for Telegram `message_id`).
- **Request ID:** `uuid.uuid4().hex[:8]` — 8-char hex, plenty unique for dozens of concurrent requests.
- **Timeout default:** 86400 seconds (24h). Spec §9.
- **Timeout return value:** sentinel string `"__TIMEOUT__"`. No exception.
- **Tool error return value:** sentinel string `"ERROR: <message>"`. No exception.
- **Port:** 9876. Binding: 127.0.0.1.
- **MCP endpoint path:** `/sse` (FastMCP default). Full agent-side URL: `http://localhost:9876/sse`.
- **No persistence.** In-memory registry only. Restart = timeouts for in-flight agents.

---

## Task Index

1. Project scaffold + pyproject + `.gitignore`
2. Registry module (`server/registry.py`)
3. JSONL logger (`server/logging_jsonl.py`)
4. Messenger interface (`server/messenger.py`)
5. Config loader (`server/config.py`)
6. Telegram backend — send methods (`server/telegram.py`)
7. Telegram backend — poll loop (`server/telegram.py`)
8. Gateway — `notify_human` (`server/gateway.py`)
9. Gateway — `ask_human` happy path
10. Gateway — `ask_human` timeout + error paths
11. Gateway — dispatch loop
12. Main entry point (`server/main.py`, `server/__main__.py`)
13. Skill file (`skill/SKILL.md`)
14. README + manual smoke test

---

## Task 1: Project scaffold

**Files:**

- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `server/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

This task produces a package that installs cleanly and runs an empty test suite. No production code yet.

- [ ] **Step 1.1: Create `pyproject.toml`**

Write this exact content to `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "switchboard"
version = "0.1.0"
description = "Human-in-the-loop MCP gateway for Claude Code agents"
requires-python = ">=3.11"
dependencies = [
    "mcp[cli]>=1.2",
    "httpx>=0.27",
    "python-dotenv>=1.0",
    "uvicorn>=0.30",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "respx>=0.21",
]

[tool.setuptools.packages.find]
include = ["server*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 1.2: Create `.gitignore`**

```gitignore
# Python
__pycache__/
*.pyc
*.pyo
*.egg-info/
.pytest_cache/
.venv/
venv/
build/
dist/

# Secrets and runtime
.env
logs/

# IDE
.vscode/
.idea/
```

- [ ] **Step 1.3: Create `.env.example`**

```dotenv
# Copy to .env and fill in. OS env vars take precedence over .env.
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Optional — defaults shown
SWITCHBOARD_HOST=127.0.0.1
SWITCHBOARD_PORT=9876
SWITCHBOARD_TIMEOUT_SECONDS=86400
SWITCHBOARD_LOG_PATH=./logs/switchboard.jsonl
```

- [ ] **Step 1.4: Create empty package markers**

`server/__init__.py`:

```python
"""Switchboard — human-in-the-loop MCP gateway."""

__version__ = "0.1.0"
```

`tests/__init__.py`: **empty file** (zero bytes).

- [ ] **Step 1.5: Create `tests/conftest.py`**

```python
"""Shared pytest fixtures."""

import pytest


@pytest.fixture
def anyio_backend():
	"""pytest-asyncio / anyio shim — stick to asyncio only."""
	return "asyncio"
```

- [ ] **Step 1.6: Install and verify**

```bash
python -m venv .venv
source .venv/Scripts/activate   # Git Bash on Windows
pip install -e ".[dev]"
pytest
```

Expected: `collected 0 items` / `no tests ran in ...s`. Install must succeed with no errors.

- [ ] **Step 1.7: Commit boundary (developer to run)**

```bash
git add pyproject.toml .gitignore .env.example server/__init__.py tests/__init__.py tests/conftest.py
git commit -m "chore: project scaffold"
```

---

## Task 2: Registry module

**Files:**

- Create: `server/registry.py`
- Create: `tests/test_registry.py`

The `Registry` holds `PendingRequest` records keyed by `request_id`, plus a secondary index `correlation → request_id` so backends can resolve responses without knowing the request_id. All mutation happens on the single asyncio loop; no locks.

- [ ] **Step 2.1: Write failing tests**

Create `tests/test_registry.py` with this exact content:

```python
"""Tests for the pending-request registry."""

import asyncio

import pytest

from server.registry import PendingRequest, Registry


@pytest.mark.asyncio
async def test_add_returns_future_and_stores_record():
	registry = Registry()
	future = registry.add("abc123", "IR2", correlation=42)
	assert isinstance(future, asyncio.Future)
	record = registry.get("abc123")
	assert isinstance(record, PendingRequest)
	assert record.request_id == "abc123"
	assert record.agent_id == "IR2"
	assert record.correlation == 42
	assert record.future is future


@pytest.mark.asyncio
async def test_resolve_by_correlation_sets_future_result_and_returns_request_id():
	registry = Registry()
	future = registry.add("abc123", "IR2", correlation=42)
	request_id = registry.resolve_by_correlation(42, "yes")
	assert request_id == "abc123"
	assert future.done()
	assert future.result() == "yes"


@pytest.mark.asyncio
async def test_resolve_by_correlation_returns_none_for_unknown_correlation():
	registry = Registry()
	registry.add("abc123", "IR2", correlation=42)
	assert registry.resolve_by_correlation(999, "late") is None


@pytest.mark.asyncio
async def test_resolve_removes_entry_from_pending():
	registry = Registry()
	registry.add("abc123", "IR2", correlation=42)
	registry.resolve_by_correlation(42, "yes")
	assert registry.get("abc123") is None


@pytest.mark.asyncio
async def test_remove_drops_both_indexes():
	registry = Registry()
	registry.add("abc123", "IR2", correlation=42)
	registry.remove("abc123")
	assert registry.get("abc123") is None
	assert registry.resolve_by_correlation(42, "late") is None


@pytest.mark.asyncio
async def test_multiple_pending_are_independent():
	registry = Registry()
	f1 = registry.add("a", "IR2", correlation=1)
	f2 = registry.add("b", "DMX", correlation=2)
	registry.resolve_by_correlation(2, "answer-b")
	assert f2.done() and f2.result() == "answer-b"
	assert not f1.done()
	registry.resolve_by_correlation(1, "answer-a")
	assert f1.done() and f1.result() == "answer-a"
```

- [ ] **Step 2.2: Run tests to verify they fail**

```bash
pytest tests/test_registry.py -v
```

Expected: `ModuleNotFoundError: No module named 'server.registry'` — all tests fail at import.

- [ ] **Step 2.3: Implement the registry**

Create `server/registry.py`:

```python
"""In-memory pending-request registry.

All access happens on a single asyncio event loop, so no locking is required.
The secondary correlation index lets a messenger backend resolve a response
using whatever opaque token it stored at send time (Telegram message_id,
Firebase doc path, etc.) without knowing the request_id.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class PendingRequest:
	request_id: str
	agent_id: str
	correlation: Any
	future: asyncio.Future[str]
	created_at: datetime = field(
		default_factory=lambda: datetime.now(timezone.utc)
	)


class Registry:
	def __init__(self) -> None:
		self._pending: dict[str, PendingRequest] = {}
		self._by_correlation: dict[Any, str] = {}

	def add(
		self, request_id: str, agent_id: str, correlation: Any
	) -> asyncio.Future[str]:
		loop = asyncio.get_running_loop()
		future: asyncio.Future[str] = loop.create_future()
		self._pending[request_id] = PendingRequest(
			request_id=request_id,
			agent_id=agent_id,
			correlation=correlation,
			future=future,
		)
		self._by_correlation[correlation] = request_id
		return future

	def get(self, request_id: str) -> PendingRequest | None:
		return self._pending.get(request_id)

	def resolve_by_correlation(
		self, correlation: Any, text: str
	) -> str | None:
		request_id = self._by_correlation.pop(correlation, None)
		if request_id is None:
			return None
		record = self._pending.pop(request_id, None)
		if record is None:
			return None
		if not record.future.done():
			record.future.set_result(text)
		return request_id

	def remove(self, request_id: str) -> None:
		record = self._pending.pop(request_id, None)
		if record is not None:
			self._by_correlation.pop(record.correlation, None)
```

- [ ] **Step 2.4: Run tests to verify they pass**

```bash
pytest tests/test_registry.py -v
```

Expected: `6 passed`.

- [ ] **Step 2.5: Commit boundary (developer)**

```bash
git add server/registry.py tests/test_registry.py
git commit -m "feat(registry): pending-request registry with correlation index"
```

---

## Task 3: JSONL logger

**Files:**

- Create: `server/logging_jsonl.py`
- Create: `tests/test_logging_jsonl.py`

Lightweight audit log. One event per line. Mirrors to stderr at INFO level for live tailing.

- [ ] **Step 3.1: Write failing tests**

Create `tests/test_logging_jsonl.py`:

```python
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
	assert ev["agent_id"] == "IR2"
	assert ev["question_preview"].startswith("Overwrite foo.java?")
	assert "ts" in ev


def test_request_resolved_records_duration_and_source(tmp_path):
	logger = JsonlLogger(tmp_path / "log.jsonl")
	logger.request_resolved(
		"a3f1", "IR2", response_text="yes", source="telegram", duration_ms=123
	)
	ev = read_events(tmp_path / "log.jsonl")[0]
	assert ev["event"] == "request_resolved"
	assert ev["response_preview"] == "yes"
	assert ev["source"] == "telegram"
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
```

- [ ] **Step 3.2: Run tests to verify they fail**

```bash
pytest tests/test_logging_jsonl.py -v
```

Expected: `ModuleNotFoundError: No module named 'server.logging_jsonl'`.

- [ ] **Step 3.3: Implement the logger**

Create `server/logging_jsonl.py`:

```python
"""JSONL audit logger for Switchboard."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_stderr_logger = logging.getLogger("switchboard")
if not _stderr_logger.handlers:
	handler = logging.StreamHandler()
	handler.setFormatter(
		logging.Formatter("%(asctime)s %(levelname)s %(message)s")
	)
	_stderr_logger.addHandler(handler)
	_stderr_logger.setLevel(logging.INFO)


def _preview(text: str, limit: int = 100) -> str:
	if len(text) <= limit:
		return text
	return text[:limit]


class JsonlLogger:
	def __init__(self, path: str | Path) -> None:
		self._path = Path(path)
		self._path.parent.mkdir(parents=True, exist_ok=True)

	def _write(self, event: dict[str, Any]) -> None:
		event["ts"] = datetime.now(timezone.utc).isoformat()
		line = json.dumps(event, ensure_ascii=False)
		with self._path.open("a", encoding="utf-8") as fh:
			fh.write(line + "\n")
		_stderr_logger.info(line)

	def request_created(
		self, request_id: str, agent_id: str, question: str
	) -> None:
		self._write({
			"event": "request_created",
			"request_id": request_id,
			"agent_id": agent_id,
			"question_preview": _preview(question),
		})

	def request_resolved(
		self,
		request_id: str,
		agent_id: str,
		response_text: str,
		source: str,
		duration_ms: int,
	) -> None:
		self._write({
			"event": "request_resolved",
			"request_id": request_id,
			"agent_id": agent_id,
			"response_preview": _preview(response_text),
			"source": source,
			"duration_ms": duration_ms,
		})

	def notify_sent(self, agent_id: str, message: str) -> None:
		self._write({
			"event": "notify_sent",
			"agent_id": agent_id,
			"message_preview": _preview(message),
		})

	def timeout(
		self, request_id: str, agent_id: str, timeout_seconds: int
	) -> None:
		self._write({
			"event": "timeout",
			"request_id": request_id,
			"agent_id": agent_id,
			"timeout_seconds": timeout_seconds,
		})

	def tool_error(
		self, request_id: str | None, agent_id: str | None, error: str
	) -> None:
		self._write({
			"event": "tool_error",
			"request_id": request_id,
			"agent_id": agent_id,
			"error": error,
		})

	def surface_error(self, detail: str, correlation: str | None = None) -> None:
		self._write({
			"event": "surface_error",
			"detail": detail,
			"correlation": correlation,
		})
```

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
pytest tests/test_logging_jsonl.py -v
```

Expected: `6 passed`.

- [ ] **Step 3.5: Commit boundary (developer)**

```bash
git add server/logging_jsonl.py tests/test_logging_jsonl.py
git commit -m "feat(logging): JSONL audit logger"
```

---

## Task 4: Messenger interface

**Files:**

- Create: `server/messenger.py`
- Create: `tests/test_messenger_contract.py`

Defines the abstract base class plus `IncomingResponse` dataclass. No concrete implementation yet — that is Task 6/7.

- [ ] **Step 4.1: Write failing tests**

Create `tests/test_messenger_contract.py`:

```python
"""Contract tests for the MessengerBackend interface."""

import inspect

import pytest

from server.messenger import IncomingResponse, MessengerBackend


def test_incoming_response_is_simple_dataclass():
	r = IncomingResponse(correlation=42, text="yes")
	assert r.correlation == 42
	assert r.text == "yes"


def test_messenger_backend_is_abstract():
	with pytest.raises(TypeError):
		MessengerBackend()  # type: ignore[abstract]


def test_messenger_backend_declares_required_methods():
	expected = {
		"send_question",
		"send_notification",
		"send_timeout_followup",
		"send_resolution_confirmation",
		"poll_responses",
	}
	declared = {
		name
		for name, member in inspect.getmembers(MessengerBackend)
		if getattr(member, "__isabstractmethod__", False)
	}
	assert expected <= declared
```

- [ ] **Step 4.2: Run tests to verify they fail**

```bash
pytest tests/test_messenger_contract.py -v
```

Expected: `ModuleNotFoundError: No module named 'server.messenger'`.

- [ ] **Step 4.3: Implement the interface**

Create `server/messenger.py`:

```python
"""MessengerBackend abstract interface and shared types.

The messenger surface is abstracted so the transport (Telegram now, Firebase
later) can evolve without touching the gateway core. Concrete impls live in
their own modules (e.g. `server/telegram.py`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator

CorrelationToken = Any


@dataclass
class IncomingResponse:
	"""A response arriving from the messenger backend.

	`correlation` is whatever opaque token the backend stored at
	`send_question` time (e.g. Telegram message_id). The gateway uses it
	to look up the pending request_id in the registry.
	"""

	correlation: CorrelationToken
	text: str


class MessengerBackend(ABC):
	@abstractmethod
	async def send_question(
		self, request_id: str, agent_id: str, question: str
	) -> CorrelationToken:
		"""Deliver the question. Return a backend-specific token that
		will be matched against `IncomingResponse.correlation` later."""

	@abstractmethod
	async def send_notification(self, agent_id: str, message: str) -> None:
		"""Fire-and-forget status update; no reply tracking."""

	@abstractmethod
	async def send_timeout_followup(
		self,
		request_id: str,
		agent_id: str,
		timeout_seconds: int,
		correlation: CorrelationToken,
	) -> None:
		"""Inform the developer a pending question has timed out."""

	@abstractmethod
	async def send_resolution_confirmation(
		self,
		request_id: str,
		agent_id: str,
		correlation: CorrelationToken,
	) -> None:
		"""Confirm to the developer that their response was received."""

	@abstractmethod
	def poll_responses(self) -> AsyncIterator[IncomingResponse]:
		"""Yield IncomingResponse as replies arrive. Infinite async
		iterator; the caller cancels the task to stop polling."""
```

- [ ] **Step 4.4: Run tests to verify they pass**

```bash
pytest tests/test_messenger_contract.py -v
```

Expected: `3 passed`.

- [ ] **Step 4.5: Commit boundary (developer)**

```bash
git add server/messenger.py tests/test_messenger_contract.py
git commit -m "feat(messenger): backend interface and IncomingResponse"
```

---

## Task 5: Config loader

**Files:**

- Create: `server/config.py`
- Create: `tests/test_config.py`

Loads settings from OS env vars, with `.env` as a fallback if the env is not already populated. Validates required fields.

- [ ] **Step 5.1: Write failing tests**

Create `tests/test_config.py`:

```python
"""Tests for env-based config loading."""

import os
from pathlib import Path

import pytest

from server.config import Config, ConfigError, load_config


def _clear_env(monkeypatch):
	for key in [
		"TELEGRAM_BOT_TOKEN",
		"TELEGRAM_CHAT_ID",
		"SWITCHBOARD_HOST",
		"SWITCHBOARD_PORT",
		"SWITCHBOARD_TIMEOUT_SECONDS",
		"SWITCHBOARD_LOG_PATH",
	]:
		monkeypatch.delenv(key, raising=False)


def test_loads_minimum_required_fields(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
	monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
	cfg = load_config(dotenv_path=tmp_path / "does-not-exist.env")
	assert isinstance(cfg, Config)
	assert cfg.telegram_bot_token == "tok"
	assert cfg.telegram_chat_id == "123"
	assert cfg.host == "127.0.0.1"
	assert cfg.port == 9876
	assert cfg.timeout_seconds == 86400


def test_raises_when_required_missing(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	with pytest.raises(ConfigError):
		load_config(dotenv_path=tmp_path / "does-not-exist.env")


def test_overrides_via_env(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
	monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
	monkeypatch.setenv("SWITCHBOARD_PORT", "9000")
	monkeypatch.setenv("SWITCHBOARD_TIMEOUT_SECONDS", "60")
	cfg = load_config(dotenv_path=tmp_path / "does-not-exist.env")
	assert cfg.port == 9000
	assert cfg.timeout_seconds == 60


def test_dotenv_used_when_env_unset(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	env_file = tmp_path / ".env"
	env_file.write_text(
		"TELEGRAM_BOT_TOKEN=from-dotenv\nTELEGRAM_CHAT_ID=999\n"
	)
	cfg = load_config(dotenv_path=env_file)
	assert cfg.telegram_bot_token == "from-dotenv"
	assert cfg.telegram_chat_id == "999"


def test_os_env_wins_over_dotenv(monkeypatch, tmp_path):
	_clear_env(monkeypatch)
	monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "os-wins")
	env_file = tmp_path / ".env"
	env_file.write_text(
		"TELEGRAM_BOT_TOKEN=from-dotenv\nTELEGRAM_CHAT_ID=999\n"
	)
	cfg = load_config(dotenv_path=env_file)
	assert cfg.telegram_bot_token == "os-wins"
	assert cfg.telegram_chat_id == "999"
```

- [ ] **Step 5.2: Run tests to verify they fail**

```bash
pytest tests/test_config.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 5.3: Implement the config**

Create `server/config.py`:

```python
"""Env-based configuration for Switchboard.

OS env vars are the source of truth. A .env file is loaded as a fallback —
values already in the OS env win over .env (python-dotenv's default
`override=False` behavior).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
	pass


@dataclass(frozen=True)
class Config:
	telegram_bot_token: str
	telegram_chat_id: str
	host: str
	port: int
	timeout_seconds: int
	log_path: str


def _require(name: str) -> str:
	value = os.environ.get(name)
	if not value:
		raise ConfigError(f"Missing required env var: {name}")
	return value


def load_config(dotenv_path: str | Path | None = None) -> Config:
	if dotenv_path is None:
		dotenv_path = Path.cwd() / ".env"
	dotenv_path = Path(dotenv_path)
	if dotenv_path.exists():
		load_dotenv(dotenv_path, override=False)

	return Config(
		telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
		telegram_chat_id=_require("TELEGRAM_CHAT_ID"),
		host=os.environ.get("SWITCHBOARD_HOST", "127.0.0.1"),
		port=int(os.environ.get("SWITCHBOARD_PORT", "9876")),
		timeout_seconds=int(
			os.environ.get("SWITCHBOARD_TIMEOUT_SECONDS", "86400")
		),
		log_path=os.environ.get(
			"SWITCHBOARD_LOG_PATH", "./logs/switchboard.jsonl"
		),
	)
```

- [ ] **Step 5.4: Run tests to verify they pass**

```bash
pytest tests/test_config.py -v
```

Expected: `5 passed`.

- [ ] **Step 5.5: Commit boundary (developer)**

```bash
git add server/config.py tests/test_config.py
git commit -m "feat(config): env-based config loader with dotenv fallback"
```

---

## Task 6: Telegram backend — send methods

**Files:**

- Create: `server/telegram.py`
- Create: `tests/test_telegram_send.py`

The four send methods (`send_question`, `send_notification`, `send_timeout_followup`, `send_resolution_confirmation`). Polling is split out into Task 7 to keep each test file focused.

- [ ] **Step 6.1: Write failing tests**

Create `tests/test_telegram_send.py`:

```python
"""Tests for the Telegram backend's outbound send methods."""

import httpx
import pytest
import respx

from server.telegram import TelegramBackend

BASE = "https://api.telegram.org/bottok"
CHAT_ID = "123"


@pytest.fixture
async def backend():
	async with httpx.AsyncClient() as client:
		yield TelegramBackend(
			token="tok", chat_id=CHAT_ID, http_client=client
		)


@respx.mock
@pytest.mark.asyncio
async def test_send_question_posts_sendmessage_and_returns_message_id(backend):
	route = respx.post(f"{BASE}/sendMessage").mock(
		return_value=httpx.Response(
			200, json={"ok": True, "result": {"message_id": 777}}
		)
	)
	correlation = await backend.send_question(
		"a3f1", "IR2", "Overwrite foo.java?"
	)
	assert correlation == 777
	assert route.called
	body = route.calls.last.request.read().decode()
	assert "Overwrite foo.java?" in body
	assert "IR2" in body
	assert "a3f1" in body
	assert CHAT_ID in body


@respx.mock
@pytest.mark.asyncio
async def test_send_question_raises_on_http_error(backend):
	respx.post(f"{BASE}/sendMessage").mock(
		return_value=httpx.Response(500, text="boom")
	)
	with pytest.raises(httpx.HTTPStatusError):
		await backend.send_question("a3f1", "IR2", "q")


@respx.mock
@pytest.mark.asyncio
async def test_send_notification_posts_with_info_prefix(backend):
	route = respx.post(f"{BASE}/sendMessage").mock(
		return_value=httpx.Response(
			200, json={"ok": True, "result": {"message_id": 1}}
		)
	)
	await backend.send_notification("IR2", "starting migration")
	assert route.called
	body = route.calls.last.request.read().decode()
	assert "IR2" in body
	assert "starting migration" in body


@respx.mock
@pytest.mark.asyncio
async def test_send_timeout_followup_uses_reply_to(backend):
	route = respx.post(f"{BASE}/sendMessage").mock(
		return_value=httpx.Response(
			200, json={"ok": True, "result": {"message_id": 2}}
		)
	)
	await backend.send_timeout_followup(
		"a3f1", "IR2", timeout_seconds=86400, correlation=777
	)
	assert route.called
	body = route.calls.last.request.read().decode()
	assert '"reply_to_message_id": 777' in body or '"reply_to_message_id":777' in body
	assert "24h" in body


@respx.mock
@pytest.mark.asyncio
async def test_send_resolution_confirmation_uses_reply_to(backend):
	route = respx.post(f"{BASE}/sendMessage").mock(
		return_value=httpx.Response(
			200, json={"ok": True, "result": {"message_id": 3}}
		)
	)
	await backend.send_resolution_confirmation(
		"a3f1", "IR2", correlation=777
	)
	assert route.called
	body = route.calls.last.request.read().decode()
	assert '"reply_to_message_id": 777' in body or '"reply_to_message_id":777' in body
	assert "answered" in body
```

- [ ] **Step 6.2: Run tests to verify they fail**

```bash
pytest tests/test_telegram_send.py -v
```

Expected: `ModuleNotFoundError: No module named 'server.telegram'`.

- [ ] **Step 6.3: Implement the send side of the backend**

Create `server/telegram.py` (the poll loop in Task 7 will be appended to this file):

```python
"""Telegram MessengerBackend implementation using raw httpx."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import httpx

from server.messenger import CorrelationToken, IncomingResponse, MessengerBackend


class TelegramBackend(MessengerBackend):
	BASE_URL = "https://api.telegram.org"

	def __init__(
		self,
		token: str,
		chat_id: str,
		http_client: httpx.AsyncClient | None = None,
	) -> None:
		self._token = token
		self._chat_id = chat_id
		self._client = http_client or httpx.AsyncClient(
			timeout=httpx.Timeout(35.0)
		)
		self._owns_client = http_client is None
		self._offset: int | None = None

	@property
	def _base(self) -> str:
		return f"{self.BASE_URL}/bot{self._token}"

	async def aclose(self) -> None:
		if self._owns_client:
			await self._client.aclose()

	async def _post_send_message(self, payload: dict) -> dict:
		payload = {"chat_id": self._chat_id, **payload}
		resp = await self._client.post(
			f"{self._base}/sendMessage", json=payload
		)
		resp.raise_for_status()
		return resp.json()["result"]

	async def send_question(
		self, request_id: str, agent_id: str, question: str
	) -> CorrelationToken:
		text = (
			f"[{agent_id} | {request_id}] {question}\n\n"
			"Reply to this message to answer."
		)
		result = await self._post_send_message({"text": text})
		return int(result["message_id"])

	async def send_notification(self, agent_id: str, message: str) -> None:
		text = f"ℹ️ [{agent_id}] {message}"
		await self._post_send_message({"text": text})

	async def send_timeout_followup(
		self,
		request_id: str,
		agent_id: str,
		timeout_seconds: int,
		correlation: CorrelationToken,
	) -> None:
		hours = max(1, timeout_seconds // 3600)
		text = (
			f"⏱️ [{agent_id} | {request_id}] timed out after {hours}h. "
			"Agent received timeout signal."
		)
		await self._post_send_message({
			"text": text,
			"reply_to_message_id": int(correlation),
		})

	async def send_resolution_confirmation(
		self,
		request_id: str,
		agent_id: str,
		correlation: CorrelationToken,
	) -> None:
		text = f"✅ [{agent_id} | {request_id}] answered"
		await self._post_send_message({
			"text": text,
			"reply_to_message_id": int(correlation),
		})

	async def poll_responses(self) -> AsyncIterator[IncomingResponse]:
		# Implemented in Task 7.
		raise NotImplementedError
		yield  # pragma: no cover — marks this as an async generator
```

- [ ] **Step 6.4: Run tests to verify they pass**

```bash
pytest tests/test_telegram_send.py -v
```

Expected: `5 passed`.

- [ ] **Step 6.5: Commit boundary (developer)**

```bash
git add server/telegram.py tests/test_telegram_send.py
git commit -m "feat(telegram): outbound send methods via httpx"
```

---

## Task 7: Telegram backend — poll loop

**Files:**

- Modify: `server/telegram.py` (replace `poll_responses` stub)
- Create: `tests/test_telegram_poll.py`

Long-poll `getUpdates` with an offset cursor. Yield `IncomingResponse(correlation=reply_to_message_id, text=text)` for any update that is a reply. Ignore updates without a `reply_to_message`. On transient errors, sleep briefly and retry.

- [ ] **Step 7.1: Write failing tests**

Create `tests/test_telegram_poll.py`:

```python
"""Tests for the Telegram backend's poll_responses async generator."""

import asyncio

import httpx
import pytest
import respx

from server.messenger import IncomingResponse
from server.telegram import TelegramBackend

BASE = "https://api.telegram.org/bottok"


def _update(update_id: int, reply_to: int | None, text: str) -> dict:
	msg = {"message_id": update_id + 1000, "text": text}
	if reply_to is not None:
		msg["reply_to_message"] = {"message_id": reply_to}
	return {"update_id": update_id, "message": msg}


@pytest.fixture
async def backend():
	async with httpx.AsyncClient() as client:
		yield TelegramBackend(token="tok", chat_id="123", http_client=client)


@respx.mock
@pytest.mark.asyncio
async def test_yields_incoming_response_for_reply(backend):
	respx.get(f"{BASE}/getUpdates").mock(
		return_value=httpx.Response(
			200,
			json={"ok": True, "result": [_update(1, reply_to=777, text="yes")]},
		)
	)
	agen = backend.poll_responses()
	response = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
	assert isinstance(response, IncomingResponse)
	assert response.correlation == 777
	assert response.text == "yes"


@respx.mock
@pytest.mark.asyncio
async def test_skips_updates_without_reply_to(backend):
	respx.get(f"{BASE}/getUpdates").mock(
		side_effect=[
			httpx.Response(
				200,
				json={
					"ok": True,
					"result": [
						_update(1, reply_to=None, text="hello"),
						_update(2, reply_to=777, text="yes"),
					],
				},
			),
		]
	)
	agen = backend.poll_responses()
	response = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
	assert response.correlation == 777


@respx.mock
@pytest.mark.asyncio
async def test_advances_offset_between_polls(backend):
	first = httpx.Response(
		200,
		json={"ok": True, "result": [_update(5, reply_to=777, text="a")]},
	)
	second = httpx.Response(
		200,
		json={"ok": True, "result": [_update(9, reply_to=888, text="b")]},
	)
	route = respx.get(f"{BASE}/getUpdates").mock(side_effect=[first, second])
	agen = backend.poll_responses()
	r1 = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
	r2 = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
	assert r1.text == "a"
	assert r2.text == "b"
	# Second call must include offset=6 (5+1).
	second_url = str(route.calls[1].request.url)
	assert "offset=6" in second_url
```

- [ ] **Step 7.2: Run tests to verify they fail**

```bash
pytest tests/test_telegram_poll.py -v
```

Expected: all three fail with `NotImplementedError` from the Task 6 stub.

- [ ] **Step 7.3: Replace the poll_responses stub**

In `server/telegram.py`, replace the `poll_responses` method (the `NotImplementedError` stub at the bottom) with this implementation:

```python
	async def poll_responses(self) -> AsyncIterator[IncomingResponse]:
		while True:
			try:
				params: dict[str, int | str] = {"timeout": 30}
				if self._offset is not None:
					params["offset"] = self._offset
				resp = await self._client.get(
					f"{self._base}/getUpdates",
					params=params,
					timeout=35.0,
				)
				resp.raise_for_status()
				data = resp.json()
				for update in data.get("result", []):
					self._offset = int(update["update_id"]) + 1
					msg = update.get("message")
					if not msg:
						continue
					reply = msg.get("reply_to_message")
					if not reply:
						continue
					yield IncomingResponse(
						correlation=int(reply["message_id"]),
						text=msg.get("text", ""),
					)
			except (httpx.HTTPError, KeyError, ValueError):
				await asyncio.sleep(2.0)
```

- [ ] **Step 7.4: Run tests to verify they pass**

```bash
pytest tests/test_telegram_poll.py tests/test_telegram_send.py -v
```

Expected: `8 passed` (5 from Task 6 + 3 from Task 7).

- [ ] **Step 7.5: Commit boundary (developer)**

```bash
git add server/telegram.py tests/test_telegram_poll.py
git commit -m "feat(telegram): long-poll getUpdates with correlation extraction"
```

---

## Task 8: Gateway — `notify_human`

**Files:**

- Create: `server/gateway.py`
- Create: `tests/test_gateway_notify_human.py`

Start the gateway module with the simpler tool first. `notify_human` is fire-and-forget: call backend, log, return `"ok"`.

- [ ] **Step 8.1: Write failing tests**

Create `tests/test_gateway_notify_human.py`:

```python
"""Tests for the notify_human tool handler."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.messenger import IncomingResponse, MessengerBackend
from server.registry import Registry


class RecordingBackend(MessengerBackend):
	def __init__(self) -> None:
		self.sent_questions: list[tuple[str, str, str]] = []
		self.sent_notifications: list[tuple[str, str]] = []
		self.sent_timeouts: list[tuple[str, str, int, Any]] = []
		self.sent_confirmations: list[tuple[str, str, Any]] = []
		self._next_correlation = 1000

	async def send_question(self, request_id, agent_id, question):
		correlation = self._next_correlation
		self._next_correlation += 1
		self.sent_questions.append((request_id, agent_id, question))
		return correlation

	async def send_notification(self, agent_id, message):
		self.sent_notifications.append((agent_id, message))

	async def send_timeout_followup(
		self, request_id, agent_id, timeout_seconds, correlation
	):
		self.sent_timeouts.append(
			(request_id, agent_id, timeout_seconds, correlation)
		)

	async def send_resolution_confirmation(
		self, request_id, agent_id, correlation
	):
		self.sent_confirmations.append((request_id, agent_id, correlation))

	async def poll_responses(self) -> AsyncIterator[IncomingResponse]:
		if False:
			yield  # pragma: no cover
		return


@pytest.fixture
def cfg(tmp_path):
	return Config(
		telegram_bot_token="tok",
		telegram_chat_id="123",
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.fixture
def logger(cfg, tmp_path):
	return JsonlLogger(cfg.log_path)


@pytest.mark.asyncio
async def test_notify_human_calls_backend_and_returns_ok(cfg, logger):
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.notify_human("starting migration", "IR2")

	assert result == "ok"
	assert backend.sent_notifications == [("IR2", "starting migration")]
```

- [ ] **Step 8.2: Run tests to verify they fail**

```bash
pytest tests/test_gateway_notify_human.py -v
```

Expected: `ModuleNotFoundError: No module named 'server.gateway'`.

- [ ] **Step 8.3: Start the gateway module**

Create `server/gateway.py`:

```python
"""FastMCP tool handlers and response-dispatch loop.

`build_tool_handlers` returns a small object with the two tool coroutines
bound to the provided dependencies. `build_gateway` wires those into a
FastMCP instance. Keeping the handlers separable from the FastMCP wiring
makes them trivially unit-testable without spinning up an MCP server.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Coroutine

from server.config import Config
from server.logging_jsonl import JsonlLogger
from server.messenger import MessengerBackend
from server.registry import Registry

TIMEOUT_SENTINEL = "__TIMEOUT__"


def _new_request_id() -> str:
	return uuid.uuid4().hex[:8]


@dataclass
class ToolHandlers:
	ask_human: Callable[[str, str], Coroutine[None, None, str]]
	notify_human: Callable[[str, str], Coroutine[None, None, str]]


def build_tool_handlers(
	config: Config,
	registry: Registry,
	backend: MessengerBackend,
	logger: JsonlLogger,
) -> ToolHandlers:
	async def notify_human(message: str, agent_id: str) -> str:
		await backend.send_notification(agent_id, message)
		logger.notify_sent(agent_id, message)
		return "ok"

	async def ask_human(question: str, agent_id: str) -> str:
		# Implemented in Task 9/10.
		raise NotImplementedError

	return ToolHandlers(ask_human=ask_human, notify_human=notify_human)
```

- [ ] **Step 8.4: Run tests to verify they pass**

```bash
pytest tests/test_gateway_notify_human.py -v
```

Expected: `1 passed`.

- [ ] **Step 8.5: Commit boundary (developer)**

```bash
git add server/gateway.py tests/test_gateway_notify_human.py
git commit -m "feat(gateway): notify_human handler"
```

---

## Task 9: Gateway — `ask_human` happy path

**Files:**

- Modify: `server/gateway.py` (replace `ask_human` stub)
- Create: `tests/test_gateway_ask_human.py`

Mint request_id, send question via backend, register pending Future, await resolution, call backend.send_resolution_confirmation, return text. Timeout and error paths come in Task 10.

- [ ] **Step 9.1: Write failing tests**

Create `tests/test_gateway_ask_human.py`:

```python
"""Happy-path test for the ask_human tool handler."""

import asyncio

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from tests.test_gateway_notify_human import RecordingBackend


@pytest.fixture
def cfg(tmp_path):
	return Config(
		telegram_bot_token="tok",
		telegram_chat_id="123",
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.fixture
def logger(cfg):
	return JsonlLogger(cfg.log_path)


@pytest.mark.asyncio
async def test_ask_human_returns_response_when_resolved(cfg, logger):
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	# Fire ask_human as a task; resolve it after a moment.
	task = asyncio.create_task(handlers.ask_human("Overwrite foo?", "IR2"))
	# Give the handler a tick to register the pending request.
	await asyncio.sleep(0)
	# RecordingBackend assigns correlation 1000 to the first question.
	resolved = registry.resolve_by_correlation(1000, "yes")
	assert resolved is not None
	result = await asyncio.wait_for(task, timeout=1.0)
	assert result == "yes"
	# Backend was asked to send the question and the confirmation.
	assert len(backend.sent_questions) == 1
	assert len(backend.sent_confirmations) == 1
	# Confirmation carries the same correlation.
	_, _, correlation = backend.sent_confirmations[0]
	assert correlation == 1000
```

- [ ] **Step 9.2: Run test to verify it fails**

```bash
pytest tests/test_gateway_ask_human.py -v
```

Expected: `NotImplementedError` from the stub.

- [ ] **Step 9.3: Replace the ask_human stub**

In `server/gateway.py`, replace the `ask_human` inner coroutine inside `build_tool_handlers` with this (the timeout/error branches will be filled in during Task 10):

```python
	async def ask_human(question: str, agent_id: str) -> str:
		request_id = _new_request_id()
		started = datetime.now(timezone.utc)
		correlation = await backend.send_question(
			request_id, agent_id, question
		)
		future = registry.add(request_id, agent_id, correlation)
		logger.request_created(request_id, agent_id, question)
		result = await future
		duration_ms = int(
			(datetime.now(timezone.utc) - started).total_seconds() * 1000
		)
		logger.request_resolved(
			request_id,
			agent_id,
			response_text=result,
			source="telegram",
			duration_ms=duration_ms,
		)
		await backend.send_resolution_confirmation(
			request_id, agent_id, correlation
		)
		return result
```

- [ ] **Step 9.4: Run test to verify it passes**

```bash
pytest tests/test_gateway_ask_human.py tests/test_gateway_notify_human.py -v
```

Expected: `2 passed`.

- [ ] **Step 9.5: Commit boundary (developer)**

```bash
git add server/gateway.py tests/test_gateway_ask_human.py
git commit -m "feat(gateway): ask_human happy path"
```

---

## Task 10: Gateway — `ask_human` timeout + error paths

**Files:**

- Modify: `server/gateway.py` (wrap `await future` in timeout and error handling)
- Create: `tests/test_gateway_timeout.py`

- [ ] **Step 10.1: Write failing tests**

Create `tests/test_gateway_timeout.py`:

```python
"""Timeout and error-path tests for ask_human."""

import pytest

from server.config import Config
from server.gateway import TIMEOUT_SENTINEL, build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from tests.test_gateway_notify_human import RecordingBackend


@pytest.fixture
def cfg(tmp_path):
	return Config(
		telegram_bot_token="tok",
		telegram_chat_id="123",
		host="127.0.0.1",
		port=9876,
		timeout_seconds=0,  # immediate timeout for the test
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.fixture
def logger(cfg):
	return JsonlLogger(cfg.log_path)


@pytest.mark.asyncio
async def test_ask_human_returns_sentinel_on_timeout(cfg, logger):
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.ask_human("Overwrite foo?", "IR2")

	assert result == TIMEOUT_SENTINEL
	# Backend was asked to send a timeout follow-up.
	assert len(backend.sent_timeouts) == 1
	assert backend.sent_timeouts[0][0] == backend.sent_questions[0][0]
	# Registry entry is cleaned up.
	assert registry.resolve_by_correlation(1000, "late") is None


class BrokenBackend(RecordingBackend):
	async def send_question(self, request_id, agent_id, question):
		raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_ask_human_returns_error_sentinel_on_backend_failure(cfg, logger):
	backend = BrokenBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.ask_human("q", "IR2")

	assert result.startswith("ERROR:")
	assert "boom" in result
```

- [ ] **Step 10.2: Run tests to verify they fail**

```bash
pytest tests/test_gateway_timeout.py -v
```

Expected: both fail — happy-path handler does not time out or catch errors.

- [ ] **Step 10.3: Rewrite `ask_human` with timeout and error branches**

In `server/gateway.py`, replace the `ask_human` body (from Task 9) with this expanded version:

```python
	async def ask_human(question: str, agent_id: str) -> str:
		request_id = _new_request_id()
		started = datetime.now(timezone.utc)
		correlation = None
		try:
			correlation = await backend.send_question(
				request_id, agent_id, question
			)
			future = registry.add(request_id, agent_id, correlation)
			logger.request_created(request_id, agent_id, question)
		except Exception as exc:
			logger.tool_error(request_id, agent_id, str(exc))
			return f"ERROR: {exc}"

		try:
			result = await asyncio.wait_for(
				future, timeout=config.timeout_seconds
			)
		except asyncio.TimeoutError:
			logger.timeout(request_id, agent_id, config.timeout_seconds)
			registry.remove(request_id)
			try:
				await backend.send_timeout_followup(
					request_id,
					agent_id,
					config.timeout_seconds,
					correlation,
				)
			except Exception as exc:
				logger.surface_error(
					f"timeout_followup_failed: {exc}",
					correlation=str(correlation),
				)
			return TIMEOUT_SENTINEL
		except Exception as exc:
			logger.tool_error(request_id, agent_id, str(exc))
			registry.remove(request_id)
			return f"ERROR: {exc}"

		duration_ms = int(
			(datetime.now(timezone.utc) - started).total_seconds() * 1000
		)
		logger.request_resolved(
			request_id,
			agent_id,
			response_text=result,
			source="telegram",
			duration_ms=duration_ms,
		)
		try:
			await backend.send_resolution_confirmation(
				request_id, agent_id, correlation
			)
		except Exception as exc:
			logger.surface_error(
				f"resolution_confirmation_failed: {exc}",
				correlation=str(correlation),
			)
		return result
```

- [ ] **Step 10.4: Run all gateway tests**

```bash
pytest tests/test_gateway_timeout.py tests/test_gateway_ask_human.py tests/test_gateway_notify_human.py -v
```

Expected: `4 passed`.

- [ ] **Step 10.5: Commit boundary (developer)**

```bash
git add server/gateway.py tests/test_gateway_timeout.py
git commit -m "feat(gateway): ask_human timeout + error handling"
```

---

## Task 11: Gateway — dispatch loop

**Files:**

- Modify: `server/gateway.py` (add `dispatch_responses` coroutine at module level)
- Add tests to: `tests/test_gateway_ask_human.py`

Consumes `backend.poll_responses()` and routes each incoming response to the registry. Logs unknown correlations.

- [ ] **Step 11.1: Add the new test**

Append this test to `tests/test_gateway_ask_human.py`:

```python
from server.gateway import dispatch_responses
from server.messenger import IncomingResponse


class YieldingBackend(RecordingBackend):
	def __init__(self, responses):
		super().__init__()
		self._responses = list(responses)

	async def poll_responses(self):
		for r in self._responses:
			yield r
		# Then hang so the dispatch task does not exit on its own.
		await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_dispatch_loop_routes_responses_to_registry(cfg, logger):
	registry = Registry()
	backend = YieldingBackend([IncomingResponse(correlation=1000, text="yes")])
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	ask_task = asyncio.create_task(handlers.ask_human("q", "IR2"))
	await asyncio.sleep(0)  # let ask_human register
	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, backend, logger)
	)

	try:
		result = await asyncio.wait_for(ask_task, timeout=1.0)
		assert result == "yes"
	finally:
		dispatch_task.cancel()
		try:
			await dispatch_task
		except asyncio.CancelledError:
			pass


@pytest.mark.asyncio
async def test_dispatch_loop_logs_unknown_correlation(cfg, logger, tmp_path):
	registry = Registry()
	backend = YieldingBackend(
		[IncomingResponse(correlation=9999, text="stray")]
	)
	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, backend, logger)
	)
	# Give it a moment to consume the stray response.
	await asyncio.sleep(0.05)
	dispatch_task.cancel()
	try:
		await dispatch_task
	except asyncio.CancelledError:
		pass

	log_text = (tmp_path / "log.jsonl").read_text()
	assert "surface_error" in log_text
	assert "9999" in log_text
```

- [ ] **Step 11.2: Run to verify failures**

```bash
pytest tests/test_gateway_ask_human.py -v
```

Expected: both new tests fail — `dispatch_responses` not importable.

- [ ] **Step 11.3: Add `dispatch_responses`**

Append to `server/gateway.py` (outside `build_tool_handlers`, at module level):

```python
async def dispatch_responses(
	registry: Registry,
	backend: MessengerBackend,
	logger: JsonlLogger,
) -> None:
	async for response in backend.poll_responses():
		request_id = registry.resolve_by_correlation(
			response.correlation, response.text
		)
		if request_id is None:
			logger.surface_error(
				"unknown_correlation",
				correlation=str(response.correlation),
			)
```

- [ ] **Step 11.4: Run tests**

```bash
pytest tests/test_gateway_ask_human.py -v
```

Expected: `3 passed`.

- [ ] **Step 11.5: Run full test suite**

```bash
pytest -v
```

Expected: all tests pass. No skipped tests.

- [ ] **Step 11.6: Commit boundary (developer)**

```bash
git add server/gateway.py tests/test_gateway_ask_human.py
git commit -m "feat(gateway): response-dispatch loop"
```

---

## Task 12: Main entry point

**Files:**

- Create: `server/main.py`
- Create: `server/__main__.py`

Wire config → logger → backend → registry → gateway → FastMCP → uvicorn. Run the uvicorn server and the dispatch task as concurrent asyncio tasks. On shutdown, cancel dispatch and close the Telegram `httpx` client.

No tests for this task — it is pure composition, and the integration is exercised by the manual smoke test in Task 14. A failing `main.py` will surface immediately on first `python -m server` invocation.

- [ ] **Step 12.1: Create `server/main.py`**

```python
"""Switchboard entry point — wires dependencies and runs the server."""

from __future__ import annotations

import asyncio
import contextlib
import signal

import uvicorn
from mcp.server.fastmcp import FastMCP

from server.config import Config, load_config
from server.gateway import (
	build_tool_handlers,
	dispatch_responses,
)
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from server.telegram import TelegramBackend


def _build_fastmcp(handlers) -> FastMCP:
	mcp = FastMCP("switchboard")

	@mcp.tool()
	async def ask_human(question: str, agent_id: str) -> str:
		"""Block until the developer responds from their phone. Returns
		the response text, or the sentinel '__TIMEOUT__' if the timeout
		window elapses."""
		return await handlers.ask_human(question, agent_id)

	@mcp.tool()
	async def notify_human(message: str, agent_id: str) -> str:
		"""Fire a status message to the developer. Non-blocking."""
		return await handlers.notify_human(message, agent_id)

	return mcp


async def _run(config: Config) -> None:
	logger = JsonlLogger(config.log_path)
	registry = Registry()
	backend = TelegramBackend(
		token=config.telegram_bot_token,
		chat_id=config.telegram_chat_id,
	)
	handlers = build_tool_handlers(config, registry, backend, logger)

	mcp = _build_fastmcp(handlers)

	uv_config = uvicorn.Config(
		mcp.sse_app(),
		host=config.host,
		port=config.port,
		log_level="info",
	)
	server = uvicorn.Server(uv_config)

	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, backend, logger)
	)

	loop = asyncio.get_running_loop()
	stop_event = asyncio.Event()

	def _request_stop() -> None:
		stop_event.set()
		server.should_exit = True

	# Windows does not support add_signal_handler reliably; fall back
	# to plain try/except KeyboardInterrupt in run().
	for sig_name in ("SIGINT", "SIGTERM"):
		sig = getattr(signal, sig_name, None)
		if sig is None:
			continue
		with contextlib.suppress(NotImplementedError):
			loop.add_signal_handler(sig, _request_stop)

	try:
		await server.serve()
	finally:
		dispatch_task.cancel()
		with contextlib.suppress(asyncio.CancelledError):
			await dispatch_task
		await backend.aclose()


def run() -> None:
	config = load_config()
	try:
		asyncio.run(_run(config))
	except KeyboardInterrupt:
		pass


if __name__ == "__main__":
	run()
```

- [ ] **Step 12.2: Create `server/__main__.py`**

```python
"""Enables `python -m server`."""

from server.main import run


if __name__ == "__main__":
	run()
```

- [ ] **Step 12.3: Smoke-verify the entry point starts**

With no Telegram creds set, the server must fail with a clear config error rather than starting and breaking later. Run:

```bash
unset TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID 2>/dev/null || true
python -m server
```

Expected output:

```
ConfigError: Missing required env var: TELEGRAM_BOT_TOKEN
```

(exit code non-zero). If any other traceback appears before `ConfigError`, fix before committing.

- [ ] **Step 12.4: Smoke-verify startup with creds (stub token)**

Set a placeholder token so config passes, then start:

```bash
export TELEGRAM_BOT_TOKEN=stub
export TELEGRAM_CHAT_ID=0
python -m server &
SERVER_PID=$!
sleep 2
curl -fsS -m 2 http://127.0.0.1:9876/sse | head -5 || echo "(sse stream opened and was closed — expected)"
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null
```

Expected: server starts, the `curl` call either returns SSE headers or is terminated by timeout (either indicates the endpoint is bound). Telegram poll loop will log transient errors from `getUpdates` rejecting the stub token — that is expected and non-fatal.

- [ ] **Step 12.5: Commit boundary (developer)**

```bash
git add server/main.py server/__main__.py
git commit -m "feat(main): wire config, gateway, MCP, uvicorn, dispatch"
```

---

## Task 13: Skill file

**Files:**

- Create: `skill/SKILL.md`

This is the content installed into `~/.claude/skills/switchboard/SKILL.md`. Consuming projects do not need to repeat any of this in their own `CLAUDE.md`.

- [ ] **Step 13.1: Write the skill**

Create `skill/SKILL.md`:

```markdown
---
name: switchboard
description: Use the ask_human and notify_human MCP tools to interact with the developer via Telegram while they are away from their desk. Invoke ask_human whenever a decision would otherwise stall the task (file overwrite, migration, ambiguous intent, permission to proceed). Invoke notify_human for non-blocking status updates.
---

# Switchboard

Switchboard is a local MCP gateway that lets you reach the developer on their phone while they are away from the desk. It exposes two tools:

- **`ask_human(question, agent_id)`** — blocks until the developer replies. Returns the reply text, or the sentinel string `"__TIMEOUT__"` if no reply arrives within the server's timeout window (default 24h).
- **`notify_human(message, agent_id)`** — fire-and-forget status update. Returns `"ok"` immediately.

## When to use it

The developer activates away mode by telling you something like:

> "I'm stepping away. Use the ask_human MCP tool for any questions or decisions that would normally require my input. I'll respond via Telegram."

Once in away mode, you must route **every** question that would otherwise go to the VS Code chat through `ask_human` instead. Do not guess at decisions that need human judgment. Do not abort. Do not wait silently.

At desk (not in away mode), interact with the developer normally through chat — Switchboard is not needed.

## Choosing an `agent_id`

The `agent_id` is a short human-meaningful label that appears in every Telegram message so the developer knows which agent is asking. In order of preference:

1. **Use a label the developer gave you.** If they said "call yourself IR2" or "label these as migration-work", use that label for every call during the session.
2. **Otherwise derive one from the current task.** A short 1-3 word label based on what you are working on: `DMXRefactor`, `IR2Migration`, `DocGen`. Pick it the first time you call `ask_human`, then reuse it for every subsequent call in the same session.

Keep the label stable across calls within a session. The developer should be able to tell at a glance that two messages are from the same agent.

## Response conventions

- Be concise in questions. The developer is on their phone. One or two sentences.
- Include enough context that the developer can decide without opening their laptop. Include file paths, commit IDs, or the specific ambiguity you need resolved.
- Suggest a default when there is one: "Overwrite foo.java with the refactored version? (default: yes)".
- For multi-choice, put the options in the question: "Use ActiveMQ or Kafka for the new event bus?"

## Handling `"__TIMEOUT__"`

If `ask_human` returns `"__TIMEOUT__"`, the developer did not reply within the window. Do not guess and continue. Instead:

1. Record what you were about to do and why you needed input.
2. Pause the current work stream. Do not take irreversible actions.
3. When the developer returns, resume from where you paused.

Use `notify_human` to record the pause if it is helpful context for later: `notify_human("Paused DMXRefactor — timed out waiting on approval to overwrite CustomerMapper.java", "DMXRefactor")`.

## Handling `"ERROR: ..."`

If `ask_human` returns a string starting with `"ERROR:"`, the gateway itself failed (e.g., Telegram unreachable). Treat this the same as a timeout — pause, do not guess.

## What not to use it for

- Do not call `ask_human` for decisions you can make yourself with the information in front of you. Away mode is not permission to defer judgment calls that do not require human input.
- Do not call `ask_human` for purely informational status ("I'm about to run the tests") — that is `notify_human`.
- Do not call either tool when the developer is at their desk and interacting with you via chat.
```

- [ ] **Step 13.2: Verify the skill file renders (optional sanity check)**

```bash
head -5 skill/SKILL.md
```

Expected: the YAML frontmatter header.

- [ ] **Step 13.3: Commit boundary (developer)**

```bash
git add skill/SKILL.md
git commit -m "feat(skill): add switchboard Claude Code skill"
```

---

## Task 14: README + manual smoke test

**Files:**

- Modify: `README.md`

Document install, run, agent wiring, skill install, and a manual end-to-end smoke sequence.

- [ ] **Step 14.1: Rewrite `README.md`**

Replace the entire contents of `README.md` with:

```markdown
# Switchboard

> A human-in-the-loop input gateway for Claude Code agents.

Switchboard is a locally-hosted MCP server that lets Claude Code agents pause mid-task and ask the developer a question via Telegram. Designed for away-from-desk workflows where the developer has stepped away but wants their agents to continue working unsupervised until they hit a decision that genuinely requires human input.

See [`docs/superpowers/specs/2026-04-19-switchboard-design.md`](docs/superpowers/specs/2026-04-19-switchboard-design.md) for the full design.

## Install

```bash
git clone <this-repo>
cd switchboard
python -m venv .venv
source .venv/Scripts/activate   # Git Bash on Windows
pip install -e ".[dev]"
```

## Configure

Set the required environment variables (OS env vars preferred; a `.env` file is loaded as a fallback if present):

```bash
export TELEGRAM_BOT_TOKEN="<token from @BotFather>"
export TELEGRAM_CHAT_ID="<your numeric chat id>"
```

To find your chat ID: message your bot any text, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser. The value at `result[-1].message.chat.id` is your chat ID.

Optional tuning:

```bash
export SWITCHBOARD_PORT=9876            # default 9876
export SWITCHBOARD_TIMEOUT_SECONDS=86400 # default 24 hours
export SWITCHBOARD_LOG_PATH=./logs/switchboard.jsonl
```

## Run

```bash
python -m server
```

The gateway binds to `127.0.0.1:9876` by default.

## Wire an agent to it

Add to the agent's MCP config (per-project or global):

```json
{
  "mcpServers": {
    "switchboard": {
      "type": "sse",
      "url": "http://localhost:9876/sse"
    }
  }
}
```

## Install the skill

Copy the skill file into your Claude Code skills directory:

```bash
mkdir -p ~/.claude/skills/switchboard
cp skill/SKILL.md ~/.claude/skills/switchboard/SKILL.md
```

## Manual smoke test

With the server running and an agent wired up:

1. In a Claude Code session, say: *"I'm stepping away — use ask_human for any decisions. Label yourself SmokeTest."*
2. Ask the agent to do something that should trigger a question, e.g. *"Delete the oldest file in logs/."*
3. Watch your phone: you should receive a Telegram message `[SmokeTest | xxxxxxxx] ...`.
4. Reply to that message with "yes" or similar.
5. The agent's `ask_human` tool call should unblock with your reply text.
6. Check `logs/switchboard.jsonl` — you should see `request_created` and `request_resolved` events.

## Tests

```bash
pytest
```

All unit tests are offline (no Telegram creds required).

## Project layout

See the design spec §11 for the canonical project layout.
```

- [ ] **Step 14.2: Commit boundary (developer)**

```bash
git add README.md
git commit -m "docs: update README for Switchboard"
```

- [ ] **Step 14.3: Run the full test suite one more time**

```bash
pytest -v
```

Expected: all tests pass.

- [ ] **Step 14.4: Final verification**

```bash
python -c "from server.main import run; print('import ok')"
```

Expected: `import ok` — the whole module graph loads cleanly with no import-order issues.

---

## Self-Review

**Spec coverage:**

- §1–3 Overview / Problem / Usage model → covered by the skill (Task 13) and README (Task 14).
- §4 Architecture → realised by Tasks 2 (registry), 4 (messenger), 6–7 (telegram), 8–11 (gateway), 12 (main wiring).
- §5.1 Switchboard MCP Server (Python, HTTP/SSE, dict-of-futures) → Task 12 wires FastMCP SSE; Task 2 is the registry.
- §5.2 MCP Tools (ask_human, notify_human) → Tasks 8–10 implement handlers; Task 12 exposes them via FastMCP.
- §5.3 Messenger Backend interface → Task 4.
- §5.3.1 Telegram concrete impl → Tasks 6–7.
- §6 Request lifecycle → exercised end-to-end by Task 11 (dispatch loop test routing through ask_human).
- §7 Agent configuration → documented in README (Task 14).
- §8 Skill, including `agent_id` selection → Task 13.
- §9 Timeout behavior (24h default, sentinel, follow-up message) → Task 10.
- §10 Logging (JSONL, event types, fields) → Task 3.
- §11 Project structure → created across Tasks 1–13.
- §12 Design decisions → documented in spec only; plan implements the decisions.
- §13 Out of scope → not implemented, by definition.
- §14 Open questions → the Telegram library decision is locked in as raw httpx (documented in "Key Implementation Decisions").
- §15 Security (loopback binding, no auth) → Task 5 (host default 127.0.0.1), Task 12 (uvicorn honors `host` from config).

**Placeholder scan:** no TBD / TODO / "implement later" strings. Every code block contains concrete implementation. The `NotImplementedError` stubs in Tasks 6 (poll_responses) and 8 (ask_human) are explicit staging steps — both are replaced by real code in subsequent tasks within this plan.

**Type consistency:**

- `Registry.add(request_id, agent_id, correlation)` — same signature in Task 2 definition and Tasks 8–11 call sites.
- `Registry.resolve_by_correlation(correlation, text) -> str | None` — consistent across Task 2 and Task 11.
- `MessengerBackend.send_question(request_id, agent_id, question) -> CorrelationToken` — matches in Task 4, Task 6 (telegram impl), Tasks 8–10 (gateway usage), Task 11 (test backend).
- `MessengerBackend.send_timeout_followup(request_id, agent_id, timeout_seconds, correlation)` — four-arg signature consistent across the ABC (Task 4), Telegram impl (Task 6), and gateway call site (Task 10).
- `IncomingResponse(correlation, text)` — same two fields in Task 4 definition, Task 7 yield site, and Task 11 dispatch consumer.
- `TIMEOUT_SENTINEL = "__TIMEOUT__"` — defined in Task 8, asserted in Task 10 and Task 13 (skill).
- Logger method names (`request_created`, `request_resolved`, `notify_sent`, `timeout`, `tool_error`, `surface_error`) — consistent between Task 3 definition and call sites in Tasks 8–11.

No gaps found.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-19-switchboard.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task with two-stage review between tasks. Best for a long plan like this one where losing context between tasks is helpful rather than harmful.
2. **Inline Execution** — execute tasks in this session using `executing-plans`, with checkpoints between tasks for review.

Which approach?
