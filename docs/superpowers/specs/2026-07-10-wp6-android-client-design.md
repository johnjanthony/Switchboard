# WP-6 — Android client remediation (REV-005, REV-201…REV-208, +REV-209 wear route)

**Date:** 2026-07-10
**Status:** design approved (brainstorm), pending spec review → plan
**Findings source:** `docs/2026-07-08-technical-review-findings.md` (WP-6 row; REV-005, REV-201…REV-208, REV-209 wear item)
**Grounding basis:** grounded against the working tree at `c76a639` (branch `develop`, clean). Line numbers cited are point-in-time and will drift as tasks land; the plan re-grounds against exact signatures per task.

## Goal

Close the nine Android correctness/robustness findings from the 2026-07-08 technical review, starting with REV-005 (the listener-leak multiplier) because it inflates the blast radius of almost every other client bug. The mainline ask/answer protocol is sound; these defects cluster in listener lifecycle, message routing on cold/partial state, and pending-state derivation.

## Guiding principle: one shared ViewModel, thin per-module wiring

`MainViewModel` lives in `android/shared/` and is consumed **verbatim** by both the phone (`app/`) and the watch (`wear/`). Every listener-layer fix (teardown, message buffering, pending authority, rejected-toast transition) is therefore a **single change in `shared/`** that both surfaces inherit. Only the manifest / FCM-intent / activity / Compose-screen changes are per-module. This is also why REV-205 ("wear drifted") is mostly *deletion*: remove wear's local re-derivation and consume what the shared VM already produces.

## Settled decisions (brainstorm)

1. **REV-203 pending authority: full, via a dedicated per-conversation listener (Option B).** The phone subscribes to the server-authoritative `pending_questions` node as the source of pending truth (as Operator already does); the message splice is kept for display ordering and the answered-checkmark affordance only. The subscription is a **dedicated per-conversation `pending_questions` listener** rather than folding the parse into the existing whole-`conversations` value listener — chosen deliberately to pay the cost now so the imminent WP-10 scaling restructure (which will narrow the whole-node listener toward per-child listeners) inherits this shape rather than having to introduce it. Accepted costs of Option B: redundant sync while the whole-node listener still exists, more listeners to tear down (REV-005 surface), and a separate-callback race the reconciler must tolerate.
2. **Test posture: pure extraction + manual smoke.** Logic with edge cases is lifted into pure Kotlin units in `shared/` (no Android imports) and unit-tested under `:shared:testDebugUnitTest`, matching the existing `MessageOrdering` / `ConversationPolicy` convention. The VM is thin wiring over those units. Compose/lifecycle wiring is verified by emulator smoke, which is the only place REV-005's real effect is observable. No Robolectric (it would need a VM dependency-injection refactor + Firebase mocking and still could not verify the REV-005 lifecycle behavior without a device — the mock-vs-reality trap).
3. **REV-206 auth-failure UX: retry in the existing empty state.** On sign-in failure the phone's "No conversations yet." surface swaps to an error message with a "Sign in" retry button; wear's empty state gets the same failed+retry variant. No dedicated sign-in route.
4. **REV-209 (wear reply route): pulled into WP-6.** The wear reply-route interpolation bug is a real correctness defect on a screen REV-205 already rewrites, so it lands with the wear alignment task. The remaining REV-209 items (`keepSynced` accumulation, hide-guard asymmetry, `requestIdToConvId` dead code) stay deferred to the dead-code/DRY pass.
5. **Discovered sibling included:** wear's `MessageViewScreen` selects a conversation on open but never clears it on leave (the phone does, via `DisposableEffect`). Same class of bug as REV-208 (background messages to a "selected" conversation get their unread zeroed). Fixed alongside REV-208/205.

## The pure units (testable core, all new, in `shared/`)

Each is a plain Kotlin class/function with no Android dependency, unit-tested in isolation.

| Unit | Responsibility | Serves |
|------|----------------|--------|
| `Subscriptions` | Collects `() -> Unit` unsubscribe lambdas; `add(unsub)`, `dispose()` invokes all and clears; idempotent (double-dispose safe). | REV-005 |
| `PendingMessageBuffer` | Stash `(msgId, ChannelMessage)` for a convId whose row is not yet present; `buffer()`, `drain(convId)`, per-conv cap (~200) to bound a permanently-unparseable node. | REV-201 |
| `PendingReconciler` | Given the authoritative `pending_questions` map + the spliced message list, produce the row's pending set (from the node) and the answered-for-checkmark set (from the splice); tolerant of pending arriving before the row is built. | REV-203, REV-205 |
| `RejectedToastTracker` | `shouldToast(msg, attachedAtIso, alreadyToasted): Boolean` — true only for a rejected message whose `timestamp` is after listener-attach and whose msgId has not been toasted. | REV-202 |
| `AuthUiState` (+ mapper) | `(userPresent, attemptInProgress, attemptFailed) → {InProgress, SignedIn, Failed}`; drives which empty-state variant renders. | REV-206 |
| `pickerTargets(all, excludeId)` (in `ConversationPolicy`) | Filters to `state == "active"`, drops the excluded id. | REV-204 |

## Per-finding design

### REV-005 — listener leak on notification tap (the multiplier)

Two independent legs, both required:

- **Teardown.** Route every listener attach through a `Subscriptions` instance. Today only `conversationMessageListeners` (map) and `adminListener` are held; the other anonymous `ValueEventListener`/`ChildEventListener` registrations and the `IdTokenListener` have no stored handle. Each attach becomes `subscriptions.add { ref.removeEventListener(listener) }` (and `{ auth.removeIdTokenListener(idListener) }` for auth). Override `onCleared()` to `subscriptions.dispose()` plus detach the per-conversation message listeners. (Task 3 adds per-conversation `pending_questions` listeners to the same map/teardown discipline.) This covers VM recreation from process death, config change, and back-out-then-relaunch.
- **Reuse.** Set `android:launchMode="singleTop"` on `MainActivity` in **both** manifests, and add `FLAG_ACTIVITY_SINGLE_TOP` to the FCM tap intent in **both** FCM services (keep `FLAG_ACTIVITY_CLEAR_TOP`). A tap on a live activity is then delivered to `onNewIntent` (already implemented in both activities, currently dead code) instead of recreating the activity — so the common path stops churning the VM entirely.

Both legs land in Task 1 so subsequent tasks' smoke tests are not fighting N+1 duplicate listeners.

### REV-201 — messages dropped when the row is absent

`addMessageToConversation` early-returns when `_conversationRows.value[convId] == null`, so messages arriving for a conversation whose summary has not yet loaded (or that hit the parse-failure branch) are discarded, and Firebase's `onChildAdded` never replays them for an already-attached listener. Fix by **buffering**, not detach/reattach: an unknown-convId message goes into `PendingMessageBuffer`; `mergeSummariesIntoRows` drains a conversation's buffer through `addMessageToConversation` when its row first appears. Buffering avoids listener churn; the per-conv cap bounds memory if a node never resolves.

### REV-203 — pending from splice heuristics instead of the authoritative node

The phone derives pending state from message-splice heuristics, so a failed/absent human-echo write (server-side, swallowed) leaves a question pending on the phone forever and a re-reply yields the alarming "withdrawn" notice.

Fix: read the server-authoritative node via a **dedicated per-conversation `pending_questions` listener** (Option B — see Settled decisions #1). The listener is attached/detached in the *same* conversation-appear/disappear callback that already manages the per-conversation message listeners (`startConversationMessageSubscriptions`), held in a map alongside them, and torn down in `onCleared` (Task 1's teardown discipline extends to cover it). Node schema (server `add_pending_question_record`): `{sender, questionText, cancelled:false, msgId, suggestions, cliSessionId, askedAt}`, keyed by `request_id`, and **deleted on resolve/cancel** — so *presence of the child ⟺ genuinely pending*. The phone's existing `Pending` data class already mirrors these camelCase fields (only `msgId` needs to become nullable, since the node allows null).

`addMessageToConversation` stops deriving pending from the splice; the splice now only produces display order and the answered-checkmark set. The reply bar and the pending dot read the authoritative pending. `PendingReconciler` holds the pure mapping.

Because the pending listener fires on a *separate* callback from the summary/message flow, the reconciler must tolerate pending arriving for a conversation whose row is not yet built — a REV-201-shaped edge. Resolve it the same way: hold the latest authoritative pending map per convId and apply it once the row is present (reuse or mirror Task 2's buffering discipline). This separate-callback race and the extra per-conversation listeners are the accepted cost of Option B, paid now so WP-10 inherits the per-child listener shape.

### REV-202 — rejected-message toast replays entire history on every attach

Today every arriving `msg.rejected` toasts unconditionally, so every cold start (and, pre-REV-005-fix, every leaked VM) re-toasts every stale rejected notice. Fix via `RejectedToastTracker`: toast only when a rejected message's `timestamp` is after the listener's attach time **and** its msgId has not already been toasted. Null/blank timestamp is treated as history (no toast). This is robust against the initial replay burst and against `onChildChanged` re-delivery, with no extra Firebase read.

### REV-204 — ended conversations offered as combine/spawn/convene targets

`_activeConversations` carries `state == "ended"` (the name lies) and the pickers do not filter. Filtering at the source is unsafe because `_activeConversations` is also used for title lookups on ended conversations (SessionDetailSheet, ResumeSessionSheet). Fix at the three picker call sites (Combine / Spawn / Convene) with `pickerTargets(...)` (`state == "active"`), leaving the lookups intact.

### REV-205 + REV-209 (wear reply route) — align wear to the shared VM, encode the route

- **Align:** wear `MessageViewScreen` deletes its local `sortedBy { timestamp }` (which undoes the splice and sorts null-timestamps first) and its local `answeredMsgIds` recomputation, consuming `row.messages` (already spliced by the VM), `row.answeredQuestionMsgIds`, and the now-authoritative `row.pendingQuestions`.
- **Encode:** the reply route stops raw-interpolating `reply/${convId}/${msg.request_id}/${msg.sender}`. Guard the answerable card on non-null `request_id` (no `answers/null` write) and `Uri.encode` the `sender` segment (a sender containing `/ ? #` no longer breaks route matching).

### REV-206 — silent sign-in failure; data layer waits forever

`GoogleAuthHelper.signInWithGoogle` swallows failures to `false` and both callers ignore the result in a one-shot `LaunchedEffect(Unit)`, so a declined/failed credential flow leaves the phone on "No conversations yet." and the watch on "Connecting…" with no error and no retry. Fix: the sign-in composable captures the result into hoisted `AuthUiState`; on `Failed`, the phone empty state renders an error message + "Sign in" button and the wear empty state renders its failed+retry variant. Retry re-invokes `signInWithGoogle`. On success the existing `IdTokenListener` attaches the DB listeners and the list populates as today.

### REV-207 — Markwon rebuilt and full-reparsed per bubble per recomposition (main thread)

Both app and wear `MarkdownText` build a `Markwon` instance inside `AndroidView.update` and call `setMarkdown` on every recomposition — continuously during pinch-zoom, which changes `fontScale` every frame. Fix: `remember` the `Markwon` instance (keyed on context), and in `update` skip `setMarkdown` unless `(content, fontScale, color)` changed since the last render. This is the primary scroll/gesture jank source.

### REV-208 — stale selected-conversation fallback zeroes unread for an unopened conversation

When the selected conversation vanishes, `mergeSummariesIntoRows` currently re-points selection at an arbitrary active row; because selection is treated as "open on screen," each arriving message for that row then writes `unread_count = 0`. Fix: fall back to `null`; the Page B route's `DisposableEffect` owns real selection. **Wear sibling (approved):** add an `onDispose { clearSelectedChannel() }` to wear's `MessageViewScreen` so it clears selection on leave, mirroring the phone.

## Task sequence (SDD plan shape, ~9 tasks + final review)

1. **REV-005** — `Subscriptions` unit + `onCleared()` teardown in the shared VM; `launchMode="singleTop"` + `FLAG_ACTIVITY_SINGLE_TOP` in both modules. *(multiplier first)*
2. **REV-201** — `PendingMessageBuffer` unit + flush-on-row-appearance in `mergeSummariesIntoRows`.
3. **REV-203** — attach a dedicated per-conversation `pending_questions` listener (managed and torn down like the message listeners); `PendingReconciler` unit tolerant of pending-before-row; strip splice-derived pending from `addMessageToConversation`; `Pending.msgId` nullable.
4. **REV-202** — `RejectedToastTracker` unit; wire into `addMessageToConversation` with attach-time capture.
5. **REV-208** — null selection fallback in the shared VM; wear `MessageViewScreen` clears selection on dispose.
6. **REV-204** — `pickerTargets` unit; apply at Combine / Spawn / Convene call sites.
7. **REV-205 + REV-209** — wear consumes shared spliced order / answered set / authoritative pending; delete local re-derivation; encode + guard the reply route.
8. **REV-206** — `AuthUiState` unit; phone empty-state error+retry; wear empty-state failed variant.
9. **REV-207** — `remember` Markwon + gate `setMarkdown`, both app and wear.
10. **Final review** — full gates + emulator smoke checklist.

Ordering rationale: REV-005 first (multiplier); REV-201 before REV-203 so message flow is sound and Task 3's reconciler can reuse the buffering discipline; REV-203 before REV-202/205 because they consume authoritative pending; wear alignment (7) after the shared pending is authoritative; REV-206/207 are independent and land late.

## Verification

- **Unit gates:** `:shared:testDebugUnitTest` for every pure unit (`Subscriptions`, `PendingMessageBuffer`, `PendingReconciler`, `RejectedToastTracker`, `AuthUiState`, `pickerTargets`).
- **Compile gates:** `:app:compileDebugKotlin` and `:wear:compileDebugKotlin`. **Never** run a bare `gradlew build` — the module has a pre-existing `:wear:lintDebug` failure unrelated to this work.
- **Server/dashboard suites:** confirm `pytest` and `node --test dashboard/*.test.js` stay green (no server/dashboard changes are expected; this is a guard against accidental drift).
- **Emulator smoke (John-assisted), per finding:**
  - REV-005: tap a notification N times in one process → no duplicate toasts / no repeated `unread_count=0` writes; listener count stable; a tap on a live activity routes through `onNewIntent` (no recreate).
  - REV-201: a message to a not-yet-loaded conversation appears once its row arrives.
  - REV-202: no rejected-toast replay on cold start.
  - REV-203: replying clears the pending indicator and produces no false "withdrawn" notice; pending survives even if the human-echo write is absent.
  - REV-204: ended conversations are absent from all three pickers.
  - REV-205 + REV-209: wear message order matches the phone; replying works with a special-character sender; a question with no `request_id` shows no answerable card.
  - REV-206: declining sign-in shows the error + retry; retry recovers.
  - REV-207: pinch-zoom is smooth (no per-frame reparse).
  - REV-208: a background message to a previously-open, now-unselected conversation does not zero its unread.
- **Emulator hygiene:** re-run `adb -s <serial> shell getprop ro.build.characteristics` immediately before every targeted `adb install`/`shell` (the phone and wear apps share `applicationId` `io.github.johnjanthony.switchboard`, so a wrong-target install silently clobbers the other app); confirm `dumpsys package … | grep lastUpdateTime` advanced on the intended device to prove a real reinstall versus a relaunch.

## Conventions

- **Agents never commit.** Leave changes staged; John commits at the end.
- **Subagent model routing (Fable-week Profile B):** implementers `model:"sonnet"` explicit on every dispatch; reviewers / final `model:"opus"`; `CLAUDE_CODE_SUBAGENT_MODEL` stays UNSET; `/usage` spot-check after Task 1.
- **No `plugin.json` bump.** The Android app is not plugin-facing (the plugin is the MCP server + skill + hooks); Android changes do not reach sessions through the plugin cache.
- **Line endings:** `android/` is a uniformly-LF module, so new Kotlin files stay LF (no `unix2dos`). This spec and any new `docs/` markdown get CRLF (`unix2dos`).
- **No version bumps** in any build file.

## Out of scope

- The server-side swallowed human-echo write (`dispatch.py`) that *causes* the REV-203 divergence — WP-6 makes the phone independent of it rather than fixing the swallow; the server behavior is a separate finding.
- REV-209 items other than the wear reply route (`keepSynced` accumulation, `hideConversation`/`unhideConversation` admin-guard asymmetry, `requestIdToConvId` dead code) — deferred to the dead-code/DRY pass.
- The whole-`conversations` value listener still downloads the full subtree (the redundant-sync cost Option B accepts); narrowing it is WP-10's scaling restructure (REV-107/DT-9), not this WP.
