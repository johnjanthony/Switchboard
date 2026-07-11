# WP-7 - Operator Dashboard Remediation (Design)

**Date:** 2026-07-11
**Work package:** WP-7 of the 2026-07-08 technical-review remediation (findings doc `docs/2026-07-08-technical-review-findings.md`).
**Findings addressed:** REV-006 (HIGH), REV-401, REV-402, REV-403, REV-404, plus three folded REV-405 items.
**Grounded against:** `develop` @ `7b4a61b` (WP-6 landed). All `dashboard/` source read in full during brainstorm.
**Surface:** `dashboard/` only. No server change, no RTDB schema change (WP-10 owns the root-tree subscription cost and any schema restructure).

## Decisions locked (brainstorm)

- **REV-403 approach: centralize all writes in the store** (Option B), not per-call-site `.catch()`. Rationale: one error-handling implementation instead of the same logic repeated across four modules plus `statusControl.js`; it honors `store.js`'s own stated contract ("components drive everything through the actions below"); it clears REV-405's "writes scattered across four modules" observation; and store actions are exercisable under `node --test` (`store.test.js`) whereas component-level `fb` calls are not.
- **Fold three cheap, in-theme REV-405 items:** (a) pending-listener read error routes to a pane instead of the global sign-in gate; (b) SpawnDialog empty-project client validation; (c) CLAUDE.md dashboard layout list fix.
- **Everything else in REV-405 stays in WP-10:** root-level `conversations` subscription cost, per-conv meta listeners, DT-9. WP-7 touches no read-path schema.

## Finding-by-finding design

### REV-006 - members / agentStatus merge to replace (HIGH)

`mergeConversationMembers` and `mergeConversationAgentStatus` currently spread the incoming snapshot over the existing map, so a key deleted server-side (agent_status cleared on every away-mode turn end; a member removed on leave/combine migration) survives forever until a full page reload.

- `mergeConversationMembers(id, map)` -> `members: map || {}`.
- `mergeConversationAgentStatus(id, map)` -> `agentStatus: map || {}`.

Both maps are populated only by the `onValue` selection listeners attached in `selectConversation` (`paths.membersActive`, `paths.agentStatus`), which deliver the complete current child map on every fire. Replace is therefore correct and mirrors `mergeConversationPending`, which already replaces. Message merging is left as-is: message children are flag-updated, never deleted.

*Test:* onValue delivers `{a, b}` then `{a}`; assert `b` is absent from state.

### REV-402 - oldest-pending age from `askedAt`

`oldestPendingAgeSeconds` derives age from a selected-conversation-only message-timestamp resolver with a page-load-first-sighting fallback, so a pending in an unselected conversation reads as seconds old and the number shifts with selection. The server writes an authoritative `askedAt` (camelCase ISO string) into every `pending_questions` record (`server/firebase.py` `add_pending_question_record`, from `started.isoformat()` in `handlers.py`).

- `rebuildPendingsFlat` adds `askedAt: record.askedAt` to each flat record.
- `oldestPendingAgeSeconds(pendingsFlat, nowMs)` (drop the `messageTimestampResolver` parameter): for each pending, parse `askedAt`; when it is missing or unparseable, fall back to the record's `firstObservedMs`. Age is `nowMs - originMs`.
- Delete the resolver machinery, which has no other consumer: the `messageTimestampResolver` state field, `rebuildMessageTimestampResolver`, its reset in `selectConversation`, its rebuild in `mergeConversationMessages`, and the argument passed at the StatusBar call site.

`firstObservedMs` (and its `firstObservedByKey` stamping in `rebuildPendingsFlat`) is retained as the defensive fallback for a record that somehow lacks `askedAt`.

*Test updates:* the `initialState shape is exactly the contract` assertion in `store.test.js` must drop the `messageTimestampResolver` key; `derive.test.js`'s oldest-age tests move to the new two-argument signature and assert `askedAt`-driven age; any `store.test.js` resolver test is removed.

### REV-401 - `startGlobalListeners` idempotent

`app.js` calls `startGlobalListeners()` on every `onAuth` fire with a user, and the function discards the global listener unsubscribes, so each sign-out / sign-in or `setGlobalReadError` -> retry cycle attaches a full duplicate listener set.

- Keep the global unsubs in a private `globalUnsubs` array. At entry, detach any existing set (`for (const u of globalUnsubs) u(); globalUnsubs = []`) before attaching, and push each new unsub.

Detach-before-attach (rather than a one-shot guard flag) is deliberate: it keeps the account-switch case correct - a real sign-out then sign-in as a different account gets a fresh, single listener set rather than being blocked by a latched guard.

*Test:* call `startGlobalListeners()` twice; assert the first set's unsubs fired and exactly one listener remains attached per path.

### REV-404 - deep-link `hashchange`

`consumeDeepLink` runs once inside the `onAuth` callback, so a `#conv=...` deep-link into an already-open tab changes the hash and nothing fires, breaking the Watchtower handoff contract except into fresh tabs.

- In `app.js`, add `window.addEventListener('hashchange', () => consumeDeepLink(store))`.

`consumeDeepLink` is a no-op when the hash has no `conv=` match, so a hash cleared to empty does nothing. `app.js` has no `node --test` harness; this is covered by manual smoke.

### REV-403 - centralize writes and surface failures

Today every write is fire-and-forget: `retrySignIn`, `ackSession`, `conveneSelected` in the store; the `push` helper and `AnswerBox` in ConversationDetail; the `push` helper and `onAwayPill` in StatusBar; `onHideToggle` in ConversationList; and `requestStatus` in statusControl.js. A denied or failed write silently vanishes, and `AnswerBox` clears its textarea optimistically before the write confirms.

Every write moves behind a store action. Each action `await`s its underlying write and routes a rejection to a pane error. Components no longer import `fb` to write.

**Store write-action inventory**

| Action | Underlying write | Failure surface | Notes |
|---|---|---|---|
| `sendAnswer(convId, requestId, text, sender)` | `setValue` answerCmd | `detail` | returns the promise so AnswerBox clears its textarea on success only |
| `restoreLine(convId, prompt)` | `pushValue` resumeCmd | `detail` | |
| `patchLine(sourceId, targetId)` | `pushValue` combineCmd | `detail` | |
| `dropLine(convId)` | `pushValue` forceEndCmd | `detail` | |
| `spawnFresh({surface, project, prompt, targetConversationId})` | `pushValue` spawnFreshCmd | `global` | |
| `awayOn()` | `pushValue` awayOnCmd | `global` | |
| `awayOff({decision, defaultText})` | `pushValue` awayOffCmd | `global` | |
| `setHidden(convId, hidden)` | `setValue` setHiddenCmd | `global` | replaces ConversationList `onHideToggle` |
| `ackSession(sessionId)` | `setValue` ackSessionCmd | `global` | add error handling to the existing action |
| `conveneSelected({target, title})` | `pushValue` conveneCmd | `global` | clear the session selection on success only |
| `requestClaudeStatus(action)` | `fetch` POST `/widget-status` | `global` | wraps the existing `statusControl.requestStatus`; injection vs import decided in the plan |
| `retrySignIn()` | `signIn()` popup | auth gate | `.catch` -> `setAuthError` so a blocked/denied popup is visible |

`AnswerBox` becomes `await store.sendAnswer(...); setText("")` gated on success. The detail dialogs (Restore / Patch / Drop) call their store actions; the `push` helpers in ConversationDetail and StatusBar and the direct `fb` imports for writes are removed.

### Error-surfacing model

Two banner surfaces, both driven by `paneErrors`:

- `paneErrors.detail` (existing, rendered in ConversationDetail): detail-pane read errors from the selection listeners, plus the four detail-scoped write failures.
- `paneErrors.global` (new): the global-scoped write failures, plus the folded pending-listener read error (extra a). Rendered as a `PaneBanner` in `App`, directly beneath `StatusBar`. Dismissable.

`PaneBanner` gains an optional `actionLabel` prop (default `"Retry"`, preserving the existing read-banner behavior). The global write banner passes `actionLabel="Dismiss"` with its action wired to `setPaneError('global', null)`. Write failures are one-shot, so the banner dismisses rather than re-attempts; the operator retries the action itself.

## Folded REV-405 extras

- **(a) Pending-error to pane.** `syncPendingListeners` attaches each active conversation's `pending_questions` listener with an `onError` of `setGlobalReadError`, so one conversation's denied read drops the whole app back to the sign-in gate (and will misfire the moment RTDB rules become path-scoped). Route it to `setPaneError('global', ...)` instead.
- **(b) SpawnDialog validation.** SpawnDialog submits with an empty project path and relies on server-side rejection after the round-trip. Disable the "Open line" button when `project` is empty and show an inline hint.
- **(c) CLAUDE.md layout list.** The `dashboard/` layout block in `CLAUDE.md` omits `document.js`, `statusControl.js`, and `doc-view.js` (all load-bearing). Add them.

## Out of scope (WP-10)

REV-107 / REV-405 root-tree `conversations` subscription cost, per-conv meta listeners, and DT-9 path restructure. WP-7 changes no read-path schema, so it composes cleanly ahead of WP-10.

## Test plan

`node --test dashboard/*.test.js` (store, derive, commands, schema, markdown, document) stays green with:

- REV-006: replace-semantics test for members and agentStatus.
- REV-402: `pendingsFlat` carries `askedAt`; `oldestPendingAgeSeconds` two-arg signature drives age from `askedAt` with `firstObservedMs` fallback; initialState-shape and derive oldest-age tests updated.
- REV-401: double-call idempotency test.
- REV-403: per write action, asserts the correct command is written and that a rejecting `fb` sets the expected `paneError`; `sendAnswer` resolves so the caller can clear on success.
- Extra (a): pending-listener `onError` sets `paneErrors.global`, not the sign-in gate.

Manual smoke (no node harness): deep-link `#conv=` into an already-open tab selects (REV-404); a denied write shows the banner and, for an answer, keeps the textarea (REV-403); a departed member clears from the roster without reload (REV-006).
