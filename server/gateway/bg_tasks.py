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
from typing import Coroutine


_BG_TASKS: set[asyncio.Task] = set()


def _spawn_bg(coro: Coroutine, *, label: str) -> asyncio.Task:
	"""Schedule `coro` as a background task tracked in `_BG_TASKS`.

	Returns the created Task. The task is removed from `_BG_TASKS` when it
	completes. Raises `RuntimeError` if no event loop is running (same as
	`asyncio.create_task`)."""
	task = asyncio.create_task(coro, name=label)
	_BG_TASKS.add(task)
	task.add_done_callback(_BG_TASKS.discard)
	return task
