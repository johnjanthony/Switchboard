# Listener Supervision + Deeper /healthz — Design Spec

**Date:** 2026-05-01
**Status:** ✅ Implemented and verified 2026-05-01. End-to-end smoke test passed (queue `/spawn` while service down, restart, command picked up within 2 s). See PROJECT-JOURNAL.md 2026-05-01 entry. Decision log at the bottom.

Tracks `docs/feature-backlog.md` items "Listener thread supervision (M1)" and "Deeper `/healthz` + crash-alert cadence (M2)" — surfaced in `docs/2026-04-28-codebase-review.md` as M1 and M2.

---

## Problem

Two related operational gaps surfaced together this morning (2026-05-01) when the host machine resumed from sleep mid-WARP-reconnect. `oauth2.googleapis.com` failed DNS for ~30 seconds; firebase_admin's OAuth refresh raised `TransportError` out of `_start_listen`, and **all five Firebase SSE listener threads died silently**. `/spawn` commands written to Firebase by the Android app sat in the queue indefinitely. There was no operator-visible signal — `/healthz` reported "all good" because it only knows about pending question state.

The two structural issues:

1. **M1 — Listener threads are unsupervised daemons.** `firebase_admin.db.ListenerRegistration` (`db.py:121`) spawns `threading.Thread(target=self._start_listen)`. When `_start_listen`'s SSE iterator raises, the thread exits and is never restarted by the SDK. Switchboard stored the registration, assumed it stayed alive, and had no recovery mechanism. (`server/firebase.py:441-487` for the away-mode listeners; `poll_responses` / `poll_commands` / `poll_away_mode_commands` for the dispatch listeners; `start_inject_listener` per-session.)

2. **M2 — `/healthz` is shallow + crash-alert fires once.** `server/main.py:266-271` reports only `pending_count`, `oldest_pending_age_seconds`, `total_answered`. `server/gateway/dispatch.py:_loop_crash_backoff` pages once at exactly `consecutive_failures == 5` and never again — a 30-minute outage produces one alert and silence.

---

## Goal

After this work, a Firebase SSE thread that dies for any reason is detected within ~5 seconds, restarted with exponential backoff, and visible at `/healthz`. Sustained dispatch-loop crashes alert at a doubling cadence so operator visibility tracks outage duration. Existing successful behavior is unchanged: hot-path latency, MCP transport semantics, away-mode protocols, collab session state.

---

## Design

### `SupervisedListener` primitive

A new class in `server/firebase_supervisor.py` owns one Firebase listener for its full lifetime. Lifecycle:

1. `start()` — schedules an asyncio supervisor task on the running loop. The task immediately calls `db.reference(path).listen(wrapped_cb)` (offloaded via `run_in_executor` since `.listen()` itself is sync), stores the `ListenerRegistration`, and enters its watchdog loop.

2. **Watchdog loop** — every `watchdog_interval` seconds (default below), check `registration._thread.is_alive()`. Healthy → reset backoff to initial. Dead → close the registration (offloaded — `.close()` blocks for many seconds during SSE teardown), increment `crash_count`, update `last_crash_at`, sleep `backoff` seconds, double `backoff` (capped), call `.listen()` again. State machine is `starting` → `live` → `reconnecting` → `live` (loop) → `stopped`.

3. **Wrapped callback** — every event update sets `last_event_at = time.monotonic()` before invoking the user callback. User-callback exceptions are caught and logged via the supplied `error_logger`; they do not kill the supervisor.

4. `stop()` — cancels the supervisor task, closes the registration. Idempotent.

5. `health()` — returns a `ListenerHealth` snapshot: `name`, `state`, `last_event_at`, `crash_count`, `last_crash_at`. The `FirebaseBackend.listener_health()` accessor converts these to JSON-friendly dicts (timestamps become "seconds ago" relative to `time.monotonic()`).

### `LoopSupervisor` for dispatch coroutines

Replaces the standalone `_loop_crash_backoff` helper. Each of the four dispatch coroutines (`dispatch_responses`, `dispatch_commands`, `dispatch_inject_queue`, `dispatch_away_mode_commands`) constructs one at startup and calls `record_success()` on every successful iteration and `await record_crash(exc)` from its outermost `except Exception`.

Tracks `consecutive_failures`, `crash_count`, `last_crash_at`, `backoff`, and a doubling `next_alert_at` (see Q2). On `record_success`, all of these reset (except `crash_count`, which is cumulative). On `record_crash`, the supervisor logs, optionally alerts (when `consecutive_failures` crosses `next_alert_at`), sleeps `backoff`, doubles `backoff` (capped). `health()` returns a `LoopHealth` snapshot.

### `/healthz` payload

```jsonc
{
  "pending": {
    "count": <int>,
    "oldest_pending_age_seconds": <float | null>,
    "total_answered": <int>
  },
  "listeners": [
    {
      "name": "responses" | "commands" | "away_mode_commands"
            | "away_mode_global" | "away_mode_channels" | "inject:<cwd>",
      "state": "starting" | "live" | "reconnecting" | "stopped",
      "last_event_seconds_ago": <float | null>,
      "crash_count": <int>,
      "last_crash_seconds_ago": <float | null>
    }
  ],
  "dispatch_loops": [
    {
      "name": "dispatch_responses" | "dispatch_commands"
            | "dispatch_inject_queue" | "dispatch_away_mode_commands",
      "consecutive_failures": <int>,
      "crash_count": <int>,
      "last_crash_seconds_ago": <float | null>
    }
  ]
}
```

---

## Open design questions

Six concrete decisions where I'd like John's call before locking the plan.

### Q1 — Watchdog interval

**Choices:** 5 s (recommended) / 10 s / 30 s.

**Recommended: 5 s.** Detection latency = at most `watchdog_interval + initial_backoff = 5 + 1 = 6 s` once a thread dies. The check is cheap (`thread.is_alive()` is a memory read). Five-second resolution feels right for a single-developer tool — fast enough that `/spawn` from the phone doesn't sit visibly stuck while we figure out the listener died, slow enough to avoid log noise.

**Why 5 s vs 10 s:** for a dead listener, the *total* time to recovery is dominated by the initial backoff (1 s) plus the time `.listen()` takes to re-establish the SSE stream (~1-3 s in practice). A 10 s watchdog adds 5 s of pure idle time per recovery. For 30 s, it's 25 s — too long when the human is staring at the phone wondering why nothing happened.

**Why not 1 s:** burns context for very rare events. The ListenerRegistration thread polling itself is essentially idle, but we'd be running a supervisor coroutine 1×/sec across 5+ listeners.

### Q2 — Crash-alert cadence

The current code alerts exactly once at `consecutive_failures == 5`. We want alerts to keep firing during a sustained outage, with diminishing frequency so we don't spam the admin channel.

**Decided: A (doubling from 5) capped by a 10-minute wall-clock gate.** Alerts fire at 5, 10, 20, 40, … consecutive failures, but only while less than 10 minutes have elapsed since the first failure of the current outage. After the 10-minute mark, alerts are suppressed even if more thresholds are crossed — the loop is clearly not going to recover on its own and continued paging adds no operator value.

Implementation: `LoopSupervisor` tracks `first_failure_at` (set on first crash, cleared by `record_success`). Inside `record_crash`, the alert path checks `time.monotonic() - first_failure_at <= 600.0` before firing.

In practice with backoff capped at 60 s, the alert sequence is roughly: ~30 s (5 failures), ~1.5 min (10 failures), suppressed at ~11 min (20 failures would have fired but 10-min gate has passed). On recovery, all state resets — a subsequent outage starts the cadence fresh.

### Q3 — `/healthz` shape change

The current `/healthz` payload has three flat fields. To add `listeners` and `dispatch_loops` cleanly, my plan groups the existing fields under a new `pending` object:

```jsonc
// before
{ "pending_count": 0, "oldest_pending_age_seconds": null, "total_answered": 5 }

// after
{ "pending": { "count": 0, "oldest_pending_age_seconds": null, "total_answered": 5 },
  "listeners": [...], "dispatch_loops": [...] }
```

**Choices:**
- **A: regrouped under `pending` (recommended)** — clean, all sections at the same depth.
- **B: keep flat** — `pending_count`, `oldest_pending_age_seconds`, `total_answered` stay at root; `listeners` and `dispatch_loops` are added alongside. No breaking change.

**Recommended: A**, but only if you confirm there are no machine consumers (a curl-and-jq script you wrote, a monitor probe, anything else). If there is a consumer, B.

**Open ask for John:** is anything outside this repo reading `/healthz`?

### Q4 — Liveness detection mechanism

How do we tell that an SDK-internal SSE thread has died?

**Choices:**
- **A (recommended): `registration._thread.is_alive()`.** Direct check on a public-attribute-named-with-underscore. It works because firebase_admin assigns `self._thread = threading.Thread(...)` and `_thread.start()` in `ListenerRegistration.__init__`. Fast, deterministic. Downside: poking through SDK encapsulation; if firebase_admin renames the attribute we get false-alives or AttributeError.
- **B: time-since-last-event heuristic.** "If no event in N minutes, assume dead." Doesn't work for naturally quiet listeners (the `commands` queue sees an event only when the user hits `/spawn` — could be hours).
- **C: replace `.listen()` entirely.** Implement our own SSE consumer using `firebase_admin._sseclient.SSEClient` (the lower-level primitive). We control the iteration, the try/except, the reconnect. Bigger surface, but no private-API reliance.

**Recommended: A**, with a defensive `getattr(reg, "_thread", None)` and treat-as-alive on AttributeError so an SDK rename degrades to "no detection" rather than "spurious crashes." Pin firebase-admin to its current major version in pyproject.toml so a future SDK upgrade triggers a manual re-check.

C is correct in the long run but ~3× the surface and out of scope for "half a day of work" per the backlog estimate. If A breaks under a future SDK upgrade, the fallback is C — at which point we'll have learned what we need from operating A in production.

### Q5 — Per-session inject listener supervision

`start_inject_listener(session_id)` is called when a collab session starts. A bare `.listen()` returns a `ListenerRegistration` stored in `self._inject_listeners[session_id]`.

**Choices:**
- **A (recommended): supervise it.** Same lifecycle: `SupervisedListener` keyed by `inject:{session_id}`, removed when the session ends.
- **B: don't supervise.** The session's outer `message_and_await_agent` has its own 24h timeout, so a dead inject listener "only" silently breaks the human-inject path — not the agent-to-agent path.

**Recommended: A.** A dead inject listener silently breaks the phone-side compose box for that session, with no operator-visible signal. The supervisor cost is one extra asyncio task per active collab session. We don't run many concurrent collabs.

**Wrinkle:** the supervisor outlives the listener registration if the session ends without an explicit teardown call. Today, sessions are not actively garbage-collected (acknowledged in `docs/superpowers/specs/2026-04-23-bring-your-own-session-design.md` line 59 — "No explicit session teardown"). So the supervisor continues running until service shutdown. Acceptable: it's idle 99% of the time, and `aclose` cleans up on shutdown. If session GC is added later (separate work), it'll need to call `await sup.stop()` for the inject supervisor.

### Q6 — `poll_spawn_collision_decision` listener

Per-spawn ephemeral listener (lines 694-712 in firebase.py). Started inside `_maybe_handle_spawn_collision`, polled with `asyncio.wait_for(..., timeout=600)`, closed in the `finally`.

**Choices:**
- **A (recommended): leave unsupervised.** The 600 s `wait_for` already catches the "thread died, future never resolves" case — the timeout fires, the spawn is treated as cancelled, and the dialog is dismissed. Adding supervision here would be belt-and-braces.
- **B: supervise it.** Consistency with the other listeners.

**Recommended: A.** B adds complexity for a case the existing timeout already handles correctly. Document the rationale in the journal entry so the inconsistency isn't surprising.

---

## Out of scope

- **`MessengerBackend` trait split (H4 demoted).** A health-accessor method on `FirebaseBackend` is fine for now. When the trait split happens, `listener_health()` moves to a `BackendHealth` mixin or similar.
- **Persistence layer (H1 deferred).** Independent.
- **Replacing firebase_admin's `.listen()` with our own SSE consumer (Q4 option C).** Standby fallback if Q4-A breaks.
- **Active liveness probing.** No write-and-listen-back heartbeat. Q4-A's thread-aliveness check covers the failure mode we observed.
- **Time-based alert cadence (Q2 option D).** Counted alerts are simpler and adequate.
- **Retiring `_inject_listeners` map.** When all listeners flow through `_supervised`, the map is dead code; can be removed in a follow-up cleanup commit but not required for this feature.

---

## Acceptance

After implementation:

- A controlled network outage (disable WARP for ~30 s, re-enable) results in `/healthz` showing `state: reconnecting` for affected listeners during the outage and `state: live` within ~10 s of network restore. `/spawn` from the phone works after the outage without a service restart.
- The doubling alert cadence is exercised by a unit test that drives `LoopSupervisor.record_crash` enough times to confirm alerts fire at 5, 10, 20.
- All existing tests still pass.
- `/healthz` is documented in `AGENTS.md` as a diagnostic surface.

---

## Decision log

Resolved 2026-05-01.

- **Q1 — Watchdog interval:** [x] **5 s**.
- **Q2 — Alert cadence:** [x] **A=double-from-5, capped by 10-minute wall-clock gate.**
- **Q3 — `/healthz` shape:** [x] **A=regrouped under `pending`.** No machine consumers.
- **Q4 — Liveness detection:** [x] **A=`registration._thread.is_alive()`.** Defensive `getattr(reg, "_thread", None)` plus an entry in `feature-backlog.md` for the Option C fallback (own SSE consumer) if firebase_admin renames the attribute.
- **Q5 — Inject listener supervision:** [x] **A=supervise.** Plus a `feature-backlog.md` entry for collab-session GC, cross-referencing the inject-supervisor wrinkle (supervisor outlives the registration if the session is purged without explicit teardown).
- **Q6 — Spawn-decision listener supervision:** [x] **A=skip.** Already bounded by the 600-second `asyncio.wait_for` timeout in `_maybe_handle_spawn_collision`. Rationale recorded in the journal entry for this work.

Plan at `docs/superpowers/plans/2026-05-01-listener-supervision-and-healthz.md` revised to lock these in.
