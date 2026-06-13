# Fresh-Spawn Membership Gap (P0-6) — Design

**Branch:** session_id-as-key. **Status:** design approved 2026-06-12; ready for implementation plan. **Relation:** P0 addendum to [`2026-06-11-remediation-spec.md`](../../2026-06-11-remediation-spec.md), discovered during the P0 live smoke (Scenario B/C setup). Gates the same branch merge as P0-1..P0-5.

## Problem

A fresh phone spawn (`SpawnHandler.handle_fresh`) mints a Conversation, pre-generates a session id, and binds it (`registry.bind_session(new_session_id, conv_id)`), but it creates **no `ConversationMember`**. The conversation's `members_active` stays empty. Nothing later fills it: every conversation-participating tool, on finding the session already bound, operates on the conversation without ever adding the caller as a member.

The binding-without-member state then breaks every downstream feature that keys off member state:

- `cli_session_end.handle_session_end` unbinds the session, then searches `members_active` for a matching `cli_session_id`, finds none, and `return`s silently. **The member never goes dormant**, so phone Resume and combine-relaunch (P0-1, P0-2) can never trigger for the primary spawn flow.
- `message_and_await_agent` returns `ERROR: session bound to conversation but not a member`.
- `enter_conversation` (bound-current branch) falls into `_queue_for_intro`, which returns `ERROR: caller not a member of target conversation`.
- `open_conversation` (bound branch) silently skips member creation and only sets the open pointer.
- `ask_human` / `notify_human` / `send_document_human` use the conversation but never create a member, perpetuating the gap.

### Live-smoke evidence (2026-06-12)

Reproduced cleanly. A fresh Windows spawn into `C:\Work\modules` (`conv-1c1c8b2a…`) blocked in `ask_human`. On `/exit`, the access log shows `POST /cli-session/end HTTP/1.1 200 OK` arriving, yet the audit log records **no** dormancy transition. The POST mechanism works; `handle_session_end` no-ops on the `target is None` (no member) path. (An earlier `/exit` of a `libraries` spawn produced no POST at all and was inconclusive; the `modules` repro isolates the root cause: POST arrives, member is missing, dormancy silently skipped.)

This is why P0-1 (combine relaunches dormant members) and P0-2 (resume survives restart) had never run live (H18): the dormant precondition they require is unreachable for fresh-spawned agents.

## Decisions (settled with John, 2026-06-12)

1. **Lazy membership.** Membership is established on the agent's first conversation-participating tool call, not eagerly at spawn time. The spawn prompt always drives a switchboard call within seconds, so the member-less window is sub-second. A never-started spawn (startup crash, tab closed immediately) stays member-less and is disposed of via force-end/hide, exactly as today. Eager placeholder members were rejected: phone Resume for a never-started session is fake anyway (no CLI session file exists to `--resume`), so eager membership would only add placeholder-name churn and a rename step for no real resumability gain.
2. **Scope: include both adjacent hardenings.** Beyond the membership fix: make `handle_session_end`'s early returns loud, and make `open_conversation`'s bound branch create the missing member. Both are small and directly implicated in tonight's silent failure.
3. **Mechanism: server-side ensure (A) plus a redundant prompt line (B).** The real fix is a server-side shared resolver. The prompt line is redundant-by-design reinforcement, not a correctness dependency.

## Design

### 1. Shared resolver: `_resolve_conversation_and_member`

Add one helper in `server/conversation_ops.py` that both mints (when unbound) and ensures membership (when bound-but-member-less):

```python
async def _resolve_conversation_and_member(
	registry: Registry,
	cli_session_id: str,
	cwd: str,
	sender: str,
	backend=None,
	mint_if_unbound: bool = True,
) -> str | None:
	"""Return the conversation id the session belongs to, guaranteeing the
	caller is a member of it.

	- Truly unbound (conv_id is None): if mint_if_unbound, mint a fresh
	  single-agent Active conversation via _create_active_conversation_for
	  (which already creates the member); else return None so the caller can
	  apply its own unbound policy (message_and_await_agent errors rather than
	  minting into an empty room).
	- Bound to a conv_id whose Conversation object is not loaded: return the id
	  unchanged. Do not mint or relocate the session — this preserves today's
	  routing for that defensive edge, and membership cannot be ensured without a
	  conversation object. (Minting here would break the existing
	  test_notify_human_routes_to_existing_conversation contract.)
	- Bound with the Conversation present but no member entry for this
	  cli_session_id (the fresh-spawn state: handle_fresh binds without adding a
	  member): add the member.
	- Bound with a member already present: no-op.
	"""
	conv_id = registry.session_to_conversation_id.get(cli_session_id)
	if conv_id is None:
		if not mint_if_unbound:
			return None
		return await _create_active_conversation_for(
			registry, cli_session_id, cwd, sender, backend=backend,
		)
	conv = registry.conversations.get(conv_id)
	if conv is None:
		return conv_id
	if not any(m.cli_session_id == cli_session_id for m in conv.members_active.values()):
		async with conv.lock:
			# Re-check inside the lock: a concurrent first call from the same
			# session may have added the member already.
			if not any(m.cli_session_id == cli_session_id for m in conv.members_active.values()):
				await _add_member(registry, conv_id, cli_session_id, sender, cwd, backend=backend)
	return conv_id
```

Locking notes:
- The mint branch delegates to `_create_active_conversation_for`, which self-locks on `session_create_lock(cli_session_id)`. The resolver must NOT also hold `session_create_lock` (asyncio locks are not re-entrant). The ensure branch uses `conv.lock` instead, which is a different lock and is never held by the mint branch.
- No re-entrancy at call sites: every caller resolves BEFORE taking its own `conv.lock`, so the resolver acquiring `conv.lock` internally is safe.
- The mint-then-ensure race is benign: `_create_active_conversation_for_locked` adds the member (members dict) before `bind_session`, so by the time a second concurrent call observes the binding, the member already exists and the ensure re-check is a no-op.

`_add_member` is reused as-is. Its side effects in the ensure context are all idempotent or harmless: `bind_session` is idempotent (already bound), `set_session_home` is guarded by `home_newly_set`, `_disambiguate_sender` finds no collision in an empty conversation, and `open_peer_future` is `None` for a freshly minted single-agent conversation so the wake is a no-op.

### 2. Apply the resolver at the call sites

Replace the three identical mint-only blocks (`ask_human`, `notify_human`, `send_document_human` in `gateway/handlers.py`, currently each: `get(cli_session_id)` then `if None: _create_active_conversation_for(...)`) with a single call to `_resolve_conversation_and_member(...)`. This both fixes the gap and removes the triplication.

`message_and_await_agent` resolves at its top (currently returns `ERROR: not in any conversation` when unbound and `ERROR: session bound ... but not a member` when member-less). Route its resolution through the same helper so the member-less error becomes unreachable. The unbound case keeps its current "End your turn." error rather than minting (message_and_await into a brand-new empty conversation is meaningless), so for this tool the resolver is only invoked when already bound — i.e. it is called for the ensure effect, not the mint effect. The plan will express this as: if unbound, keep the existing error; if bound, ensure membership.

### 3. Prompt reinforcement (redundant-by-design)

Add one sentence to `_format_fresh_prompt`'s non-join branch noting that the agent's membership registers on its first switchboard tool call. No server behavior depends on this line; the agent's first `ask_human` already triggers the server-side ensure. It exists so an agent reading its prompt understands the model. (If review finds it adds noise, it can be dropped without affecting correctness.)

### 4. `open_conversation` and `enter_conversation`

`open_conversation`'s bound branch calls `_resolve_conversation_and_member` at the top of the branch, before it takes `conv.lock` to set the open pointer and rename. This guarantees a member exists before the rename logic runs (today the rename loop silently finds no member and only sets the open pointer).

`enter_conversation` is NOT blanket-resolved: its purpose is to migrate the caller into the **open** conversation, which is distinct from the bound one. Only its bound-current branch (the one that proceeds to `_queue_for_intro` on the current conversation) ensures membership in the current conversation first, converting the `caller not a member` error into a real membership. The migrate and join-open branches already create members via `_migrate_member` / `_add_member` and are unchanged.

### 5. `handle_session_end` loud logs

Each of the three silent early returns gains a `surface_error` log before returning:
- session not bound (`conversation_id is None`),
- conversation missing (`conv is None`),
- no matching member (`target is None`).

`handle_session_end` currently takes no logger. Add an optional `logger=None` parameter and thread the existing logger from the `/cli-session/end` route in `main.py`. The `target is None` log is the one that would have made tonight's failure visible immediately.

### 6. Testing (TDD, predicted failures)

One test per behavior change, each written failing-first and watched fail for the predicted reason before implementing:

- **Membership on first call:** construct a registry in the fresh-spawn state (conversation present, session bound, `members_active` empty), invoke `ask_human` (mock the wait), assert a member with the caller's `cli_session_id` now exists. Predicted failure: no member created.
- **End-to-end dormancy regression:** same setup, ensure membership via the first tool call, then `handle_session_end(reason="logout")`, assert the member is `alive=False`, `session_end_reason="logout"`, not permanently lost. Predicted failure (pre-fix): `target is None`, member never dormant. This is the regression that maps directly to tonight's bug.
- **Idempotent resolver:** two sequential `_resolve_conversation_and_member` calls for the same bound session add exactly one member.
- **Loud log on no-member end:** `handle_session_end` on a bound-but-member-less conversation emits the `surface_error` log. Predicted failure: silent return, no log.
- **`open_conversation` ensures member:** bound-but-member-less session calling `open_conversation` results in a member entry. Predicted failure: only the open pointer is set.

Existing suites that must stay green: `test_spawn_handler.py`, `test_cli_session_end.py`, `test_e2e_spawn_resume.py`, `test_hydration.py`, `test_e2e_open_enter.py`, `test_conversation_ops.py`, and the gateway tool tests. Run from the repo root with `python -m pytest tests/<file> -v`. Tabs for indentation. No commits (John commits). No em-dashes in generated text. `unix2dos` every new file.

## Acceptance criteria

1. A fresh-spawned agent's first `ask_human` / `notify_human` / `send_document_human` call creates a `ConversationMember` bound to its `cli_session_id`.
2. After that, an orderly `/exit` (SessionEnd, reason `logout`) marks the member dormant (`alive=False`, resumable), surfacing the dormancy system message.
3. The dormant member is then a valid target for phone Resume (P0-2) and combine-relaunch (P0-1) — verified live in the resumed smoke (Scenarios B and C).
4. `handle_session_end` logs loudly on every early return.
5. `open_conversation` and `enter_conversation` (bound-current) no longer leave a bound session member-less.
6. Full suite green, no new skips.

## Out of scope / follow-ups

- T-145 (force-end cancellation surfaces as a transport error, prompting an agent retry) is independent and stays on the backlog.
- T-003 GC (reaping members whose SessionEnd never fired, e.g. SIGKILL/tab-close) remains the mitigation for the genuinely-killed case; this design does not change it. Note the earlier `libraries` spawn whose `/exit` produced no POST at all is a separate best-effort-hook concern (the hook is documented best-effort and will not fire on a non-orderly close), not addressed here.
- Eager spawn-time membership is explicitly rejected (see Decision 1).

## Files touched (anticipated)

| File | Change |
|------|--------|
| `server/conversation_ops.py` | New `_resolve_conversation_and_member`; reuse `_add_member` |
| `server/gateway/handlers.py` | Resolver at `ask_human`/`notify_human`/`send_document_human`/`message_and_await_agent`/`open_conversation`/`enter_conversation` (bound-current) |
| `server/cli_session_end.py` | Optional `logger`; loud logs on the three early returns |
| `server/main.py` | Thread the logger into the `/cli-session/end` route's `handle_session_end` call |
| `server/spawn.py` | One reinforcing sentence in `_format_fresh_prompt` (non-join branch) |
| `tests/test_fresh_spawn_membership.py` | New: the five tests above |
