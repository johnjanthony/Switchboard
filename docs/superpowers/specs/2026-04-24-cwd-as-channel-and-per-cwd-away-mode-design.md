# Cwd-as-Channel and Per-cwd Away Mode — Design Spec

**Date:** 2026-04-24
**Status:** Approved (brainstorm)
**Branch (target):** TBD (next branch after `channel-hide-and-away-mode-toggle` ships)
**Predecessors:**
- `2026-04-23-away-mode-enforcement-design.md` (V1 global away-mode flag and Stop-hook protocol)
- `2026-04-24-channel-hide-and-away-mode-toggle-design.md` (V1 hide + pill-chip toggle + bulk-respond on exit)

## Summary

Replace the agent-reported `channel_id` with the agent's canonical working directory (`cwd`) as the single namespace for channels, sessions, and routing keys. Replace the global `away_mode_active` boolean with a layered model: a global flag plus per-`cwd` overrides. Re-key blocking exchanges (`ask_human`, `message_and_await_agent`) by `(canonical_cwd, sender)` so a phone reply for a cancelled or superseded question lands on whoever is currently waiting in that slot — eliminating the silent-loss bug surfaced 2026-04-24. Refresh the Android UI from a top-level `ScrollableTabRow` to a list-based two-page model (session list + session view) more in line with modern chat idioms. Drop `request_id` from the wire protocol entirely — it remains a server-internal UUID for logs.

This is a heavy refactor. There is no migration path. Firebase data is wiped pre-cutover (already done as of authoring).

## Motivation

Three concerns that the 2026-04-24 work surfaced and parked:

1. **Per-channel away-mode.** V1's global flag means an at-desk Claude session and an away-mode session can't coexist — the Stop hook blocks both. Real concurrent usage (per testing pattern: away-mode session for one task, at-desk session for another) requires per-session resolution.
2. **At-desk `ask_human` redirect under per-channel state.** The 2026-04-24 redirect uses the global flag. Under per-channel state, the redirect should consult *this channel's* flag, naturally handling spawned-for-phone sessions that should keep blocking-ask behavior even when a terminal session's flag is off.
3. **Reply-routing race.** When an agent's `ask_human` is cancelled (Ctrl-C, restart, MCP drop), the registry's `request_id` entry is removed, and a subsequent phone reply at `/responses/{request_id}` is dropped as `unknown_correlation`. The user's effort silently vanishes.

These bundle naturally because they share the same registry-level state and Android reply-path code. Rather than designing each separately, this spec collapses the channel/session concept to `cwd` and re-keys routing by `(cwd, sender)`, which solves all three without introducing new identifier categories.

## Decisions made during brainstorm

- **Cwd-as-channel.** Cwd is materially more reliable than agent-reported `channel_id` (system-prompt anchored, doesn't drift mid-conversation). The two concepts collapse cleanly: in real-world usage 1 cwd = 1 session = 1 phone tab, and collab is 1 cwd = N sessions = 1 tab. Heavy refactor approved; no migration since Firebase data is wiped.
- **No SessionStart-hook bootstrap.** Considered using Claude Code's `session_id` (via a SessionStart hook + PPID-keyed local file lookup) to harden against agent-reported drift. Rejected: drift target moves but doesn't vanish (agent still has to remember the bootstrap value across tool calls), and the cost (new hook + Windows verification + SKILL.md bootstrap step) outweighs the marginal gain.
- **`(cwd, sender)` as the routing key for blocking exchanges.** Supersede semantics: a new `ask_human` in an occupied slot cancels the old future. Constraint: never run two agents with the same sender in the same cwd (collab agents pick distinct names).
- **Title parameter on every messaging tool.** Optional at the wire, mandated by SKILL on first call, omit-as-no-change semantics on subsequent calls. Server truncates at 80 chars. No spawn-prompt seeding — fresh agents construct from leaf folder name.
- **Title propagation to collab partners via server-side prepend** (not via return value). Partner-delivery path only (Firebase write stays clean); only-when-changed firing.
- **Two-tier away-mode state**: `_global_away: bool` + `_cwd_overrides: dict[str, bool]`. Override wins; else global.
- **Android UI overhaul to list-based nav**: Page A (session list) + Page B (session view); `ScrollableTabRow` removed.

---

## Section 1 — Canonical-cwd: the unified namespace

Every server-side data structure formerly keyed by `channel_id` is now keyed by **`canonical_cwd`** — the agent's working directory after server-side normalization.

### Canonicalization rules

Applied on every cwd ingress:

1. Resolve to absolute path, collapsing `.` and `..` segments.
2. Convert backslashes to forward slashes.
3. Lowercase the drive letter on Windows (`C:\` → `c:/`).
4. Convert Git-Bash-style `/c/Work/...` → `c:/Work/...` (detect leading `/<single-letter>/` on Windows and rewrite to drive form).
5. Strip trailing slash.

Result example: `c:/work/switchboard`.

### Firebase key form

Firebase paths can't contain `/`. The Firebase channel key is the canonical form with slashes flattened to `__`: `c:/work/switchboard` → `c:__work__switchboard`. The unflattened canonical form is also stored as a separate `cwd_canonical` field for popover display.

### Ingress points

Canonicalization is applied at:
1. Every MCP tool call carrying `cwd` (from agent's `$PWD` or system-prompt).
2. The Stop hook's `GET /away-mode?cwd=<stdin_cwd>`.
3. The Android phone reply path (server canonicalizes when reading the Firebase write).
4. The spawn handler's target-workspace path.

### Validation

If canonicalization fails (path doesn't exist on the server's filesystem, or syntactically invalid), the server returns an error and refuses the call. Better to surface a bad cwd loudly than to silently fall back to a stale or mismatched channel.

### Naming

Tool signatures rename `channel_id` → `cwd`. Server-internal code uses `canonical_cwd` or `cwd_key` for the normalized form. The Firebase `channels/` namespace name is retained (no rename to `cwds/`) — internal hierarchy term, not user-facing.

---

## Section 2 — Registry, pending-routing, and supersede

### Registry primary key

`Registry._pending` re-keys from `request_id` → `(canonical_cwd, sender)`:

```python
self._pending: dict[tuple[str, str], PendingRequest] = {}
```

`request_id` (UUID) becomes a server-internal field on `PendingRequest` for log correlation; it is not the lookup key and does not appear in the Firebase wire protocol.

### `PendingRequest` shape

```python
@dataclass
class PendingRequest:
    cwd: str            # canonical
    sender: str
    request_id: str     # UUID, server-internal
    future: asyncio.Future
    started_at: datetime
    msg_id: str | None  # Firebase message id for the question
```

### Add / supersede

When `ask_human(cwd=C, sender=S, ...)` arrives:

1. If `(C, S)` is already in `_pending`, the existing entry is **superseded**:
   - The existing future is `cancel()`-ed (raises `CancelledError` in any code still `await`-ing it; in practice the previous agent is dead).
   - The existing entry is removed from `_pending`.
   - The Firebase question entry for the previous request is marked `cancelled: true`.
2. A fresh `PendingRequest` is created and stored under `(C, S)`.
3. The new question is written to Firebase as usual.

### Resolve

When a phone reply arrives at `/responses/<canonical_cwd_key>__<sender>`:

1. Server canonicalizes the path components, looks up `_pending[(cwd, sender)]`.
2. If found: `future.set_result(reply_text)`, remove the entry from `_pending`, write resolution-confirmation to Firebase so the phone UI marks the question answered.
3. If not found: log `unknown_correlation` with the `(cwd, sender)` pair; phone shows a "Reply couldn't be delivered — the question was withdrawn" toast.

### Cancel (agent-side)

When `ask_human`'s `await` raises `CancelledError` (Ctrl-C, agent restart, MCP drop):

1. `gateway.py`'s except block calls `registry.remove(cwd, sender)`.
2. Server marks the Firebase question entry as `cancelled: true` (closes the silent-loss bug).
3. Phone UI reads the cancelled flag, hides the reply input on that question, shows a "Withdrawn" badge on the message bubble.

### Timeout

Existing per-request timeout logic stays, just keyed by `(cwd, sender)` instead of `request_id`. On timeout, server removes the entry, sends Firebase timeout-followup to the phone, and the agent's tool call returns `__TIMEOUT__`.

### Collab session registry

`_sessions` re-keys from `channel_id` → `canonical_cwd`. `CollabSession.session_id` becomes `CollabSession.cwd`. BYO collab no longer needs a pre-shared session id; two agents with distinct senders auto-attach to the same cwd-keyed session.

### Persistence

`_pending` stays in-memory only. On server restart, all pending futures are abandoned; the corresponding Firebase questions are marked `cancelled: true` during the restart cleanup pass (the existing collab-restart cleanup at `server/main.py:55-63` is the model). Phone UI updates accordingly.

Away-mode state and channel titles persist (Sections 3 and 4).

---

## Section 3 — Firebase schema and Android UI

### Firebase structure

```
channels/
  <canonical_cwd_key>/
    title: "Per-cwd away mode"          # latest title; drives tab label
    cwd_canonical: "c:/work/switchboard"# verbatim, for popover display
    hidden: false
    last_activity_at: "<ISO timestamp>"
    preview: "Last message text snippet..."
    unread_count: 0
    messages/
      <msg_id>/
        type: "question" | "notify" | "agent" | "document" | "system"
        sender: "Claude" | ...
        title: "..." | null
        text: "..."
        format: "plain" | "markdown"
        timestamp: ...
        cancelled: false                  # only meaningful for type=question
        # type=question fields: suggestions, request_id (UUID echo, internal)

responses/
  <canonical_cwd_key>__<sender>: {
    text: "..."
    written_at: ...
  }

away_mode/
  global: false
  overrides/
    <canonical_cwd_key>: bool

away_mode_commands/
  <auto-id>: { type: "enter_global" | "exit_global" | "enter_cwd" | "exit_cwd",
               cwd: "..." | null, issued_at: "..." }
```

Notes:
- **Channel-level `title`** is the latest title written by any messaging tool. Updated on every messaging call that includes a non-empty title. Calls with omitted `title` leave it unchanged.
- **Per-message `title`** captures the title at write time; used by the timeline to render an inline subheader on change. Stored as null when omitted by the tool call.
- **`responses/<key>__<sender>`** is the single-slot write target for phone replies — keyed by `(cwd, sender)`. New replies overwrite the slot (consistent with supersede: only one current pending per slot).
- **`away_mode/`** replaces the V1 global `away_mode_active` flag (Section 4).
- **`away_mode_commands`** queue feeds the server's command processor; both global and per-cwd actions go through it.

### Android UI

#### Page A — Session list (home screen)

- Scrollable list of non-hidden channels by default, ordered by `last_activity_at` desc.
- Row content: title (primary), preview snippet of last message (secondary, truncated), relative timestamp (e.g. "2m", "1h", "Yesterday"), unread badge count.
- Status indicators on the row: pending-question dot, away-mode badge, document-attached glyph.
- Long-press a row → context menu surfacing: "Hide channel" / "Unhide channel", "Tab info" (popover with `cwd_canonical`).
- App-bar overflow menu carries `Show hidden (N)` toggle (greyed when N=0).
- App-bar carries the global away-mode pill (single tap to enter; long-press confirm to exit, mirroring V1 confirm-on-exit).

#### Page B — Session view

- Reached by tapping a row on Page A. Back nav returns to Page A.
- Existing message-list rendering, with per-message inline title subheader rendered when message N's title differs from message N-1's title.
- Per-channel away-mode pill chip in the toolbar — scoped to *this* session's cwd. Behavior depends on global resolution:
  - Global off + override absent → pill is "off." Tap → enters per-cwd (sets override `True`).
  - Global on + override absent → pill shows "On (global)." Long-press confirm → exits per-cwd (sets override `False`, exempting cwd).
  - Override present → pill reflects override directly; opposite action toggles.
- Pill writes per-cwd commands into `away_mode_commands` queue. Server consumes commands uniformly.
- Tab-info icon in the app bar → popover showing `cwd_canonical`, hidden toggle, away-mode toggle (redundant with pill, discoverable).

#### Reply input lifecycle

The reply input box and send button are tied to "is there a pending question for `(cwd, sender)` in this channel?":
- Pending exists → input visible, send enabled.
- No pending (resolved, cancelled, withdrawn, or never existed) → input hidden.

Withdrawn questions display the original message bubble with reduced opacity and a "Withdrawn" badge but no input. If a new question arrives later for the same `(cwd, sender)`, the input reappears bound to that one.

If a phone reply submission races a withdrawal (already in flight when the cancelled flag arrives), server logs `unknown_correlation` and the phone surfaces a toast: *"Reply couldn't be delivered — the question was withdrawn."* No retry prompt; if a current question existed, the reply would have routed there automatically.

#### Hidden channel UX

- Hide: long-press a row on Page A → context menu → "Hide channel" → writes `hidden=true`. Row drops out of the default list.
- Browse hidden: app-bar overflow → `Show hidden (N)` toggle. When on, hidden rows reappear in the same list intermixed by `last_activity_at`, visually distinguished (reduced opacity, italic title, "hidden" badge).
- Unhide:
  - Long-press a hidden row → context menu → "Unhide channel" (writes `hidden=false`).
  - Auto-unhide on `ask_human` (V1 behavior preserved): server writing a `question` to a hidden channel also writes `hidden=false`. `notify_human` and `send_document_human` writes do **not** auto-unhide — only blocking-attention (`question`) writes do, matching V1 intent.
  - Auto-unhide on spawn-collision Continue or Clear (Section 5).
- FCM suppression while `hidden=true` is preserved from V1 unchanged.

#### Notification deep-linking

FCM notification tap navigates directly to Page B for the relevant channel. Back from Page B → Page A. Existing FCM channel-routing (Updates / Questions / Documents) is unchanged.

### What's NOT changing on the Android side

- FCM topic split (`questions` / `notifications` / `updates`).
- Three-channel notification routing logic.
- Suggestion-button rendering on questions.
- BulkRespondDialog mechanics (just rescoped — see Sections 4 and 5).

---

## Section 4 — Per-cwd away-mode and the Stop hook protocol

### Server state (replaces global flag)

`Registry`'s `_away_mode_active` and `_away_mode_entered_at` are removed and replaced with:

```python
self._global_away: bool = False
self._cwd_overrides: dict[str, bool] = {}     # canonical_cwd → True/False
self._away_mode_callback: Callable[[str | None, bool], None] | None
# callback signature: (cwd_or_None, active). cwd=None for global flips.
```

### Resolution rule

`is_away_mode_active(cwd: str) -> bool`:

1. If `cwd in self._cwd_overrides`: return `self._cwd_overrides[cwd]`.
2. Else: return `self._global_away`.

Override stores literal `True`/`False` to disambiguate intent: under global=on, override=`False` means "this cwd is exempt from global"; under global=off, override=`True` means "this cwd is in away mode anyway." Either form represents "differs from global."

### Operations

| Action | Effect |
|---|---|
| Enter global | `_global_away = True`; `_cwd_overrides = {}` (clear) |
| Exit global | `_global_away = False`; `_cwd_overrides = {}` (clear) |
| Enter cwd `C` | `_cwd_overrides[C] = True` (no-op when current resolution for C is already `True`) |
| Exit cwd `C` | `_cwd_overrides[C] = False` |

Future sessions spawned while global=on inherit away-mode automatically (no override → falls through to `True`).

### Sidecar persistence

```json
{
  "global": true,
  "overrides": {
    "c:/work/switchboard": false
  }
}
```

Loaded on startup; persisted on every state change. No backward-compat with V1 sidecar shape; old file is silently overwritten on first write (Firebase wipe means no mirror inconsistency).

### Firebase mirror

```
away_mode/
  global: bool
  overrides/
    <canonical_cwd_key>: bool
```

`MultiBackend.write_away_mode_mirror(cwd: str | None, active: bool)` handles both global and per-cwd writes. The startup `_wire_away_mode_mirror` pass pushes both `global` and the full overrides map.

### Tool signatures (agent-facing)

```python
async def enter_away_mode(cwd: str) -> str    # sets override True
async def exit_away_mode(cwd: str) -> str     # sets override False
```

Agent passes `cwd` (from `$PWD` or system-prompt). Server canonicalizes. Idempotent. Global enter/exit is **not** an MCP tool — it is human-driven from Android via the `away_mode_commands` queue.

### Stop hook protocol

`scripts/turn-end-hook-away-mode.py`:
- Reads `cwd` from Claude Code's stdin JSON.
- Calls `GET /away-mode?cwd=<urllib.parse.quote(cwd)>`.
- Server canonicalizes the query param, applies the resolution rule, returns `{"active": bool}`.
- Fail-open: any error (timeout, connection refused, malformed response, missing `cwd`) → silent exit 0. Block/deny output only when `active=true`.

The `REDIRECT_REASON` text and overall hook script structure are unchanged.

### At-desk redirect

`gateway.py`'s `ask_human` redirect-decision changes from `if not registry.is_away_mode_active():` to `if not registry.is_away_mode_active(cwd):` — per-cwd resolution drives the redirect for that call. Same downgrade behavior (notify-type write, no PendingRequest, returns the redirect-error string).

### Bulk-respond on global exit

When the Android global pill issues `exit_global`:

1. Server gathers all live `_pending` entries across all cwds.
2. Phone displays a multi-section dialog: pending questions grouped by channel (cwd), each with the question text and per-question metadata.
3. Three actions:
   - **Send to all** — uses a pre-drafted default response (editable in the dialog text field). On confirm, server resolves every listed pending in parallel via the standard `(cwd, sender)` resolution path. Global exit completes.
   - **Skip** — no responses sent; pending entries remain; global exit completes; user handles each individually via Page B.
   - **Cancel** — abort the global exit entirely; dialog dismisses; global stays on.

### Bulk-respond on per-cwd exit

When the Android per-channel pill issues `exit_cwd` for cwd `C`:

1. Server gathers pending entries for `(C, *)` only.
2. Same three-action dialog, scoped to that cwd's pending questions.

Per-cwd exit applies the override (`_cwd_overrides[C] = False`); global stays as-is.

---

## Section 5 — Spawn flow

### Setup

Today's spawn flow (`server/spawn.py`):
1. User taps "spawn agent" on phone, picks workspace path, optionally collab settings.
2. Server creates a pending spawn record; the launching surface (terminal-side script) picks it up.
3. Agent launches with the spawn prompt.

Under cwd-as-channel:
- The spawn prompt drops `channel_id` and replaces it with: *"Your `cwd` for switchboard tool calls is your current working directory. Read it via `$PWD` or the 'Primary working directory' field of your system prompt."*
- The collab-spawn variant drops the shared-`channel_id` instruction; agent enrollment is by-cwd.
- The first-call title guidance is added (matches SKILL.md text).
- `sender` and the "sender defaults to 'Claude'" guidance are preserved.

### Collision detection

Server checks `channels/<canonical_cwd_key>/messages` on spawn-form submission:
- Empty / absent → proceed silently.
- Non-empty → push the three-way dialog to Android before launching.

### Three-way dialog

The Android dialog displays:
- Channel title (or leaf folder name fallback).
- Last activity timestamp (relative format: "2 hours ago" / "3 days ago" / absolute date for older).
- Three actions:

**Continue conversation.** Spawn proceeds. Existing `messages`, `title`, `_cwd_overrides[<cwd>]`, `hidden`, `preview`, `last_activity_at`, `unread_count` all preserved. The new agent constructs its own title on first call (may overwrite the existing channel title).

**Clear and start fresh.** Server wipes:
- `channels/<key>/messages/*`
- `channels/<key>/title`
- `channels/<key>/preview`, `last_activity_at`, `unread_count`
- `responses/<key>__*` (any stale reply slots)
- Any in-memory `_pending[(cwd, *)]` entries (cancelled cleanly with Firebase mark)

Then forces:
- `_cwd_overrides[<cwd>] = True` (cwd starts in away mode after Clear)
- `channels/<key>/hidden = False` (channel becomes visible)

Then proceeds with the spawn.

**Cancel.** Spawn aborts; no state changes.

### BYO collab joining

Second agent calling `message_and_await_agent(cwd, sender)` for an existing `_sessions[cwd]`:
- Server enrolls the new sender into the existing CollabSession (cwd-keyed).
- No spawn dialog — this is a join, not a spawn.
- Two agents in same cwd with distinct senders automatically share the session.
- The first agent's title sticks unless the joining agent updates it.

### Race condition

Two genuine simultaneous spawns to the same cwd: server's spawn handler serializes by holding a per-cwd async lock during the collision check + launch. The second spawn observes the first's freshly-spawned state and prompts the user again (which they will likely Cancel), or proceeds if the first hasn't yet written messages.

### Edge cases

- **Spawn into non-existent cwd.** Path validation (existing) plus canonicalization rules reject syntactically invalid paths. Server returns an error to the spawn surface; phone shows it inline.
- **Spawn into hidden channel.** Three-way dialog surfaces and notes the hidden state; both Continue and Clear set `hidden=false` (Clear via the forced-flip rule above; Continue via the auto-unhide-on-`ask_human` mechanic, which fires on the spawn's first interaction).

---

## Section 6 — Tests, doc updates, cutover

### Test surface

New / updated tests:

- **Path canonicalization** (`tests/test_canonicalization.py`, new) — Windows backslash, Git Bash `/c/...`, mixed-case drive, trailing slash, `.` and `..`, syntactically invalid paths (rejected).
- **Registry by `(cwd, sender)`** — supersede on add into occupied slot, resolve-by-key, remove-by-key, ported CollabSession tests.
- **Two-tier away-mode resolution** — truth table coverage for `is_away_mode_active(cwd)` × global × override; sidecar round-trip with the new shape.
- **Stop hook protocol** (`tests/test_turn_end_hook.py`, updated) — query carries cwd, server canonicalizes, response reflects per-cwd resolution; fail-open paths preserved.
- **At-desk redirect under per-cwd** — redirect when target cwd resolves to inactive; PendingRequest path when active.
- **Phone reply path** — write to `responses/<key>__<sender>`; resolution / supersede / unknown-correlation / withdrawn paths.
- **Title handling** — omit-as-no-change; truncate-on-overlong (>80 chars); channel-level title updates only on non-empty.
- **Title prepend in collab** — only-when-changed; partner-delivery-only path; `last_title_delivered_to[(cwd, partner)]` tracking.
- **Cancel / withdrawn lifecycle** — Firebase mark; in-memory cleanup; phone-side input visibility tied to pending existence.
- **Spawn collision detection** — empty channel proceeds silently; non-empty triggers dialog; Continue preserves; Clear wipes + forces away=true / hidden=false; Cancel aborts.
- **Bulk-respond on global exit** — multi-section dialog assembly; Send-to-all parallel resolution; Skip leaves pending intact; Cancel aborts global exit.
- **BYO collab via cwd discovery** — second agent calling `message_and_await_agent` with same cwd + distinct sender enrolls into existing CollabSession.

Existing tests rewrite scope: most of `test_gateway_*`, `test_messenger_contract`, `test_main_routes`, `test_collab`, `test_spawn_handler` (signatures change). Rate-limiter tests stay nearly identical (just keyed differently).

### Documentation updates

**`skill/SKILL.md`:**
- Replace every `channel_id` parameter with `cwd` in tool signatures.
- Add `title` parameter (optional) with first-call mandate, leaf-folder-name guidance for fresh sessions, task-derived guidance after work begins, ≤80-char rule, omit-as-no-change semantics.
- `enter_away_mode(cwd)` and `exit_away_mode(cwd)` now require `cwd`.
- "Reading your cwd" note: from system prompt's "Primary working directory" line, or `$PWD` via Bash.
- BYO collab section simplified: agents use cwd as the session, distinct senders disambiguate.
- Drop the channel-id-assignment guidance entirely.

**`AGENTS.md`:**
- Replace "channel" with "channel (= canonical cwd)" or analogous throughout.
- Update "Testing the app at your desk" with the per-cwd pill chip mechanics + global pill on Page A.
- Add a "Spawn collision" subsection describing the Android dialog.
- Update recovery / troubleshooting sections to reflect the layered away-mode model.

**`scripts/turn-end-hook-away-mode.py`:** comments updated; URL builder appends `?cwd=<urlencoded>`.

### Cutover sequence

1. Spec approved (this doc); plan written via `writing-plans`; implementation on a feature branch with subagent-driven slices.
2. Server tests passing locally; Android `compileDebugKotlin` clean.
3. **Firebase data wipe** (already done as of authoring).
4. Server deployed (sidecar regenerates with new shape on first write).
5. Android APK installed on device.
6. Hook script updated; settings.json registers it (existing hook gets the new query-param behavior; signature unchanged from harness POV).
7. SKILL.md copied to `~/.claude/skills/switchboard/SKILL.md`.
8. End-to-end validation on device:
   - Spawn into fresh cwd.
   - Spawn into existing cwd (Continue and Clear paths).
   - Per-channel pill toggle (enter/exit).
   - Global pill toggle (enter/exit) including bulk-respond dialog.
   - Withdrawn-question UX.
   - Title rendering on Page A and Page B.
   - Hidden channel hide/show/unhide flow.
   - Collab via shared cwd with distinct senders.
   - Title prepend in collab message-and-await flow.

### Rollback considerations

Heavy refactor + data wipe means no incremental-rollback path. If V2 has a fatal flaw discovered post-cutover, rollback is "git revert + redeploy + lose all V2-era Firebase data." Pre-cutover, spec branch and implementation branch stay separate so reverting is a clean git operation; main is not updated until end-to-end validation passes.

### Out of scope for this upgrade

- MCP-disconnect-during-restart hardening (separate concern flagged 2026-04-24).
- `UserPromptSubmit` hook for `enter_away_mode()` enforcement (separate small follow-up).
- Channel_id drift hardening via SessionStart-hook bootstrap (rejected during this brainstorm — moves the drift target without solving it).
- Auto-pruning of stale channels on Page A.
- Vector iconography for the app icon.
- `BulkRespondDialog` landscape layout (separate UI follow-up).
