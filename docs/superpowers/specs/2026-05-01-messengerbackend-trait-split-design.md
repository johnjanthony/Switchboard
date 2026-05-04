# MessengerBackend Trait Split (H4) — Design Spec

**Date:** 2026-05-01
**Status:** ⏳ Designed; implementation pending.

Tracks `docs/tracking/backlog.md` item "`MessengerBackend` trait split (god-interface refactor)" — surfaced in `docs/2026-04-28-codebase-review.md` as H4. Folds in the M1 surface from `docs/superpowers/specs/2026-05-01-listener-supervision-and-healthz-design.md` per the 2026-05-01 backlog addendum.

---

## Problem

`server/messenger.py` defines a 31-method `MessengerBackend` ABC. The majority of methods carry pure Firebase semantics — away-mode mirroring, listener subscription, spawn-collision storage, channel-meta lookups, queue cleanup. Three structural issues:

1. **The ABC is a god interface.** Any test that needs a backend has to mock or stub all 31 methods, even when the unit under test only uses one. The "pluggable backends" promise in `AGENTS.md` is unsupported by this surface — replacing Firebase would require re-implementing roughly 80% of these methods.

2. **The abstraction has leaked.** `server/main.py:258` does `isinstance(backend, FirebaseBackend)` to gate a single call (`backend.make_pending_mirror_writer()`). The very existence of that check is the symptom the H4 entry calls out.

3. **Drift since the entry was written (2026-04-28).** Two more Firebase-only methods (`write_response_text`, `make_pending_mirror_writer`) appeared after the entry. The M1 listener supervision work (2026-05-01) added `listener_health()` and a `_supervised` registry as cross-cutting state. Three pre-existing methods (`update_channel_title`, `update_last_activity`, `fetch_message_text`) are dead code with zero production callers anywhere in `server/`, `tests/`, or the cross-platform client trees.

---

## Goal

After this work:

- `MessengerBackend` is gone. Five focused trait classes plus a 1-method `Backend` base replace it.
- `FirebaseBackend` inherits all six. No new functionality. No behavior change in production code paths.
- The `isinstance(backend, FirebaseBackend)` check at `server/main.py:258` is converted to a `getattr` capability check, matching the pattern `listener_health()` and `write_response_text()` already use. After H4, no `isinstance(backend, FirebaseBackend)` references survive in `server/`.
- Gateway-handler signatures narrow from `MessengerBackend` to the specific traits each handler uses, so reviewers can see at a glance which surface is in play.
- Test fakes can declare a trait base for type-checker leverage; existing inline per-test stubs continue to work without conftest scaffolding.
- The 398 existing tests pass; no test is removed for behavioral reasons.

---

## Non-goals

- **Deleting the three dead methods.** `update_channel_title`, `update_last_activity`, `fetch_message_text` get dropped from the trait surface but kept as concrete methods on `FirebaseBackend` so the existing unit tests of those methods pass untouched. A separate backlog item will track final deletion after verifying no out-of-tree consumers.
- **Replacing `firebase_admin.db.listen()` with our own SSE consumer.** Tracked separately as the M1 fallback item in `docs/tracking/backlog.md`.
- **Adding new pluggable backends** (e.g. an in-memory test backend, an alternate transport). H4 makes that possible; it does not deliver one.
- **Refactoring `SupervisedListener` internals**, the Firebase data model, or the JMS/REST transport.

---

## Design

### Inheritance shape

```python
class Backend(ABC):
	@abstractmethod
	async def aclose(self) -> None: ...

class MessageWriter(ABC): ...
class ResponsePoller(ABC): ...
class AwayModeMirror(ABC): ...
class ChannelLifecycle(ABC): ...
class InjectPort(ABC): ...

class FirebaseBackend(MessageWriter, ResponsePoller, AwayModeMirror,
                     ChannelLifecycle, InjectPort, Backend):
	# implements all six surfaces
	# also defines (Firebase-internal, not on any trait):
	#   listener_health, write_response_text, make_pending_mirror_writer
	#   update_channel_title, update_last_activity, fetch_message_text  (dead concretes)
	#   _supervised dict, _no_op_async_logger helper
```

The `Backend(ABC)` 1-method base exists so every backend has a contract for resource teardown. Without it, alternate backends could silently skip `aclose()` and leak resources.

### Trait surfaces

#### `Backend` (1 method)

| Method | Default |
|---|---|
| `aclose() -> None` | abstract |

#### `MessageWriter` (7 methods) — channel-message writes, acks, system messages

| Method | Default |
|---|---|
| `write_channel_message(...)` | abstract |
| `send_timeout_followup(...)` | abstract |
| `send_resolution_confirmation(...)` | abstract |
| `send_text(text)` | no-op |
| `send_spawn_ack(channel_id, prompt)` | no-op |
| `send_stale_reply_notice(cwd, sender)` | no-op |
| `mark_question_cancelled(cwd, request_id)` | no-op |

Trait scope: anything that produces or annotates a message bubble in the channel. Includes both the abstract production-message methods and the no-op-default system/admin notifications.

#### `ResponsePoller` (5 methods) — response/command queues + cleanup + startup reset

| Method | Default |
|---|---|
| `poll_responses() -> AsyncIterator[IncomingResponse]` | abstract |
| `poll_commands() -> AsyncIterator[str]` | abstract |
| `poll_away_mode_commands() -> AsyncIterator[dict]` | empty generator |
| `delete_response_slot(slot)` | no-op |
| `reset_all_pending_responses()` | no-op |

Departure from the 2026-04-28 backlog entry: `reset_all_pending_responses` was listed under `AwayModeMirror` (likely a copy-paste artifact, since `main.py:248-256` calls all three startup resets in sequence). It belongs on `ResponsePoller` semantically — it zeros out `channels/*/pending_responses`, which is response state.

#### `AwayModeMirror` (5 methods) — away-mode state mirror + listeners + startup resets

| Method | Default |
|---|---|
| `write_away_mode_mirror(cwd, active)` | no-op |
| `load_away_mode_snapshot(registry)` | no-op |
| `start_away_mode_listeners(registry)` | no-op |
| `reset_all_away_mode()` | no-op |
| `delete_legacy_away_mode_node()` | no-op |

#### `ChannelLifecycle` (8 methods) — channel-state CRUD + spawn-collision sub-flow

| Method | Default |
|---|---|
| `write_session_meta(channel_id, type, project_key, ...)` | no-op |
| `read_channel_meta(cwd) -> dict` | returns `{"title": None, "last_activity_at": None, "hidden": False}` |
| `has_messages(cwd) -> bool` | returns `False` |
| `wipe_channel(cwd)` | no-op |
| `set_channel_hidden(cwd, hidden)` | no-op |
| `write_spawn_collision_prompt(...)` | no-op |
| `clear_spawn_collision_prompt(spawn_id)` | no-op |
| `poll_spawn_collision_decision(spawn_id) -> dict` | raises `NotImplementedError` |

Two departures from the 2026-04-28 backlog entry. First, the trait was named `SpawnCollisionPort` and contained only the spawn-collision methods. Renamed `ChannelLifecycle` because the actual cohesive set is broader — channel create/inspect/mutate/destroy plus the spawn-time collision sub-flow that uses those reads. Second, `write_session_meta` lives here, not on `MessageWriter` — it writes the channel's identity record (type, project_key, agent_senders, task), not a message bubble.

#### `InjectPort` (2 methods) — per-session inject listener

| Method | Default |
|---|---|
| `start_inject_listener(session_id)` | no-op |
| `poll_inject_messages() -> AsyncIterator[tuple]` | empty generator |

### Firebase-only methods (capability-detected, not on any trait)

These exist on `FirebaseBackend` but never on the abstract surface. Callers use `getattr` or `hasattr` capability detection.

| Method | Caller(s) | Status |
|---|---|---|
| `listener_health() -> list[dict]` | `server/main.py:275` (healthz) | Already `getattr`-gated. No change. |
| `write_response_text(channel_id, msg_id, text)` | `gateway/dispatch.py:46`, `gateway/bulk_respond.py:53` | Already `hasattr`-gated. No change. |
| `make_pending_mirror_writer() -> Callable[[str, int], None]` | `server/main.py:258-259` | Currently `isinstance`-gated. **Convert to `getattr` capability check as part of H4.** |

The conversion at `server/main.py:258-259`:

```python
# Before:
if isinstance(backend, FirebaseBackend):
	registry.set_pending_mirror(backend.make_pending_mirror_writer())

# After:
mirror_writer_fn = getattr(backend, "make_pending_mirror_writer", None)
if callable(mirror_writer_fn):
	registry.set_pending_mirror(mirror_writer_fn())
```

After H4, `grep -rn "isinstance.*FirebaseBackend" server/` returns zero results. This is the verifiable success criterion for the leaked-abstraction goal.

### Dead methods

| Method | Disposition |
|---|---|
| `update_channel_title(cwd, title)` | Drop from trait surface. Keep concrete on `FirebaseBackend`. |
| `update_last_activity(cwd, ts, preview)` | Drop from trait surface. Keep concrete on `FirebaseBackend`. |
| `fetch_message_text(cwd, msg_id)` | Drop from trait surface. Keep concrete on `FirebaseBackend`. |

Verified zero production callers across `server/`, `tests/`, and the cross-platform client trees (`*.kt`, `*.swift`, `*.ts`, `*.js`). Existing unit tests in `test_firebase_hidden.py` and `test_messenger_contract.py` continue to exercise the concrete `FirebaseBackend` methods.

A follow-up backlog item will track final deletion: "Delete `FirebaseBackend.update_channel_title` / `update_last_activity` / `fetch_message_text` after verifying no out-of-tree consumers."

### Gateway handler signature narrowing

Each gateway-side function gets a narrower type hint reflecting the traits it actually uses. Concrete map (subject to refinement during plan execution as exact handler-by-handler call sets are confirmed):

| Function | Today | Post-H4 (target traits) |
|---|---|---|
| `gateway/handlers.py::_handle_notify` | `MessengerBackend` | `MessageWriter` |
| `gateway/handlers.py::_handle_ask` | `MessengerBackend` | `MessageWriter` |
| `gateway/handlers.py::_handle_resolve` | `MessengerBackend` | `MessageWriter` |
| `gateway/handlers.py::_handle_collab_start` | `MessengerBackend` | `MessageWriter`, `InjectPort`, `ChannelLifecycle` |
| `spawn.py::SpawnHandler` | `MessengerBackend` | `MessageWriter`, `InjectPort`, `ChannelLifecycle` |
| `gateway/dispatch.py::dispatch_responses_loop` | `MessengerBackend` | `ResponsePoller`, `MessageWriter`, plus `write_response_text` capability check |
| `gateway/dispatch.py::dispatch_commands_loop` | `MessengerBackend` | `ResponsePoller`, `AwayModeMirror`, `MessageWriter` |
| `gateway/dispatch.py::dispatch_inject_queue_loop` | `MessengerBackend` | `InjectPort` |
| `gateway/dispatch.py::dispatch_away_mode_commands_loop` | `MessengerBackend` | `ResponsePoller`, `AwayModeMirror` |
| `gateway/bulk_respond.py::bulk_respond` | `MessengerBackend` | `MessageWriter`, plus `write_response_text` capability check |
| `server/main.py::serve` | `MessengerBackend` | concrete `FirebaseBackend` (lifecycle wiring + capability checks) |

For multi-trait signatures, prefer a small parameter intersection-class (an ABC-union) defined adjacent to the function: `class _CollabBackend(MessageWriter, InjectPort, ChannelLifecycle): ...` rather than overloading types or accepting multiple parameters. **Do not mix `typing.Protocol` into these intersection bases** — Python rejects `class X(MessageWriter, ..., Protocol)` with `TypeError: Protocols can only inherit from other protocols` because the traits are ABCs. Plain multi-inheritance ABC-union gives the same type-narrowing benefit at function signatures without the language-level constraint. Commit to this single style across the codebase during plan execution.

### Test strategy

Codebase already uses inline per-test fakes; there is no central conftest backend stub. Three flavors of fake exist today:

1. **`FirebaseBackend`-subclassing fakes** (`test_firebase_hidden.py`, `test_firebase_spawn_decision.py`) — exercise real `FirebaseBackend` logic with network calls stubbed. Untouched by H4.

2. **Per-test inline duck-typed stubs** (`test_main_routes.py::_MirrorBackend`, `test_firebase_supervisor.py`'s three inline `_FakeBackend` classes, `test_away_mode_commands.py::FakeBackend`) — already per-trait-minimal in spirit but lack the trait *type* to declare against. After H4, optionally inherit from the specific trait the test cares about (e.g. `class _MirrorBackend(AwayModeMirror)`). Not mandatory; existing duck typing continues to work.

3. **Full ABC contract stubs** (`test_messenger_contract.py::_Stub`, `_StubBackend`, `_RecordingBackend`) — exist solely to verify abstract-method declarations and default behaviors. Reorganize into `test_backend_contracts.py` (renamed, plural) with one `class TestXxxContract:` per trait inside. Single file, six classes (Backend + 5 traits), structurally mirrors the trait surface for navigation while avoiding 5× import boilerplate. The contract stubs simplify because the dead-method overrides become unnecessary once those methods are off the ABC.

`tests/conftest.py` gets no new entries.

---

## Migration order

The full set of changes lands as a single commit per John's bulk-review preference. The ordering below describes the working sequence during execution to keep tests green at each task boundary; the dual-inheritance state at step 2 is intermediate working state, not a shipping artifact — final commit lands at step 5.

1. Define new trait classes (`Backend`, `MessageWriter`, `ResponsePoller`, `AwayModeMirror`, `ChannelLifecycle`, `InjectPort`) in `server/messenger.py` alongside the existing `MessengerBackend`. Each trait gets the methods listed above with the listed defaults. Run tests; should still be 398/398.

2. Make `FirebaseBackend` inherit the new traits in addition to `MessengerBackend` (transitional dual inheritance). Run tests; still 398/398.

3. Narrow gateway-handler signatures one handler at a time per the table above, fixing any test type hints as we go. Run tests after each handler. Stays at 398/398.

4. Convert the `isinstance(backend, FirebaseBackend)` check at `server/main.py:258` to a `getattr` capability check.

5. Remove `MessengerBackend` from `FirebaseBackend`'s bases. Drop `MessengerBackend` itself from `server/messenger.py`. Drop the three dead methods (`update_channel_title`, `update_last_activity`, `fetch_message_text`) from the abstract trait surface (keep concrete on `FirebaseBackend`). Run tests.

6. Rename `test_messenger_contract.py` → `test_backend_contracts.py` and reorganize into per-trait classes. Drop the now-unnecessary dead-method stub overrides in `_StubBackend` / `_RecordingBackend`.

7. Add a new entry to `docs/tracking/backlog.md` capturing the dead-method follow-up: "Delete `FirebaseBackend.update_channel_title` / `update_last_activity` / `fetch_message_text` after verifying no out-of-tree consumers" (MEDIUM priority, following the H4 entry pattern). Doing this *before* removing the H4 entry guarantees the follow-up is captured in the same commit and isn't forgotten.

8. Remove the H4 entry (and its M1 addendum) from `docs/tracking/backlog.md` since this spec supersedes. The M1 addendum is currently uncommitted on `develop`; deleting the entry it was inside resolves both as a single change.

9. Final verification: `pytest` passes (count may shift slightly because some contract-test signature assertions for the now-absent abstract surface are removed; no behavioral test is removed). `grep -rn "isinstance.*FirebaseBackend" server/` returns zero. `grep -rn "MessengerBackend" server/ tests/` returns zero. `/healthz` still returns valid `listener_health` snapshots.

---

## Risks

- **Method resolution order on six-base multiple inheritance.** Mitigation: traits' methods are uniquely named (verified by the slot-everything-into-one-trait exercise during brainstorming); no name collisions across surfaces. Python MRO is deterministic by C3 linearization; no diamond hazards because the traits don't share base classes other than `ABC`.

- **Test fakes that don't declare a trait base type miss type-checker validation of method signatures.** Mitigation: the runtime ABC check still fires on instantiation if abstract methods are unimplemented; declaring a trait base on inline fakes is opt-in tightening, not a structural requirement.

- **Out-of-tree consumers of `MessengerBackend`.** This codebase is single-developer; no known external consumers exist. **Assumption: no out-of-tree code imports `MessengerBackend` from this repo.** If any future external consumer is identified, the dual-inheritance step 2 becomes a deprecation cycle with `MessengerBackend` retained as a deprecated alias instead of intermediate working state.

- **Backlog drift between this spec and `docs/tracking/backlog.md`.** The 2026-04-28 H4 entry plus the 2026-05-01 M1 addendum are stale relative to this spec (3 dead methods, 2 more Firebase-only methods, the trait rename, and the `write_session_meta` move all post-date the entry). Mitigation: this spec supersedes; on merge, the H4 entry is removed from `docs/tracking/backlog.md`.

---

## Verification at completion

- `pytest` passes. Count may shift slightly: contract-test signature assertions for the now-absent abstract surface are removed; behavioral tests are unchanged.
- `grep -rn "isinstance.*FirebaseBackend" server/` returns zero matches.
- `grep -rn "MessengerBackend" server/ tests/` returns zero matches; only `docs/` historical references survive (e.g. `docs/2026-04-28-codebase-review.md`).
- `/healthz` payload still includes `listener_health` snapshots.
- Gateway handler function signatures show specific trait names instead of `MessengerBackend`.
- The H4 entry (with its M1 addendum) is removed from `docs/tracking/backlog.md`; this spec is referenced from `PROJECT-JOURNAL.md`.

---

## Decision log

- **2026-05-01:** Settled architecture as fully decomposed (no surviving `MessengerBackend` aggregator). Reasoning: the testability win cited in the backlog entry only materializes if call sites stop type-hinting the god interface. A surviving aggregator class would make that a code-review concern instead of a structural one.

- **2026-05-01:** `Backend` base contains exactly one method (`aclose`). Earlier draft included `listener_health()` as cross-cutting, but its state vocabulary (`state: starting|live|reconnecting|stopped`, `crash_count`, `last_event_at`) is `SupervisedListener`-specific — Firebase-implementation-specific. Stays Firebase-side, capability-detected via `getattr`. The capability-detection pattern is structurally different from the `isinstance(backend, FirebaseBackend)` leak the H4 entry calls out.

- **2026-05-01:** `_supervised: dict[str, SupervisedListener]` and `_no_op_async_logger` helper stay Firebase-side. Same reasoning — Firebase-internal supervision state, not abstract-surface concern.

- **2026-05-01:** `write_session_meta` placed on `ChannelLifecycle`, not `MessageWriter`. Channel-state writes (the channel's identity record: type, project_key, agent_senders, task) are not message bubbles. Putting it on `MessageWriter` would collapse that trait into "anything that mutates Firebase," which is the god-interface failure mode in miniature.

- **2026-05-01:** Trait formerly named `SpawnCollisionPort` renamed to `ChannelLifecycle`. The trait actually owns the full channel-state CRUD surface (`read_channel_meta`, `has_messages`, `wipe_channel`, `set_channel_hidden`) plus the spawn-collision sub-flow that uses those reads. The narrower name lied. Alternative names considered: `ChannelPort` (vague), `ChannelOps` (too generic), `SessionPort` (overloaded — "session" already means MCP session, collab session, agent session in this codebase). `ChannelLifecycle` wins on accuracy.

- **2026-05-01:** Three dead methods (`update_channel_title`, `update_last_activity`, `fetch_message_text`) drop from the trait surface but stay as concrete `FirebaseBackend` methods. Reasoning: removing them from the abstract surface is what H4 is for; deleting the concrete implementations is a separate concern that risks behavior change if any out-of-tree consumer exists.

- **2026-05-01:** Two more Firebase-only methods discovered during brainstorm verification — `write_response_text` (already `hasattr`-gated) and `make_pending_mirror_writer` (currently `isinstance`-gated). The latter is the actual leaked-abstraction tell the H4 entry calls out. Disposition: convert `isinstance` → `getattr` as part of H4 so the entry's stated success criterion is verifiable.

- **2026-05-01:** Contract-test reorganization stays in a single file (`test_backend_contracts.py`) with one class per trait, rather than splitting into 5+1 files. Reasoning: contract tests verify the abstract surface itself, which production code never sees; mirroring the trait split into the test layout reinforces nothing where the H4 win actually lands. Splitting by behavior (the existing pattern in `test_firebase_hidden.py`, `test_firebase_spawn_decision.py`) ≠ splitting by interface surface.

- **2026-05-01:** Test-fake strategy is "lean (C)" — no new conftest scaffolding. Codebase already uses inline per-test fakes; H4 just lets them optionally declare a trait base for type-checker leverage. The pain H4 fixes was at function signatures (reviewers can't tell what a handler uses), not at fake construction.

- **2026-05-01 (mid-implementation, discovered during Task 3):** Multi-trait parameter intersection classes are plain ABC-unions, not `Protocol` subclasses. Reason: Python rejects `class X(MessageWriter, ..., Protocol)` with `TypeError: Protocols can only inherit from other protocols` because the traits are ABCs. The earlier draft of this spec (and the matching plan) suggested the Protocol form; corrected after the Task 3 implementer hit the runtime error. Behavior is equivalent for type-narrowing and runtime; the change is purely a language-level constraint. Same correction applies wherever an intersection class is needed (Tasks 3, 4, 5).

- **2026-05-01 (mid-implementation, design pairing):** `build_tool_handlers`'s `backend` parameter narrowed to a Protocol-equivalent ABC-union (`_ToolHandlersBackend(MessageWriter, InjectPort, ChannelLifecycle)`) in Task 3 rather than deferred to Task 7. Reason: the closures inside `build_tool_handlers` capture `backend` from enclosing scope (no per-closure annotations exist to narrow), so handler-narrowing for that file lands at the factory boundary regardless. Doing it now (vs Task 7) makes Task 7 strictly less work, makes the trait surface visible mid-implementation, and uses the Protocol shape that's already stable. The original plan Step 4 ("leave `build_tool_handlers` at `MessengerBackend` until Task 7") was based on the assumption of standalone `_handle_*` functions that don't exist; reality is closures.
