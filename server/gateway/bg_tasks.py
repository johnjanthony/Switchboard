"""Background-task tracking primitive.

Per the CPython docs for `asyncio.create_task`:

> Save a reference to the result of this function, to avoid a task disappearing
> mid-execution. The event loop only keeps weak references to tasks.

A bare `asyncio.create_task(coro)` whose return value is discarded can be
garbage-collected before the coroutine finishes — silently, with no log line.
That failure mode is nearly impossible to diagnose post-hoc.

Use `_spawn_bg(coro, label=...)` instead. It stores a strong reference in the
module-level `_BG_TASKS` set and registers a done-callback that removes the
reference once the task completes (success, exception, or cancellation).

Always pass a `label` — `repr(task)` includes the name, so a stuck or failing
task is easy to identify in logs / debuggers / `asyncio.all_tasks()` output.

Must be called from a running event loop (delegates to `asyncio.create_task`).
For sites that need to schedule onto a specific loop captured externally
(e.g. `loop.call_soon_threadsafe(lambda: _spawn_bg(...))` from a non-loop
thread), the lambda runs on the loop thread, so the inner `_spawn_bg` call
satisfies the running-loop requirement.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Coroutine


_BG_TASKS: set[asyncio.Task] = set()

_log = logging.getLogger(__name__)

BG_FAILURE_AUDIT_LABEL = "bg_failure_audit"

_FAILURE_HOOK: Callable[[str, BaseException], None] | None = None


def set_bg_failure_hook(hook: Callable[[str, BaseException], None] | None) -> None:
	"""Install a process-wide callback invoked on the loop thread when a tracked
	background task fails (REV-105: stdlib logging alone leaves fire-and-forget
	failures invisible to the JSONL audit trail). main.py wires this to the
	JsonlLogger at startup. The hook must not block; exceptions it raises are
	logged and swallowed. Tasks labelled BG_FAILURE_AUDIT_LABEL are exempt so a
	failing audit write cannot recurse."""
	global _FAILURE_HOOK
	_FAILURE_HOOK = hook


def _on_task_done(task: asyncio.Task) -> None:
	"""Done-callback: drop the strong ref, and log any exception. These tasks are
	fire-and-forget (mostly Firebase writes), so a failure here previously
	vanished with no trace; logging it makes a failed background write visible.
	The optional failure hook additionally routes the failure to the JSONL
	audit log (wired in main.py, REV-105)."""
	_BG_TASKS.discard(task)
	if task.cancelled():
		return
	exc = task.exception()
	if exc is not None:
		_log.error("background task %s failed: %r", task.get_name(), exc, exc_info=exc)
		if _FAILURE_HOOK is not None and task.get_name() != BG_FAILURE_AUDIT_LABEL:
			try:
				_FAILURE_HOOK(task.get_name(), exc)
			except Exception:
				_log.exception("bg_failure_hook raised")


def _spawn_bg(coro: Coroutine, *, label: str) -> asyncio.Task:
	"""Schedule `coro` as a background task tracked in `_BG_TASKS`.

	Returns the created Task. The task is removed from `_BG_TASKS` when it
	completes; any exception it raised is logged (not swallowed silently).
	Raises `RuntimeError` if no event loop is running (same as
	`asyncio.create_task`)."""
	task = asyncio.create_task(coro, name=label)
	_BG_TASKS.add(task)
	task.add_done_callback(_on_task_done)
	return task


async def drain_bg_tasks(timeout: float = 5.0) -> int:
	"""Wait up to `timeout` seconds for outstanding background tasks to finish so
	their fire-and-forget writes flush before the loop closes on shutdown.

	Returns the number still pending after the timeout (0 on a clean drain).
	Never raises: per-task exceptions are handled by the done-callback."""
	pending = [t for t in _BG_TASKS if not t.done()]
	if not pending:
		return 0
	_, still_pending = await asyncio.wait(pending, timeout=timeout)
	return len(still_pending)
