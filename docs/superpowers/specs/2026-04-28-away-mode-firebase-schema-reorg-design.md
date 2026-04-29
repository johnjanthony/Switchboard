# Away-mode Firebase Schema Reorganization — Design Spec

**Date:** 2026-04-28
**Status:** Approved (brainstorm)
**Branch (target):** TBD
**Predecessors:**
- `2026-04-23-away-mode-enforcement-design.md` (V1 global away-mode flag)
- `2026-04-24-channel-hide-and-away-mode-toggle-design.md` (per-cwd override mirror, bulk-respond on exit)
- `2026-04-24-cwd-as-channel-and-per-cwd-away-mode-design.md` (cwd-as-channel; two-tier away-mode in Registry)

## Summary

Four coordinated Firebase schema changes that align the published mirror with the conceptual model, unblock cross-device unseen-state sync between phone and Wear OS, and eliminate server-orchestrated transient UI state:

1. **Co-locate per-channel away-mode with the channel.** Move `away_mode/overrides/{cwdKey}` → `channels/{cwdKey}/away_mode`. The override conceptually belongs to the channel; co-locating makes lifecycle alignment automatic (channel wipe removes the override) and removes one Firebase listener on Android.
2. **Group global settings.** Move `away_mode/global` → `global_settings/away_mode`. Opens `global_settings/` as the home for future top-level switches (notification quiet hours, default sender, etc.). Once both moves land, the `away_mode/` top-level node is gone.
3. **Cross-device unseen-state synchronization.** Make `unread_count` actually live (server-incremented, client-cleared) and add `pending_responses` as the canonical Firebase contract for "are there ask_human questions awaiting response?" Both fields are server-maintained. Drop the device-local `_unseenChannels` set so reading a message on phone clears the indicator on watch.
4. **Client-side bulk-respond dialog.** Eliminate the `bulk_respond_dialog/` Firebase node entirely. The phone shows the dialog itself when toggling away-mode off, sourcing pending-question detail from local channel state. The user's decision (send default / skip / cancel + default text) rides as additional fields on the existing `away_mode_commands/` exit command. The server applies the decision when processing the command, then flips the away-mode state.

Alongside the schema move, the Registry's away-mode state shifts from "in-memory authoritative + `away-mode.json` sidecar persisted" to **Firebase-backed read-through cache**: Firebase is the canonical store, the in-memory `_global_away` and `_cwd_overrides` exist only to keep the Stop/AfterAgent turn-end hook fast (it reads `is_cwd_away(cwd)` on every agent turn — can't round-trip to Firebase per call), and the cache is populated by Firebase listeners + a startup snapshot. The `logs/away-mode.json` sidecar is deleted. The pending-request side of Registry (`_pending`) is unaffected — futures can't survive restart, so in-memory is correct.

Firebase data is wiped at deploy. Single-developer tool, channels are dev artifacts.

## Motivation

Three concerns surfaced during 2026-04-26 cwd-as-channel post-merge testing and 2026-04-27 wear-OS work:

1. **Schema-shape friction.** The current `away_mode/{global, overrides/{key}}` layout is orthogonal to the channel structure it logically belongs to. Wiping a channel doesn't wipe its override; the phone keeps a separate listener for overrides; future global flags would have to wedge into the same `away_mode/` node or invent their own siblings.
2. **Unread badges are dead.** `channels/{key}/unread_count` exists in the schema and is read by Phone (`SessionRowComposable.kt:187-188`) and Wear (`MainActivity.kt:275`) — but nothing writes it. Today's "unseen" indicator is a per-device `Set<String>` in `MainViewModel` ([MainViewModel.kt:70-71](../../../android/shared/src/main/java/io/github/johnjanthony/switchboard/MainViewModel.kt#L70-L71)). Phone and Wear each maintain their own; reading on one device does not clear the other.
3. **No public contract for "is there pending?"** The bulk-respond-on-exit-away dialog triggers off `len(registry.pending_for_cwd(cwd)) > 0` — a server-process-internal check. Nothing on Firebase states the canonical answer, so phone-initiated features (e.g., a future "respond to all pending" button independent of away-mode transitions) would have no canonical field to consult.

These three bundle naturally because they share the same `firebase.py` mirror code, the same Android `syncChannel` / `Channel` data class, and the same migration window. Doing them as one branch is one Firebase wipe instead of three.

## Decisions made during brainstorm

- **Selecting the channel is the only "seen" trigger.** Notifications, list visibility, and dismissals do not clear unread state. Matches existing local behavior; smallest contract change.
- **`unread_count` counts every non-Human message.** Questions, notifications, documents, system messages — anything except Human replies. Mirrors today's local-set semantics.
- **`pending_responses` is the public contract** for the bulk-respond-on-exit-away dialog trigger. The phone reads it to decide whether to render the dialog before sending the exit command. The server's in-memory Registry remains authoritative for pending futures and is the source of truth when the helper resolves them on `decision == "send_default"`.
- **Server writes the mirrors atomically.** `unread_count` and `pending_responses` are maintained server-side via `ServerValue.increment(±n)`; clients only write `0` to `unread_count` on `selectChannel`. Atomic increments avoid lost-update races.
- **Per-channel away-mode field is `bool | null`.** `null` (or missing) means "follow global"; `true` means away override; `false` means at-desk override. Matches current Registry semantics.
- **Firebase is the single source of truth for away-mode state.** Registry's `_global_away` / `_cwd_overrides` become a read-through cache populated by Firebase listeners; the `away-mode.json` sidecar is removed. Writes flow through Firebase (`write_away_mode_mirror`); the listener echoes the change back into the cache. Pending futures (`_pending`) stay in-memory.
- **Commands queue stays.** Phone-initiated state changes still flow through `away_mode_commands/`, not direct Firebase writes — the queue is what gives the server its chance to run bulk-respond before committing the away-mode flip. Queue handler now writes to Firebase instead of mutating in-memory state.
- **Pending-dot row-level check uses `pendingResponses > 0`.** The `SessionRowComposable.kt` row-level "this channel has unanswered questions" check collapses to the new field. The `pendingQuestions: Map` shape stays for the channel-detail reply-input UI, which needs the per-question records.
- **Bulk-respond dialog moves to the phone.** Server stops orchestrating the dialog handshake. Phone shows its own dialog (built from local `pendingQuestions` data) before sending the exit command, and the user's decision rides on the command. `bulk_respond_dialog/active` and `bulk_respond_dialog/decision` Firebase paths are removed; `write_bulk_respond_dialog`, `clear_bulk_respond_dialog`, `poll_bulk_respond_decision`, `build_bulk_respond_payload[_for_cwd]` are removed; the queue handler's dialog-handshake branch is replaced with a small decision-applier.
- **Migration is a clean wipe.** Stale `away_mode/*` paths can be ignored; a one-shot startup delete is optional. The `logs/away-mode.json` file is deleted (or left to die — server no longer reads or writes it).

---

## Section 1 — New Firebase shape

```
channels/{key}/
  cwd_canonical: str
  title: str | null
  hidden: bool
  preview: str | null
  last_activity_at: str | null
  messages: { msg_id: ChannelMessage }
  away_mode: bool | null          # NEW: null/missing = follow global
  unread_count: int               # NOW WIRED: server +=, client = 0 on select
  pending_responses: int          # NEW: server-maintained, drives bulk-respond trigger

global_settings/
  away_mode: bool                 # MOVED from away_mode/global

# DELETED: away_mode/ (top-level node and all its children)
```

### Field semantics

| Field | Server behavior | Client behavior |
|---|---|---|
| `channels/{key}/away_mode` | Written by `write_away_mode_mirror` on registry override changes; deleted on override removal; bulk-cleared on global toggle | Phone+Wear read from channel snapshot; UI consults `channel.awayMode ?? globalAway` |
| `channels/{key}/unread_count` | Atomic +1 on every non-Human message append in `write_message` | Client writes `0` on `selectChannel(cwdKey)` |
| `channels/{key}/pending_responses` | Atomic ±n via Registry mirror callback (add/resolve/remove/cancel/timeout); zeroed for all channels at server startup | Read-only on client; UI may bind for indicators |
| `global_settings/away_mode` | Written by `write_away_mode_mirror` when `cwd is None` | Single value-listener on Android replaces the previous `away_mode/global` listener |

---

## Section 2 — Per-channel away-mode co-location

### Server (`firebase.py`)

Rewrite `write_away_mode_mirror`:

```python
async def write_away_mode_mirror(self, cwd: str | None, active: bool | None) -> None:
    from server.canonicalization import to_firebase_key
    if cwd is None:
        await asyncio.to_thread(lambda: db.reference('global_settings/away_mode').set(active))
    else:
        key = to_firebase_key(cwd)
        ref = db.reference(f'channels/{key}/away_mode')
        if active is None:
            await asyncio.to_thread(lambda: ref.delete())
        else:
            await asyncio.to_thread(lambda: ref.set(active))
```

### Bulk-clear on global toggle

The current bulk-clear on global-away exit walks the registry's overrides and writes them out individually under `away_mode/overrides/*`. Replace with a single multi-location update against `channels/`:

```python
updates = {f'channels/{to_firebase_key(c)}/away_mode': None for c in registry.cwd_overrides()}
db.reference().update(updates)
```

(`null` in a multi-location update deletes the key.)

### `Registry` — Firebase-backed cache

`_global_away: bool` and `_cwd_overrides: dict[str, bool]` continue to exist as in-memory state, but they are now a **read-through cache populated by Firebase listeners**, not the authoritative store. The cache exists for one reason: the Stop/AfterAgent turn-end hook reads `is_cwd_away(cwd)` on every agent turn, and a Firebase round-trip per check would be too slow. The cache is rebuilt from a Firebase snapshot at startup and kept in sync via listeners.

**Removed:**

- `_load_away_mode` and `_save_away_mode` methods.
- The `logs/away-mode.json` sidecar file (deleted at deploy or left to die — server no longer reads or writes it).
- All `_save_away_mode()` calls scattered through `set_global_away`, `set_cwd_override`, `remove_cwd_override`.

**Reshaped:**

- `set_global_away(active)`, `set_cwd_override(cwd, active)`, `remove_cwd_override(cwd)`: become thin wrappers over `write_away_mode_mirror`. They compute the multi-location update (e.g. global toggle that needs to clear all overrides), write to Firebase, and return. The cache update happens via the listener echoing the change back.
- `is_cwd_away(cwd)`, `cwd_overrides()`: continue reading from the cache (unchanged callers, fast local reads).

**Added:**

- Startup snapshot load: after Firebase init, before the gateway accepts requests, read `global_settings/away_mode` and walk `channels/*/away_mode` to populate the cache.
- Firebase listener wiring: a value listener on `global_settings/away_mode` updates `_global_away`; channel-level listeners (likely sharing the existing channels listener path) update `_cwd_overrides` on add / change / remove.
- Listener-vs-write idempotence: when the server itself writes a change and the listener fires back, the cache compare-and-update is a no-op. No double-fire side effects.

**Commands queue is unchanged in shape.** Phone-initiated `enter_global` / `exit_global` / `enter_cwd` / `exit_cwd` continue to flow through `away_mode_commands/`, not direct Firebase writes — the queue is what gives the server its chance to run the bulk-respond flow before committing the away-mode flip. Queue handler now calls `write_away_mode_mirror` instead of mutating in-memory state directly; the listener picks up the change and the cache updates.

### `wipe_channel`

Already deletes channel-scoped paths. Override is now a channel-scoped path, so it is wiped automatically — that's the lifecycle-alignment win the backlog called out. No extra wipe step needed.

---

## Section 3 — Global settings grouping

Trivial follow-on from Section 2: `cwd is None` branch in `write_away_mode_mirror` writes to `global_settings/away_mode` instead of `away_mode/global`. Android global-listener target moves correspondingly.

`global_settings/` opens for future tenants (quiet hours, default sender) — not populated by this branch.

Once Sections 2 and 3 land, the `away_mode/` top-level node is dead and can be deleted.

---

## Section 4 — Cross-device unseen-state synchronization

### `unread_count` — server-incremented, client-cleared

**`firebase.py · write_message`:** every non-Human message append (i.e. `message_type != "human"`, which today is the same condition that gates FCM notification dispatch) atomically increments `channels/{key}/unread_count`:

```python
db.reference(f'channels/{key}/unread_count').set(firebase_admin.db.ServerValue.increment(1))
```

(Or the SDK-equivalent atomic-increment call.)

**`MainViewModel.selectChannel`:** writes `0`:

```kotlin
fun selectChannel(cwdKey: String) {
    _selectedCwdKey.value = cwdKey
    channelsRef.child(cwdKey).child("unread_count").setValue(0)
}
```

The local `_unseenChannels: MutableStateFlow<Set<String>>` and its mutation in `addMessage` are removed. Phone (`SessionRowComposable.kt`) already binds the badge to `channel.unreadCount`. Wear (`MainActivity.kt:260, 274`) replaces `unseenChannels.contains(...)` checks with `channel.unreadCount > 0`.

**Why atomic-increment:** two concurrent message appends could otherwise lose an increment under a read-modify-write. `ServerValue.increment` is the Firebase RTDB primitive for this.

**Race during select:** if a message arrives the same instant a `select` writes 0, both writes land; the field ends at the post-increment value (worst case the badge flashes briefly, but ends correctly). Acceptable.

### `pending_responses` — Registry mirror

**Registry callback:** add a `pending_mirror: Callable[[str, int], None]` to Registry, invoked on every mutation that changes pending count for a `cwd`. The callback is fired synchronously from the Registry method; the implementation schedules the actual Firebase write as an asyncio task so Registry's sync interface is preserved. (Errors in the fire-and-forget task are logged via the standard surface_error path.)

| Registry mutation | Effect |
|---|---|
| `add(cwd, sender, ...)` | `pending_mirror(cwd, +1)` |
| `resolve(cwd, sender)` | `pending_mirror(cwd, -1)` (only if a record was popped) |
| `remove(cwd, sender)` | `pending_mirror(cwd, -1)` (only if a record was popped) |
| `cancel_pending_for_cwd(cwd)` | `pending_mirror(cwd, -len(victims))` (single combined call) |
| Timeout dispatch (when a future is fulfilled with `__TIMEOUT__` and the entry is removed) | `pending_mirror(cwd, -1)` |

The callback writes atomically:

```python
async def _pending_mirror(cwd: str, delta: int) -> None:
    key = to_firebase_key(cwd)
    db.reference(f'channels/{key}/pending_responses').set(ServerValue.increment(delta))
```

Single chokepoint — no scattered direct `db.reference` writes from gateway/spawn paths.

**Bulk-respond trigger swap.** In `gateway.py`'s away_mode_commands handler, replace:

```python
pending = registry.pending_for_cwd(canonical)
if not pending:
    continue
```

with:

```python
count = await backend.read_pending_responses(canonical)  # new backend method
if count <= 0:
    continue
```

When the phone builds the dialog payload (Section 5), it sources per-question detail from the channel's local `pendingQuestions: Map<String, Pending>` — populated by the existing channel-messages listener. The server's Registry-derived `pending` list is consulted only when the queue handler applies a `decision == "send_default"` command (resolving each pending future and writing the default-text response). If Registry is empty for the cwd at that moment, the resolution loop simply iterates zero entries — no special drift-warning path is needed. The original drift hazard (phone reading `pending_responses` directly to drive the dialog) is unreachable under the client-driven design adopted in Section 5.

**Startup recovery.** After Firebase init, before the gateway starts accepting requests:

```python
async def reset_all_pending_responses(self) -> None:
    snap = db.reference('channels').get(shallow=False) or {}
    updates = {f'channels/{key}/pending_responses': 0 for key in snap.keys()}
    if updates:
        db.reference().update(updates)
```

Matches in-memory Registry (which starts empty). Note: `unread_count` is NOT reset — it represents user reading state, restart-independent.

---

## Section 5 — Client-side bulk-respond dialog

### New command-payload shapes

The `away_mode_commands/` queue command for exits gains optional decision fields. Schema (additive — enters are unchanged):

```
{
  type: "exit_global" | "exit_cwd",
  cwd?: str,                          # required for exit_cwd
  decision?: "send_default" | "skip" | "cancel",   # required when phone has run the dialog
  default_text?: str,                  # required when decision = "send_default"
  issued_at: str
}
```

When `pending_responses` is `0` for the affected scope, `decision` may be omitted entirely — the queue handler treats absent-decision-with-zero-pending as "just flip the state."

When `pending_responses > 0` and `decision` is absent, the queue handler logs a `bulk_respond_decision_missing` warning and treats it as `cancel` (no state change). This guards against malformed phone-side flows; under normal operation the phone always populates the decision when pending exists.

### Phone-side flow

1. User toggles the away-mode pill off (global or per-cwd).
2. Phone reads `pending_responses`:
   - For `exit_global`: sum across all channels.
   - For `exit_cwd`: read `channels/{key}/pending_responses`.
3. If sum is `0`: send the exit command with no decision field. Done.
4. If sum is `> 0`: render the bulk-respond dialog. Source per-question detail from each affected channel's `pendingQuestions: Map<String, Pending>` (already populated by the channel listener — covers both visible and hidden channels). Group by cwd; show sender, question text, and an editable default-text field.
5. User picks `Send default to all`, `Skip`, or `Cancel`. On confirm, phone sends the exit command with the chosen `decision` (and `default_text` when applicable).

If the user backs out before confirming, no command is sent — away-mode state stays as it was. Idempotent.

### Server-side decision application

Queue handler reads the command, branches on `decision`:

- `decision == "send_default"`: for each pending entry in `registry.pending_for_cwd(cwd)` (or `registry.all_pending()` for global), write `responses/{cwd}__{sender}` with the default text. The existing response listener resolves the futures via `registry.resolve(cwd, sender, default_text)`. After all writes, call `write_away_mode_mirror` to flip the state.
- `decision == "skip"`: do not write any responses. Call `write_away_mode_mirror` to flip the state, leaving pending questions in place (they continue to wait for individual replies or eventual timeout).
- `decision == "cancel"`: do nothing. State stays as it was.

### Removed Firebase paths and methods

- `bulk_respond_dialog/active` and `bulk_respond_dialog/decision` (Firebase paths).
- `firebase.py · write_bulk_respond_dialog`, `clear_bulk_respond_dialog`, `poll_bulk_respond_decision`.
- `gateway.py · build_bulk_respond_payload`, `build_bulk_respond_payload_for_cwd`, `bulk_respond_send_to_all`, `bulk_respond_send_to_all_for_cwd`, `bulk_respond_skip`, `bulk_respond_cancel`, `bulk_respond_cancel_for_cwd` (most of these collapse into a small `_apply_bulk_respond_decision(cwd|None, decision, default_text)` helper that writes responses).
- The dialog-handshake branch in the queue handler (the `await backend.write_bulk_respond_dialog(...)` / `await backend.poll_bulk_respond_decision()` flow) — replaced with a synchronous decision-application call.

### Drift handling

If the server restarts and Firebase still shows `pending_responses > 0` for a cwd whose Registry is empty (agents are dead, futures are gone), the phone may still offer a dialog. When `decision == "send_default"` arrives at an empty Registry, response writes target slots with no waiting future and are no-ops (existing behavior of `responses/` listener). User-visible consequence: "send default" appears successful but resolves nothing. Acceptable — the agents were already disconnected, and the next message into those channels will reset state.

---

## Section 6 — Android client changes

All changes in `android/shared/src/main/java/io/github/johnjanthony/switchboard/MainViewModel.kt` and the consumers in `android/app/...` (Phone) and `android/wear/...` (Wear).

### `MainViewModel.kt`

**Drop:**

- `_unseenChannels: MutableStateFlow<Set<String>>` and the `unseenChannels: StateFlow<Set<String>>` accessor (lines 70-71).
- The local-set mutation in `addMessage` (lines 260-262).
- The `awayModeRef.child("overrides")` listener block in `setupAwayModeListener` (lines 297-308).
- The `setupBulkRespondListener()` and the `_bulkRespondDialog` flow + `bulk_respond_dialog/active` listener (lines 333-355). The dialog is now phone-driven; no Firebase listener is needed.
- `submitBulkRespond(action, defaultText)` — replaced by the new client-side flow that bundles the decision into the exit command.

**Re-target:**

- `awayModeRef.child("global")` listener → `database.getReference("global_settings/away_mode")`. Variable-rename pass: `awayModeRef` becomes `globalAwayRef` (or inlined).

**Add:**

- `Channel` data class field: `awayMode: Boolean? = null` and `pendingResponses: Int = 0`.
- `syncChannel`: read both new fields from the snapshot.
- New flow for the bulk-respond dialog: when `requestAwayModeToggle(cwdKey, desired=false)` is invoked and there are pending responses for the affected scope, surface a dialog (a new `MutableStateFlow<BulkRespondPrompt?>` populated from local `pendingQuestions` data), wait for the user's decision, then send an exit command that includes `decision` + `default_text`.

**Change:**

- `selectChannel(cwdKey)`: write `0` to `channels/{cwdKey}/unread_count` via Firebase. Drop the local-set mutation.
- `isAwayActive(cwdKey)`: consult `channels[cwdKey]?.awayMode ?: _globalAway.value`.
- `_cwdOverrides` flow: derive from `_channels` if any caller still depends on the map shape; otherwise remove. Inspect call sites (`PerCwdAwayPill`, `requestAwayModeToggle`) to determine.
- `exitGlobalAway()` / `exitCwdAway(cwd)`: gain `decision: String?` and `defaultText: String?` parameters. The command payload includes them when present.

### `BulkRespondDialog.kt`

The existing dialog Composable can be largely reused — its inputs already come from a `BulkRespondPayload` data class (`sections: List<BulkRespondSection>`). Source change only: instead of being populated from a Firebase snapshot, the payload is built locally from `_channels` filtered by `pendingQuestions` non-empty entries. The `submitBulkRespond` callback wires to the new exit-command-with-decision path instead of writing to `bulk_respond_dialog/decision`.

### Phone (`SessionRowComposable.kt`)

The badge already binds to `channel.unreadCount` (line 187-188). No change.

**Pending-question dot:** line 165 currently does `if (channel.pendingQuestions.values.any { !it.cancelled })`. Replace with `if (channel.pendingResponses > 0)`. The `pendingQuestions: Map<String, Pending>` field itself stays — the channel-detail reply-input UI needs the per-question records to render the reply box.

If any sites consult `unseenChannels.contains(...)`, swap to `channel.unreadCount > 0`. (Search needed during implementation.)

### Wear (`android/wear/src/main/java/io/github/johnjanthony/switchboard/MainActivity.kt`)

Replace `unseenChannels.contains(channel.cwdKey)` (line 260) with `channel.unreadCount > 0`. Collapse the unread-marker rendering (line 274-277) to bind off `channel.unreadCount` directly.

---

## Section 7 — Migration

Single clean Firebase wipe at deploy. After deploy:

- The old `away_mode/` top-level node is stale data; either ignore it (RTDB doesn't surface unused nodes to the client) or one-shot delete on first server startup post-deploy. Recommend: one-shot delete, since it costs nothing and removes confusion in the Firebase console.
- `unread_count` and `pending_responses` start absent on every channel. Server writes them lazily on first message / first pending mutation. Reads of absent fields default to `0` on client.
- `logs/away-mode.json` sidecar file is no longer used. The file can be deleted manually or left in place — server code no longer references it. Recommend: delete on deploy to avoid confusion.
- Server startup reads the Firebase snapshot to populate the cache. If Firebase is empty at deploy (post-wipe), cache starts empty (matches the post-wipe truth — global default `False`, no overrides).

No phased rollout. Single-developer tool.

---

## Section 8 — Testing

### Server contract (`tests/test_firebase.py` and friends)

- `write_away_mode_mirror(None, True)` writes `global_settings/away_mode = True`; old `away_mode/global` is not touched.
- `write_away_mode_mirror(cwd, True)` writes `channels/{key}/away_mode = True`; old `away_mode/overrides/*` is not touched.
- `write_away_mode_mirror(cwd, None)` deletes `channels/{key}/away_mode`.
- Bulk-clear on global toggle deletes `away_mode` field on every channel that had an override.
- `write_message` of `type="question"` increments both `unread_count` and `pending_responses`; of `type="notification"` or `type="document"` increments `unread_count` only; of `type="human"` increments neither.
- `wipe_channel` deletes `unread_count`, `pending_responses`, and `away_mode` along with existing fields.

### Registry — away-mode cache (`tests/test_registry.py`)

- Cache is empty on construction; populated only via the Firebase snapshot-load path.
- Snapshot load: given a fixture Firebase state with `global_settings/away_mode = true` and two channels with `away_mode` overrides, the cache reflects both after `load_snapshot()`.
- Listener fire: a Firebase value-event handler updating `global_settings/away_mode` updates `_global_away`. Same for per-channel `away_mode` events.
- `set_global_away` / `set_cwd_override` / `remove_cwd_override` issue Firebase writes (via `write_away_mode_mirror`) and do not mutate in-memory state directly. Listener fire is what updates the cache.
- The `logs/away-mode.json` file is never read or written.

### Registry — pending mirror (`tests/test_registry.py`)

- Inject a mock `pending_mirror` callback and assert call counts and signs for each mutation path.
- `cancel_pending_for_cwd` invokes the callback once with `delta = -len(victims)`, not N times with `-1`.

### Bulk-respond decision application (`tests/test_gateway.py`)

- Exit command with `decision` absent and `pending_responses == 0` → state flips, no responses written.
- Exit command with `decision == "send_default"` and matching Registry → response writes for each pending entry, then state flips.
- Exit command with `decision == "skip"` → state flips, no responses written.
- Exit command with `decision == "cancel"` → no state change, no responses written.
- Exit command with `decision == "send_default"` but empty Registry (post-restart) → resolution loop iterates zero entries; state flips cleanly. No special warning needed (the agents whose futures would have been resolved are already gone).
- Exit command with `decision` absent but `pending_responses > 0` → `bulk_respond_decision_missing` warning logged; treated as `cancel` (no state change).
- Old Firebase paths `bulk_respond_dialog/active` and `bulk_respond_dialog/decision` are never written.

### Path-asserting tests

Any existing test that asserts paths under `away_mode/global` or `away_mode/overrides/*` needs the new path. `test_messenger_contract` (signature-only) is unaffected.

### Android (manual)

No formal Android test suite exists in the repo. Manual verification:

1. Spawn phone+watch on the same Firebase project.
2. Send a notification to a cwd. Both devices show unread badge / dot.
3. Open the channel on phone. Watch's badge clears within Firebase listener latency (~1s).
4. Toggle the per-cwd away pill on phone. Watch reflects the new state without any new listener — proves the channel-snapshot path covers it.

---

## Section 9 — Out of scope (explicit)

- `_pending` (the request-futures map in Registry) stays in-memory only — futures can't survive restart, and `pending_responses` is a one-way mirror for UI/trigger purposes, not state recovery.
- `_sessions` (active collab sessions, keyed by cwd) and `_recently_ended` (60-second breadcrumb of just-ended sessions, used to disambiguate "partner ended first" from "never a member" on a late-arriving `end_collab`) — both stay in-memory only. They hold asyncio futures and sub-second race-mitigation state respectively; neither survives restart, neither needs to. No external observer (phone, watch) consults this state — collab message exchanges already flow through the existing per-channel Firebase listener — so there is no Firebase mirror to maintain.
- Presence / "actively viewing" tracking on a channel.
- New `global_settings/` tenants (quiet hours, default sender, etc.) — folder is opened for future, not populated by this branch.
- The `away_mode_commands/` queue itself stays. Considered replacing it with direct phone-initiated Firebase writes; rejected on architectural grounds — exits require server-side orchestration (decision application + state flip) that a "phone writes the field" pattern can't carry cleanly. The queue is correctly designed and not subject to revision.

---

## Open questions

None at brainstorm sign-off. Implementation may surface call-site decisions (e.g., whether `_cwdOverrides` flow can be removed entirely or needs to be derived); those are local judgment calls and don't affect the spec.
