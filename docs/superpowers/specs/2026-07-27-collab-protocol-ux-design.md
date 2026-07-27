# Collab Protocol UX Design — Discovery, Peer Awareness, Non-Blocking Messaging

**Date:** 2026-07-27
**Authors:** Claude Win (Fable), Antigravity — drafted collaboratively in a Switchboard conversation, per John's brief
**Status:** Fleshed out 2026-07-27 with John — open questions resolved, D6 corrected. Awaiting final review; no implementation authorized yet.

## Motivation

During the 2026-07-27 session (at-desk FCM gate review), both agents stumbled on the collab protocol itself. Every problem below was hit live, in one session, by two different agents:

1. **"Join the open conversation" is a gamble.** The MCP tool docstring (`server/main.py:561`) tells agents a ref-less `join_conversation` targets "the currently-open conversation... (promoted as open)". That model is retired — the only remaining `open_conversation` code is the legacy-node deletion migration (`server/firebase.py:562`, `server/main.py:749`). Actual behavior is the candidate rule (`server/conversation_ops.py:71`): land in the single unambiguous Active conversation with `origin == "join"`, exactly one alive member, created inside `JOIN_CANDIDATE_WINDOW_SECONDS`; zero or several candidates silently mint a new room. Claude's ref-less join found the right conversation by luck. One extra solo conversation and it would have minted an empty room with no warning.
2. **Lookup cannot disambiguate.** `lookup_conversation_ids(sender_contains="Antigravity")` returned ten bare conversation ids — no titles, no member states, no last-activity. No recipe can be built on bare ids. Separately, Antigravity searched `sender_contains="Fable"` and got zero results because the peer's sender string is "Claude Win" — the name John uses for an agent and the agent's sender string can differ.
3. **Peer-state blindness.** An agent cannot tell whether its peer is blocked waiting on it, busy working, or gone. "Peer left" is detectable only by string-matching `[X left]` in the log. This session: Antigravity received a review, correctly went off to implement it, but had no way to say "ack, working on it" without blocking its own session — so Claude's `message_and_await_agent` wait sat parked >120s and had to be cancelled (TaskStop) and re-sent.
4. **Harness asymmetry on blocking calls.** Claude Code auto-backgrounds any MCP call still running after ~120s: the agent keeps working, gets a completion notification, and can cancel a parked call. Antigravity's `call_mcp_tool` is fully synchronous: while it blocks, the entire session is frozen and John cannot even type into it. The protocol currently assumes blocking waits are cheap; for agy they are the most expensive thing it can do.

## Decisions

| # | Decision | Kind |
|---|----------|------|
| D1 | Fix the stale `join_conversation` docstring; teach the `minted:true` tell and a find-and-join recipe | docs |
| D2 | `lookup_conversation_ids` returns metadata rows, not bare ids | server |
| D3 | Conversation-tool envelopes gain `peers: [{sender, state, waiting, last_spoke_at}]` | server |
| D4 | New tool `post_agent_message` — speak without blocking. No listen+block primitive is added | server |
| D5 | Optional `timeout_seconds` on `message_and_await_agent` | server |
| D6 | Define supersession semantics for `message_and_await_agent`; preserve the existing cancellation cleanup | server |
| D7 | Per-harness waiting guidance in SKILL.md (Claude Code backgrounding, agy short-slice pattern) | docs |

## Design

### D1 — Join semantics: one truth, documented twice

The candidate rule in `conversation_ops.py` is the truth. Rewrite the `join_conversation` docstring in `main.py` to describe it (delete every mention of "currently-open conversation" / "promoted as open") and make SKILL.md's description match it in substance.

New SKILL.md recipe, **Find and join an existing conversation**:

1. If John's prompt or a convene notice contains a conversation id, `join_conversation(ref=<id>)`. Never go ref-less when told to join a specific conversation.
2. Otherwise `lookup_conversation_ids` — `title_contains` first (titles are agent-set and descriptive), then `sender_contains`. The name John calls an agent is not necessarily its sender string (e.g. "Fable" vs sender "Claude Win"), so on zero hits retry with likely variants before concluding it does not exist.
3. Exactly one plausible match: join it by ref. Several: pick by `last_activity_at` and member states (D2 makes this possible), or ask John. Zero: ask John.
4. If you do go ref-less expecting to land in an existing conversation and the envelope returns `minted: true`, you did NOT find it — you created a new empty room. Leave it (`leave_conversation`) and go back to step 2.

### D2 — Lookup returns metadata

`lookup_conversation_ids` keeps its name and filters but returns rows instead of bare ids:

```json
{"status": "ok", "conversations": [
  {"conversation_id": "conv-...", "title": "...", "last_activity_at": 1753600000.0,
   "created_at": 1753590000.0, "origin": "join",
   "members": [{"sender": "Antigravity", "state": "alive"}, {"sender": "Claude Win", "state": "dormant"}]}
]}
```

Member entries here carry `sender` + `state` only — enough to disambiguate; the richer per-member fields (`waiting`, `last_spoke_at`) are D3 envelope content. Rows sorted by `last_activity_at` descending. Scope stays Active-only (current behavior); an `include_ended` parameter was considered and rejected (decision 2026-07-27) — Ended conversations are retention-pruned within 72h and cannot be joined or messaged, so nothing needs to find them. Member counts are derivable from `members`; no separate count field (redundant data invites drift). The old `conversation_ids` key is dropped — agents are re-taught by SKILL.md in the same change. Consumers verified 2026-07-27: the smoke harness never calls this tool and Android/Operator read RTDB directly, never MCP envelopes; the only consumers are agents and this repo's tests.

### D3 — Peer state in envelopes

Every `join_conversation`, `message_and_await_agent`, and `post_agent_message` (D4) success envelope gains:

```json
"peers": [{"sender": "Antigravity", "state": "alive", "waiting": true, "last_spoke_at": 1753600000.0}]
```

- `state`: `alive` | `dormant` (dormant members ARE included — "was here, session exited" is decision-relevant context for wait-or-leave).
- `last_spoke_at` (nullable float): when this member last wrote a message. "Silent for 2 hours" vs "posted 3 minutes ago" feeds the same wait-or-leave decision.
- `waiting: true` means the server currently holds a parked wait for that member — a message sent now will wake them. It answers "do I hold the baton?" authoritatively (the server owns the wake FIFO; today agents can only guess).
- Precise meaning caveat, documented in SKILL.md: `waiting=true` does not mean the peer is idle. A Claude Code agent whose wait was backgrounded is still `waiting` server-side while doing other work locally. It means "deliverable now", not "twiddling thumbs".
- Sources: `waiting` derives from membership in the conversation's `wait_queue` (`server/gateway/handlers.py:619`) — the server's own truth, no new state. `last_spoke_at` is tracked on the member record when it speaks or posts, and rebuilt from the conversation's message history on hydration; null when unknown.

`join_conversation` today returns `peers` as a bare name list; this upgrades it to objects. Same re-teach-via-SKILL.md compatibility argument as D2.

### D4 — `post_agent_message`: speak without blocking

The tool set today, framed as {speak, listen} x {block, non-block}: speak+block is `message_and_await_agent`, listen+non-block is `join_conversation`. This adds speak+non-block:

```
post_agent_message(sender, message, title?) ->
  {"status": "ok", "conversation_id": ..., "msg_id": ..., "log": "<unseen delta>", "peers": [...]}
```

- Writes the message (type `agent_msg`), wakes blocked peers, returns immediately with the unseen log delta and `peers` (D3). It is `message_and_await_agent` minus the wait, including its entry resolution (the resolver that heals fresh-spawn membership; amendment 2026-07-27 — raw lookups would reject a phone-spawned agent whose first collab action is a post).
- Firebase write failure is non-fatal but honest (amendment 2026-07-27): the in-memory append and peer wake have already succeeded, so instead of an ERROR (which would invite a double-post) the ok envelope carries `write_failed: true` and no `msg_id`. Agents must not re-post on it.
- Separate tool, not an `await_reply=false` param: a param contradicting the tool's own name ("and_await") invites misuse, and the collab rules gain a crisp vocabulary — "reply with `message_and_await_agent`, or acknowledge with `post_agent_message`".
- Consumes the same per-conversation rate-limit bucket with the same degrade-to-FCM-suppression behavior (REV-109). Its `agent_msg` type means the just-shipped at-desk FCM gate already keeps posts silent when John is at his desk.
- Wakes the FIFO-oldest single waiter via the existing `_wake_one_from`, exactly as `message_and_await_agent` does — no new fan-out semantics — but passes its own session id as an exclusion so it can never consume its own armed wait (amendment discovered during implementation, 2026-07-27: `_wake_one_from` gains an optional `exclude_cli_session_id`; excluded live entries keep their FIFO position, and the three existing callers are unchanged. Without this, a poster whose backgrounded wait was the oldest live entry would wake itself, contradicting the never-disturbs rule below).
- `unread_count` behavior is unchanged (decision 2026-07-27): the badge stays a pure unseen-history counter and every `agent_msg`, posts included, bumps it. The at-desk FCM gate already keeps posts from buzzing.
- Does not supersede or disturb a parked wait from the same session (see D6).
- **No listen+block primitive.** The empty-message ban exists to prevent wait-wait deadlock; the listen quadrant stays covered by non-blocking `join_conversation` polling.

New SKILL.md pattern, **Long work mid-collab** (the exact fix for this session's stumble): on receiving a wake that requires substantial work — `post_agent_message` an ack with an ETA, do the work, then `message_and_await_agent` with the result. Peers see the ack, John sees progress, nobody sits parked.

### D5 — Caller-chosen wait timeout

`message_and_await_agent(..., timeout_seconds?)` — optional. The caller value is clamped to [10, `SWITCHBOARD_TIMEOUT_SECONDS`] seconds: floor 10 because anything shorter is polling by another name and hammers the wake queue; ceiling is the config value (default 86400, i.e. 24h). Omitting the param keeps today's default window, and the shared 24h default itself is untouched — it also governs `ask_human` (decision 2026-07-27). Rationale: for agy a blocking wait freezes the whole session (Motivation #4), so it needs short bounded slices; a chosen short timeout makes `{"status":"timeout"}` an EXPECTED loop-control outcome, not an anomaly. SKILL.md gets a corresponding note: a timeout you explicitly requested is yours to handle (typically: do other work, poll via `join_conversation`, or re-enter a wait); the "pause and record" timeout protocol applies to default-window timeouts you did not choose.

### D6 — Supersession semantics (cancellation already exists)

Correction from the first draft: client cancellation cleanup already exists and is tested — the `CancelledError` path removes the wait-queue entry and marks the session (`server/gateway/handlers.py:631`, `test_cancelled_wait_resets_session_state`). Only supersession is genuinely undefined today, and background usage makes it easy to hit.

- **Supersession:** a second `message_and_await_agent` from a session that already has a parked wait replaces it; the parked call returns `{"status": "superseded"}` (mirrors `ask_human`). Rejecting instead would force a manual cancel first, which agy cannot even perform.
- **Mechanics:** a `SUPERSEDED` sentinel following the existing `TIMEOUT_SENTINEL` pattern (`server/gateway/handlers.py:25`). On a new wait, under `conv.lock`, any existing `wait_queue` entry for the same `cli_session_id` is removed and its future resolved with the sentinel; `_wrap_wait_result` maps it to the envelope.
- **`post_agent_message` never supersedes** a parked wait from the same session — posting an update while your wait stays armed is legitimate (and for Claude Code, natural: the wait is backgrounded).
- **Regression guard:** supersession must not disturb the existing cancellation path; the implementation adds supersession tests alongside the existing cancel test.

### D7 — Per-harness waiting guidance (SKILL.md)

New SKILL.md subsection, factual and per-harness:

- **Claude Code:** MCP calls still running after ~120s are moved to a background task automatically; you keep working and receive a completion notification. Backgrounded waits do not survive session exit. To retract a parked wait, TaskStop it (with D6, a new wait also supersedes cleanly). This is harness behavior, not a switchboard feature — do not assume peers have it.
- **Antigravity (agy):** `call_mcp_tool` blocks the whole session; John cannot interact while it waits. Background shell tasks are fine — status and partial output are readable mid-turn via `manage_task(status)` — but a blocking `call_mcp_tool` is atomic and non-interruptible: nothing, including background-task completion notifications, can be acted on until it returns. Do not sit in long default-window waits. Pattern: `post_agent_message` acks and updates; work; poll `join_conversation` (returns unseen delta, never blocks) between work chunks; when genuinely idle, use `message_and_await_agent` with a short `timeout_seconds` (D5) — each timeout returns control, at which point check background tasks, poll, and re-enter the wait. Ending the turn instead is honest but requires John (or a hook) to resume you — say so in your last post if you do it.
- **Explicitly rejected:** shell-scripting the MCP HTTP endpoint (curl) from background commands. The transport is stateful streamable HTTP (session establishment, `mcp-session-id`, SSE frames); a hand-rolled client is exactly the fragility this spec exists to remove.

### Baton-rule clarifications (SKILL.md, part of D7)

- `leave_conversation` counts as answering the baton: the parting wakes blocked peers. Verbal "I'm leaving" without the tool call is still not a leave.
- A wake whose delta is only a status ack (e.g. a `post_agent_message` "working on it") does not pass you the baton; stay waiting or go do something useful.
- Peer departure is now read from `peers` (D3), not by string-matching `[X left]` in the log; the log line remains for human readability.
- **Last agent standing:** if a wake (or a `peers` check) shows no other alive member, read the parting message in the delta. Consensus reached: report the outcome to John — `ask_human` in away mode, terminal at-desk — as the conversation's single reporter. No consensus: tell John the conversation ended unresolved and what remains open. Do not keep waiting in an empty room; dormant members do not reply.

### Delivery notes

- `post_agent_message` needs no hook changes: the injector self-filters on the `mcp__switchboard__` prefix (`scripts/cli-session-injector-hook.py:38`), so the new tool receives `cli_session_id`/`cwd` injection automatically.
- SKILL.md ships inside the Claude Code plugin: skill edits require a `.claude-plugin/plugin.json` version bump, or version-gated caches keep serving the stale skill.
- Delivering the updated guidance to agy follows the existing chezmoi wiring (README, "Antigravity CLI (agy)"); out of scope here beyond this pointer.
- One spec, one implementation plan (decision 2026-07-27). The plan may sequence the pure-docs fixes (D1, the existing-behavior parts of D7) as early tasks, but everything ships as one reviewed unit.

## Out of scope

- Any Android / Operator surface changes. `waiting` and lookup metadata are agent-facing; the phone already renders member states its own way.
- Roster hygiene for accumulated Active conversations (ten Active conversations matched one sender today). Real, but a retention/lifecycle question — flagged as a follow-up, not designed here.
- listen+block primitive (rejected, D4). curl-based MCP access for agy (rejected, D7).

## Resolved questions (2026-07-27 review with John)

1. **Envelope consumers:** verified — the smoke harness calls none of these tools, and Android/Operator read RTDB directly, never MCP envelopes. Only agents and this repo's tests consume the D2/D3 payloads; shapes may change freely.
2. **`include_ended` on lookup:** rejected, YAGNI. Lookup stays Active-only (see D2).
3. **`timeout_seconds` bounds:** clamp to [10s, `SWITCHBOARD_TIMEOUT_SECONDS`]; the shared 24h default is untouched (see D5).
4. **Unread badge:** unchanged — a pure unseen-history counter; all agent messages, posts included, keep bumping it (see D4).

## Sign-off

- Claude Win (Fable): agreed, 2026-07-27.
- Antigravity: agreed, 2026-07-27 (recorded in the conversation log).
- Revision 2026-07-27, fleshed out with John: open questions resolved as recorded above; D6's cancellation claim corrected against the code (cleanup already implemented and tested).
