# Convening Chunks 2-5: Roadmap and Decisions

**Date:** 2026-07-06
**Status:** Approved by John (brainstormed 2026-07-06; resolves the parent's "Proposed, not yet brainstormed" status)
**Parent:** `2026-07-01-convening-simplification-design.md` (the full design; this doc records the chunking, the decisions, and the deltas). Companions: `2026-07-06-session-registry-chunk-design.md` (chunk 1, in implementation), `2026-07-06-t001-retriage-decision.md` (T-001 sequenced after these chunks).
**Plans:** deferred by decision - each chunk's implementation plan is written only after the preceding chunk lands, because plans are grounded in exact signatures and the tree changes under them. Specs don't have that problem; this one is stable against chunk 1's *interfaces* (SessionRegistry, `sessions/` tree, registry states, `GET /sessions`).

## Ordering decision (server-first)

- **Chunk 2 - agent path:** `join_conversation` + structured JSON returns + the single SKILL.md protocol revision. Server-only.
- **Chunk 3 - human path:** convene command + dispatcher + wake delivery + Operator multi-select. Live sessions only.
- **Chunk 4 - Android batch:** Sessions board, spawn resume-picker rework, phone convene multi-select, resume-into-conversation. One Gradle/deploy cycle.
- **Chunk 5 - deprecation:** open/enter become logging shims, then the open marker and lobby machinery are deleted; SKILL.md final trim.

Rationale: convening via Operator needs no Android work (multi-select rides on chunk 1's Sessions rail), and during development convening is as much an at-desk operation as a phone one - so the phone board is off the critical path. Server momentum continues directly from chunk 1, convening becomes usable earliest, and all slow-iteration Android work batches into one chunk. Rejected orderings: phone-first (roster on the phone soonest, but convening lands last and Android is touched twice) and convene-first (human path before agent path splits the SKILL.md protocol churn across two windows).

## Chunk 2 - `join_conversation` + structured returns

**The tool:** `join_conversation(sender, ref?)`. Never blocks.

- `ref` absent: if the open marker points at an active conversation, join it; otherwise mint a new conversation, **promote it to the open marker**, and return immediately. The promotion is what preserves today's pairing semantics through the deprecation window: the second ref-absent joiner lands with the first instead of minting a second room. Waiting for a peer stops being implicit in the join: a joiner who wants to wait calls `message_and_await_agent` next (already the blocking verb).
- `ref` given (a conversation_id from `lookup_conversation_ids`, a convene notice, or John's prompt): idempotent join. Already a member → `{"status":"ok","already_member":true}`. Bound to a different conversation → migrate (same explicit-move semantics as enter's Branch 3 today).
- **History is synchronous:** the join response carries the conversation log that `_queue_for_intro` delivers today by blocking. No intro-queue wait exists on the join path.

**Structured returns:** `join_conversation`, `message_and_await_agent`, `leave_conversation`, `combine_conversations`, and `lookup_conversation_ids` return one-line JSON with a `status` field: `ok | timeout | conversation_ended | conversation_empty`, plus `conversation_id`, `peers`, `log`, `cause` as applicable. `ask_human` keeps its bare-string reply for human answers; only its terminal sentinels adopt the JSON shape (`{"status":"timeout"}`, `{"status":"conversation_ended","cause":"force-ended"}`), so sentinel string-matching leaves agent-land in one revision.

**SKILL.md:** rewritten once, in this chunk, documenting only the new surface. open/enter keep working, undocumented, until chunk 5. One plugin version bump covers the SKILL revision.

**Not touched in chunk 2:** open/enter handlers, the open marker, `open_peer_future`, the lobby-hold branches (all die in chunk 5). The wait-queue/talking-stick core is untouched throughout.

## Chunk 3 - convene command, wake delivery, Operator multi-select

**Command tree:** `convene_commands/<push-id>` - top-level, matching `spawn_commands`/`combine_commands`/`away_mode_commands`. (Delta from the parent spec's `commands/convene/` nesting, for consistency with the existing trees.) Shape: `{session_ids: [...], target: "new" | "<conversation_id>", title, issued_at, status}`. New `dispatch_convene_commands` loop, freshness-gated via `command_freshness.py`, supervised like its siblings.

**Per-session routing** (existing `conversation_ops` machinery): unbound → `_add_member` + bind; bound to a solo conversation → `_migrate_member`; bound to a multi-party conversation → **skipped**, reason recorded on the command result. Pulling a member out of an active collab remains `combine_conversations`' job. A system intro message ("John convened: Claude Win, Claude WSL") lands in the target conversation.

**Wake matrix implementation** (decided: extend existing hooks; no new hook registrations):

| Session state | Mechanism | Latency |
| :--- | :--- | :--- |
| `awaiting_agent` | server resolves the blocked `message_and_await_agent` future with `{"status":"convened", conversation_id, peers, log}` | immediate |
| `awaiting_human` | never preempted; convene notice attached to the pending record and prepended to the eventual answer payload | on answer |
| `active` | per-session **notice queue** on the SessionRegistry (RTDB-mirrored; survives restart). Turn-end hook's GET gains `session_id`; the response gains `notices`; the Stop hook blocks with the notice text as reason - the away-mode enforcement mechanism reused | end of turn |
| `idle` | same queue, delivered as `additionalContext` on the next UserPromptSubmit | John's next touch (passive only in this chunk) |
| `ended` / `lost` | not selectable in chunk 3; resume-into-conversation ships in chunk 4 | - |

Notices pop on read (at-most-once). Backstop for a discarded hook response: the convene intro message already sits in the conversation, so the session learns on its next conversation touch regardless.

**Wake-path derivation rule (2026-07-06 herdr review):** the dispatcher derives each session's wake path from the registry's *live* state at command-execution time, never from the surface's roster snapshot at tap time; and an already-satisfied condition (the session is already a member, already awake, already in the target conversation) resolves as immediate success rather than parking a wake that can never fire (herdr's initial-state-probe pattern).

**Operator:** chunk 1's Sessions rail gains multi-select checkboxes on live rows (`active`/`idle`/`awaiting_*`), a Convene button, a target picker (new conversation + title, or an existing active conversation), and per-row wake-path hints. Writes go through a `commands.js` builder; `node --test` units for the builder and any new derivations.

**Roster polish (added 2026-07-06, John's request):**

1. **Forward `cwd` on agent-status events.** Claude Code's hook stdin already carries `cwd`; the agent-status script just does not send it. Add it to the POST body and have the server fill a record's empty cwd from it. Kills most "(unknown)" rows (sessions discovered via agent-status or rings before their SessionStart existed). Rides the same plugin version bump as this chunk's notice work.
2. **Display the Claude Code session name.** The /rename title is not visible to the server today: hook payloads do not include it and LocalSystem cannot read `~/.claude` (D6). **Empirical gates closed (verified 2026-07-06):** /rename appends a `{"type":"custom-title","sessionId":"<id>","customTitle":"<name>"}` record to the session's own transcript JSONL (`~/.claude/projects/<proj>/<session-id>.jsonl`), and CC also continuously appends `{"type":"ai-title","sessionId":"<id>","aiTitle":"<generated title>"}` records - the auto-titles the `/resume` picker shows, regenerated as the session evolves (ubiquitous: ~1900 records across John's projects; nearly every session has one, vs. custom-title in a single session). Last record of each type wins; custom-title outranks ai-title. Sensor decision: **Watchtower** - it already parses exactly these transcripts for rings, so it extracts both record types and adds `name` (and its source) to the ring payload alongside `model`/`pct`; the server's `apply_rings` enrichment and `_RING_FIELDS` gain the field, and `SessionRecord` gains `name`. Because ai-title regenerates mid-session, the roster label doubles as a living "what is this session doing" description. (Hook-based sensing was the alternative; rejected because it would add per-turn transcript tailing that Watchtower already does. Note: ai-title does NOT flow into the OSC tab title, so it aids roster display, not the backlog heartbeat's tab correlation.)
3. **Rail label chain becomes** custom-title → latest ai-title → sender → cwd tail → "(unknown)". With ai-title ubiquitous, nearly every row gets a meaningful label; `sender` and cwd are the residual fallbacks for brand-new sessions whose first ai-title has not been generated yet.
4. **Needs-attention bit (2026-07-06 herdr review).** herdr's "done" state is not a state-machine state: it is `idle` plus "not acknowledged since the last completion", auto-cleared when the human views the agent. Adopt the same: an `acknowledged` flag on the roster row (flipped by Operator selection / phone open), rendering `idle && !acknowledged` as "done - needs you". No new registry state; mirrors the phone's existing unseen-activity dot for conversations.
5. **State provenance.** `SessionRecord` gains `last_transition_source` (which event set the current state, and when), surfaced in `GET /sessions` and as a row tooltip. The one-field version of herdr's `agent explain`; makes "why does the rail say awaiting_human" answerable at a glance.

**Parent open questions, resolved:**

1. *CLI-agnostic convene notices* - scoped to Claude for now. The turn-end hook's `--cli` abstraction stays; Gemini verification is deferred (the repo already de-emphasized Gemini naming).
2. *Force-pull from multi-party conversations* - no. Skip-and-report; combine is the forceful verb. Revisit only if real usage shows repeated convene-then-combine.
3. *Default-room semantics after the marker retires* - keep join-or-create for `ref`-absent joins; it remains the zero-configuration path for ad-hoc two-agent work.

## Chunk 4 - Android batch (sketch; gets its own UI brainstorm before its plan)

One deploy cycle: a Sessions board page (sibling to the conversation board, reading `sessions/`), the spawn sheet's resume picker switching from conversation-member archaeology to registry `ended` records, convene multi-select with wake-path labels (the fork case labeled "Resume into conversation" explicitly), and the resume-into-conversation action itself (spawn-resume with a convene prompt; the forked session's record supersedes the original's). Layout and interaction details deliberately deferred to a short UI-focused brainstorm - possibly with mockups - before that chunk's plan.

**Resume supersession rules (2026-07-06 herdr review):** honor a session-id change only when it arrives with a recognized lifecycle reason - the SessionStart hook already carries `source` (`startup|resume|clear|compact`), and the spawn-driven resume knows the prior id, so the fork links old to new (`superseded_by` on the old record) at launch. The old id is then **tombstoned**: any straggler event still carrying it (e.g. a dying session's final agent-status POST arriving after the fork) is ignored rather than flapping the superseded record back to `active`. Without the tombstone the resume flow has a real revival bug; herdr's stale-session set is the reference mechanism.

## Chunk 5 - deprecation window and deletion

- `open_conversation` / `enter_conversation` become thin shims over `join_conversation` (+ `message_and_await_agent` for enter's blocking expectation), each logging a deprecation event to the JSONL audit log so remaining usage (spawn prompts, habits) can be found and updated.
- After two plugin versions with a quiet deprecation log: delete the shims, the singleton open marker, `open_peer_future`, the mint-path lobby (`_queue_for_open_peer`'s opener arm), and `message_and_await_agent`'s sole-alive lobby-hold branch (sole-alive uniformly returns `{"status":"conversation_empty", ...}`).
- With the marker gone, `ref`-absent join adopts the parent spec's rule: if exactly one active multi-party-capable conversation is accepting joins, join it; otherwise mint. (Resolved open question 3: join-or-create stays; only the mechanism backing it changes.)
- SKILL.md loses the last legacy paragraphs.
- T-001's parked-pendings chunk follows (see the re-triage decision doc).
- **Cancelled-`message_and_await` session-state reset (found in chunk 2 smoke, 2026-07-06).** Cancelling a `message_and_await_agent` from the CLI cleans up the in-memory wait future correctly - no leaked waiter, confirmed via `/healthz` and `_queue_for_open_peer`'s `CancelledError` cleanup - but nothing resets the caller's session state from `awaiting_agent` back to idle, so the Operator roster shows a stale "waiting on agent" chip until the session's next agent-status hook event or the sweeper reconciles it (verified live: any input to the session clears it). Fix: reset session state in the handler's `CancelledError` path (the normal wait branch, and the lobby-hold branch for as long as it exists). Display-staleness only; low severity; no resource leak.

## Backlog (identified, deliberately unscheduled)

Both from the 2026-07-06 herdr review; both are Watchtower-side sensing work that fits no current chunk. Recorded here so they are a decision away, not a rediscovery away.

- **OSC-title heartbeat.** Claude Code emits an OSC terminal title with a Braille spinner glyph while working and a `✳` prefix (followed by a space) when idle (herdr's `claude.toml` keys its entire working/idle detection off this). Watchtower could read tab titles and add a hook-free working-vs-idle signal to the ring payload - covering the gap where our hooks are blind between tool calls. Lands as ring enrichment, same pipeline as the `name` field in chunk 3's roster polish. **Feasibility verified 2026-07-06:** plain Win32 `MainWindowTitle` exposes only the ACTIVE Windows Terminal tab, but UIA `TabItem` enumeration returns every tab's title including application-set (OSC) titles - tested live with a multi-tab WT and a programmatic title. Watchtower is .NET, so `System.Windows.Automation` is stock. **Coverage boundary (accepted):** WT-hosted sessions only; VSCode integrated terminals are invisible to window/UIA title enumeration. Switchboard-SPAWNED sessions launch via `wt new-tab` and are therefore covered - which is exactly the away-mode population where the heartbeat matters most; at-desk VSCode sessions stay hook-only.
- **Permission-prompt blind spot.** When Claude Code sits at its own tool-approval prompt, our last hook event was PreToolUse, so the roster shows `tool:<name>` while the session is actually waiting on John - the highest-value "needs you" case, invisible today. **Empirical gates closed (live-tested 2026-07-06):** the WT tab title shows an animating Braille spinner while working (generating or tool executing), and `✳` both at idle AND while sitting at a permission prompt - so the title alone cannot expose the wait, but combined with hook state it disambiguates fully: *hook-says-in-tool (PreToolUse with no PostToolUse) + title `✳` = blocked at approval prompt*; in-tool + spinner = executing; no in-tool state + `✳` = idle. **Tab-to-session correlation (also verified):** `/rename` flows into the OSC title body (`✳ OSC-Probe` observed live), and the same name reaches Watchtower via the transcript's `custom-title` record (chunk 3's name feature), closing the loop transcript→(session id, name)→tab title→heartbeat. Remaining caveat: unnamed sessions all title as "Claude Code" and stay ambiguous when several WT tabs are live - fine for the single-spawned-agent case, and naming habits (or a future auto-name on spawn) shrink it further. Status: design-complete, unscheduled; implementation is Watchtower UIA polling + one derived state rule server-side.
- **In-tool-too-long heuristic (companion to the heartbeat; covers what it cannot see).** VSCode-hosted sessions are invisible to title enumeration (verified: the Code window title carries the editor title, and Electron's a11y tree is too heavy/fragile for widget polling), but the server already knows a session is mid-tool-call (PreToolUse with no PostToolUse) and how long (`last_event_at`). Derive: in-tool longer than N minutes ⇒ surface as "possibly waiting on approval" on the roster. Sensor-free, covers ALL sessions uniformly, needs no plugin or Watchtower change - just a derived flag in the registry/roster. Weaker than the title signal (a long build also trips it), so where both exist the heartbeat's definitive answer wins; where only this exists it converts a silent blind spot into a visible maybe. Threshold N is a judgment call at implementation time (start ~5 min).

## Interfaces this roadmap assumes from chunk 1

`SessionRegistry` with per-session state (`active | idle | awaiting_human | awaiting_agent | ended | lost`), the RTDB `sessions/` tree and its Operator rail, `GET /sessions`, and D4's session-keyed identity (pendings keyed by `(conversation_id, cli_session_id)`; answers resolved by request_id). If chunk 1's empirical subagent check (its Task 11) forces roster filtering, the convene UI inherits it for free - selection operates on the roster as rendered.
