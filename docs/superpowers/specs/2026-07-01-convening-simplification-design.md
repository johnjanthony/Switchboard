# Convening Simplification: Bringing Agents Together Without the Dance — Design

**Date:** 2026-07-01
**Status:** Proposed (not yet brainstormed with John; drafted by Claude from repo review)
**Scope:** Server (new convene command + tool-return restructuring), phone + Operator (multi-select convene from the Sessions roster), SKILL.md (major simplification), gateway handlers (deprecation path for the open/enter lobby mechanics).
**Depends on:** `2026-07-01-session-lifecycle-registry-design.md` (the roster is the enabler). Addresses review finding D3.

## Problem

Bringing existing agents together in a conversation currently requires the *agents* to execute a rendezvous protocol: one calls `open_conversation` (which blocks-or-not depending on whether it is already bound), the global open marker flips, peers call `enter_conversation` (blocking, intro-queued until someone speaks), and everyone must correctly interpret sentinel strings (`__TIMEOUT__`, `__CONVERSATION_EMPTY__` + parting-message lines, `"ok. open_conversation = <id>\nPeer 'X' joined."`). The singleton open marker means at most one convening can be in flight globally. SKILL.md spends most of its length teaching this protocol, and the same tools behave differently based on server state the agent cannot observe — the exact class of fragility the 2026-05-19 redesign called out in the old collab protocol (H8/H9/H10) reappearing one level up.

The root cause is an inversion of responsibility: **convening is a coordination decision the human makes, but the protocol makes agents responsible for executing it.** The agents are the least reliable parties in the system for multi-step stateful protocols, and the human — who already has the phone in hand — has no way to just *point at two sessions and say "talk."*

## Goals

- The primary convening path becomes: John selects sessions on the phone or Operator → taps **Convene** → the server does everything. Zero agent-side protocol for the common case.
- Agent-initiated convening remains possible but collapses to one idempotent tool with structured returns.
- Multiple simultaneous convenings are possible (the singleton open marker stops being the mechanism).
- SKILL.md's convening section shrinks to a few paragraphs.
- No regression to what the human sees on the phone: bubbles, attributions, intro messages, talking-stick flow carry forward.

## Non-goals

- No change to `ask_human` / `notify_human` / `send_document_human` / away-mode semantics.
- No cross-host convening (T-025 stays out of scope).
- No immediate removal of `open_conversation` / `enter_conversation` — deprecation with a compatibility window, since spawn prompts and muscle memory reference them.

## Design

### 1. Human-driven convening (primary path)

The Sessions roster (companion spec) gains multi-select. Selecting N live sessions (optionally plus one existing conversation) and tapping **Convene** writes one RTDB command:

```
commands/convene/<push-id>:
  { session_ids: [...], target: "new" | "<conversation_id>",
    title: "...", queued_at, status: "queued" }
```

A new `dispatch_convene_commands` loop (same shape and freshness-gating as the existing combine/spawn/away dispatchers, reusing `command_freshness.py`) executes it server-side with machinery that already exists in `conversation_ops`:

- `target: "new"` → mint a conversation (existing create path) titled from the command.
- For each session: if unbound → `_add_member` + bind routing; if bound to a solo conversation → migrate (existing member-migration path); if bound to a multi-member conversation → **do not silently rip it out**; mark that session `skipped: "in multi-party conversation"` in the command result. Pulling a member out of an active collab is what `combine_conversations` is for, deliberately kept a distinct and more forceful verb.
- Post a system intro message to the target ("John convened: Claude Win, Claude WSL") so the human-visible transcript explains itself.

**Waking the convened.** Hooks are reactive: a Stop hook only fires when a turn is *ending*, so a session whose turn ended before the convene has no hook boundary coming — the turn-end mechanism alone cannot wake it, and the wait queue only wakes sessions blocked *inside* `message_and_await_agent`. Wake behavior is therefore a matrix keyed off the session registry's `state` field, and the Convene sheet shows each selected session's wake path up front:

| State at convene | Mechanism | Latency |
| :--- | :--- | :--- |
| `active` (mid-turn) | Stop-hook convene notice ("You have been added to conversation `<id>` with peers X, Y...") via the extended turn-end server check | end of current turn |
| `awaiting_agent` (blocked in `message_and_await_agent` / wait queue) | server resolves the blocked future directly with `{"status":"convened", conversation_id, peers, log}` — the blocking-tool call is a server-controlled suspension point | immediate |
| `awaiting_human` (blocked in `ask_human`) | do **not** preempt the pending phone question; append the convene notice to the eventual answer payload | on answer |
| `idle` (turn ended, at prompt) | no injectable boundary exists. Passive: the UserPromptSubmit hook also checks for pending notices, so the session learns on John's next touch. Active: the existing spawn-resume flow with a convene prompt — `--resume` forks the transcript to a new session id (clean at a turn boundary); the registry marks the original `superseded_by` the fork. The sheet labels this action **"Resume into conversation"** so the fork is explicit | passive: unbounded; active: seconds |
| `ended` / `lost` | resume path only (same as idle-active) | seconds |

Mitigating invariant: when away-mode enforcement is working, sessions are rarely genuinely `idle` — the Stop hook blocks bare turn-ends and pushes agents into `ask_human` / `message_and_await_agent`, i.e., into the two immediately wakeable states. The idle row is primarily the at-desk and protocol-miss case, and its active option unifies with resume-into-conversation as one code path.

### 2. Agent-facing simplification (secondary path)

Replace the open/enter pair with one tool:

```
join_conversation(sender, ref?)
```

- `ref` absent → join-or-create the **default room**: if exactly one active multi-party-capable conversation is accepting joins, join it; otherwise mint one and return immediately (never lobby-block). The "wait for a peer" behavior stops being implicit in the join call — a joiner who wants to wait calls `message_and_await_agent` next, which is already the blocking-wait verb.
- `ref` given (a conversation_id from `lookup_conversation_ids`, from a convene notice, or from John's prompt) → idempotent join of that conversation. Already a member → `{"status":"ok","already_member":true}`.
- Multiple rooms may be joinable concurrently; the singleton open marker survives only as the *default* `ref`-absent target during the deprecation window, then retires.

**Structured returns everywhere on the conversation tools.** All conversation-tool responses become one-line JSON: `{"status":"ok|timeout|conversation_ended|conversation_empty", "conversation_id":..., "peers":[...], "log":[...], "cause":...}`. `ask_human` keeps its bare-string reply contract (its consumers treat the reply as opaque human text; unchanged), but its terminal sentinels gain the same JSON shape. SKILL.md replaces the sentinel prose with a five-line status table.

**Deprecation:** `open_conversation` / `enter_conversation` remain for two plugin versions as thin shims over `join_conversation` + `message_and_await_agent`, logging a deprecation event to the JSONL audit log so real usage (spawn prompts, habits) can be found and updated before removal.

### 3. What gets deleted when this lands

The bound/unbound branch in `open_conversation`; the open-marker-dependent sole-alive branch in `message_and_await_agent` (sole-alive uniformly returns `{"status":"conversation_empty", ...}` without the lobby-hold special case); the mint-path `open_peer_future` on `Conversation`; and roughly half of SKILL.md's convening section. The wait-queue/talking-stick core, intro-on-speak behavior, and combine remain untouched — they are the good parts.

## Migration order

1. Session registry lands (companion spec) — roster visible, no behavior change.
2. `join_conversation` + structured returns ship alongside the old tools (agents can be moved incrementally; SKILL.md documents only the new surface).
3. Convene command + dispatcher + roster multi-select ship (Operator first, then phone).
4. Deprecation window; shims log usage; open marker retired; SKILL.md final trim.

## Open questions

1. **Convene notice delivery for Gemini** — the turn-end hook already abstracts `--cli`; confirm the injected-notice pattern behaves equivalently there before advertising convene as CLI-agnostic.
2. **Should convene ever force-pull from a multi-party conversation?** Current answer: no (use combine). If real usage shows John repeatedly convene-then-combining, revisit with a `force: true` flag on the command rather than changing the default.
3. **Default-room semantics after the open marker retires** — is `ref`-absent join-or-create still wanted, or should `ref` become required once convene notices and John's prompts always carry an id? Leaning: keep join-or-create; it is the zero-configuration path for ad-hoc two-agent work at the terminal.
