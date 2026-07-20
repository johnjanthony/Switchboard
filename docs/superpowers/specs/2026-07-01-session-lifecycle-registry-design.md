# Session Lifecycle Registry: Sessions as First-Class Entities — Design

**Date:** 2026-07-01
**Status:** Proposed (not yet brainstormed with John; drafted by Claude from repo review)
**Scope:** Python server (SessionRegistry, new/extended HTTP ingest, RTDB `sessions/` fan-out, staleness sweeper), plugin hooks (add SessionStart; extend agent-status payload), Android phone + Operator (a Sessions roster), spawn/convene integration. Builds on the 2026-06-25 watchtower-data-into-server design and reuses its verified correlation.
**Companion:** `2026-07-01-convening-simplification-design.md` (consumes the roster this spec provides); `2026-07-01-architecture-review-goal-drift.md` findings D2/D4.

## Problem

The server only learns a Claude Code session exists when the session *speaks* — the first switchboard MCP call auto-creates a conversation and binds routing. A session that is running but has never called a switchboard tool is invisible to the phone and to Operator. Consequences:

- The phone's conversation board is a list of *conversations*, not of *what is running on the desk*. A busy session that hasn't asked anything cannot be seen, connected to, or convened from the phone.
- Convening agents requires the agents themselves to execute the open/enter rendezvous protocol, because the human has no roster to select from (the companion spec's core complaint).
- Watchtower discovers sessions client-side by scanning `~/.claude/projects` transcripts — session awareness lives in a .NET widget, not in the hub, and the phone can't see it. The 2026-06-25 design already pushes ring snapshots (which carry `session_id`) to the server, but as telemetry annotations on conversation members, not as a session inventory.
- Crash detection is indirect: a session that dies without its SessionEnd hook firing simply goes quiet.

The goal: **the server tracks the lifetime of every Claude Code session on the workstation**, from start to end, whether or not it ever touches a switchboard tool — so the phone and Operator can present a live session roster to connect, convene, resume, or observe.

## Verified foundations (what already exists)

1. **Identity correlation.** `cli_session_id == Claude Code session_id == transcript filename stem == the id used by claude --resume` (verified in the 2026-06-25 spec). A session registry keyed by this id is directly actionable for resume and for ring/transcript correlation.
2. **Lifecycle event plumbing, minus one hook.** The plugin already registers `UserPromptSubmit` / `PreToolUse` / `PostToolUse` / `Stop` (agent-status-hook.py → `POST /agent_status`, fire-and-forget, 1s timeout) and `SessionEnd` (cli-session-end-hook.py → marker files swept by `dispatch_session_end_markers`). The only missing lifecycle event is **SessionStart**. Claude Code supports a SessionStart hook; adding it to `hooks.json` completes birth-to-death coverage.
3. **Telemetry push.** Watchtower's `POST /widget-snapshot` (2026-06-25) delivers per-session rings `{session_id, pct, model, status, context_tokens, window, is_error}` — a second, independent liveness signal plus context/model enrichment the hooks can't provide.
4. **Sensor constraint.** The service runs as LocalSystem and cannot read `~/.claude/projects` or reach WSL (verified 2026-06-25). Therefore the registry is **push-fed only** (hooks + Watchtower); no server-side transcript scanning. This design respects that constraint throughout.

## Goals

- Every Claude Code session on the host appears in the registry within one hook-fire of starting, with cwd, surface, start time, and current state.
- Sessions are tracked independently of conversation membership; binding to a conversation becomes an *attribute* of a session, not the condition of its existence.
- The phone and Operator render a Sessions roster: live sessions with state, cwd/project, context ring, conversation binding, and last-activity age — with connect/convene/resume actions (actions specified in the companion spec).
- Ended and lost sessions are distinguishable (clean end vs. presumed-dead), with resume candidacy derivable (`ended` + known cwd + surface ⇒ resumable).
- Watchtower can eventually drop its *own* session-list UI in favor of the hub's (out of scope here; noted as an enabled follow-on).

## Non-goals

- **No server-side transcript scanning** (LocalSystem constraint; Watchtower remains the sole file-system sensor).
- **No cross-host registry.** Single workstation, matching the 2026-06-25 single-host decision; the RTDB schema leaves room to nest under a host key later.
- **No change to ask/notify semantics** or to away-mode behavior.
- **No pending-question persistence** (T-001 stays separate; see review finding D5).
- **Convening UX** — deliberately split into the companion spec so this one stays a pure substrate design.

## Design

### SessionRecord and SessionRegistry (server, in-memory)

```python
@dataclass
class SessionRecord:
    cli_session_id: str            # identity; == transcript stem == resume id
    cwd: str                       # canonical (canonicalization.py)
    surface: Literal["windows", "wsl"]
    started_at: str                # ISO; SessionStart hook, or first-seen fallback
    last_event_at: str             # bumped by every hook event + ring sighting
    state: Literal["active", "idle", "awaiting_human", "awaiting_agent",
                   "ended", "lost"]
    state_detail: str | None       # existing agent-status detail (capped 200)
    conversation_id: str | None    # current binding; None = unbound
    sender: str | None             # display name once known (first tool call)
    model: str | None              # enrichment from Watchtower rings
    context_pct: float | None      # enrichment from Watchtower rings
    end_reason: str | None         # clean end: hook-supplied; lost: "presumed-dead"
    source: Literal["hook", "spawn", "rings"]  # how first discovered
```

`SessionRegistry` lives alongside (not inside) the conversation `Registry`, same single-event-loop access model, mirroring the snapshot store's placement. Conversations keep their own member rosters; the session registry holds the superset. `registry.session_to_conversation_id` becomes derived state published on the record (long-term, the binding map's single source of truth can move here — see review finding D4).

### Event sources → state machine

| Source | Event | Effect |
| :--- | :--- | :--- |
| **SessionStart hook (new)** | session born / resumed / cleared | upsert record, `state=idle`, set `started_at`, cwd, surface (inferred as in `conversation_ops._infer_surface`) |
| agent-status hook | UserPromptSubmit | `state=active` |
| agent-status hook | PreToolUse (ask_human) | `state=awaiting_human` (existing CLEAR_TOOLS mapping) |
| agent-status hook | PreToolUse (message_and_await_agent) | `state=awaiting_agent` (existing WAITING_TOOLS) |
| agent-status hook | PostToolUse / Stop | `state=active` / `idle` |
| SessionEnd (marker sweep) | clean end | `state=ended`, `end_reason` from hook payload |
| spawn.py | spawn/resume launched | upsert provisional record (`source=spawn`) so the roster shows the session immediately, reconciled when its SessionStart arrives |
| widget-snapshot ingest | ring present | bump `last_event_at`; enrich `model`, `context_pct`; **discover** unknown sessions (`source=rings`, e.g. plugin-less or pre-registry sessions) |
| widget-snapshot ingest | ring vanished | input to the staleness sweeper (below), not an immediate transition |
| MCP tool call | any switchboard tool | upsert/refresh (covers sessions that predate the SessionStart hook rollout — the current discovery path, retained as a safety net) |

**Unknown-session grace:** `/agent_status` events for ids with no record upsert a minimal record rather than being dropped (today they are only meaningful for conversation members). This single change is what promotes sessions to first-class.

### Staleness sweeper (crash detection)

A periodic task (piggybacking the existing dispatch-loop supervision from `firebase_supervisor.py`) marks a session `lost` when **all** of: no hook event for `SESSION_LOST_AFTER_SECONDS` (default 900), not `awaiting_human`/`awaiting_agent` (blocked-on-tool sessions are legitimately silent for hours — the pending future is the liveness proof), and its ring is absent from the latest Watchtower snapshot (when Watchtower is itself fresh; if Watchtower is offline, staleness judgments are suspended and the roster shows "sensor offline" rather than guessing). `lost` is reversible: any later event for the id revives the record. This deliberately mirrors the 2026-06-25 staleness philosophy — show honest ages, never present stale as live.

### RTDB fan-out and readers

New top-level tree, written on-change only (same diff discipline as the widget snapshot store):

```
sessions/
  <cli_session_id>/          # ids are RTDB-safe as-is (uuid-like); encode via
    cwd, surface, state,     # canonicalization helpers if that assumption breaks
    state_detail, started_at, last_event_at,
    conversation_id, sender, model, context_pct, end_reason
```

Hydration: on server start, `sessions/` entries in non-terminal states are rehydrated as `state=unknown-age` records and immediately subject to the sweeper — sessions survive a hub restart *as roster entries* even though (per T-001) their pending questions do not. Ended/lost records older than `SESSION_RETENTION_HOURS` (default 72) are pruned by the sweeper to keep the tree and the roster bounded; retained long enough to serve as resume candidates.

**Operator first** (proven pattern from 2026-06-25): a Sessions rail/board reading `sessions/` — state chip, project (cwd tail), ring %, binding, age. Phone follows: a Sessions board sibling to the conversation board; the spawn sheet's "resume dormant session" picker switches from conversation-member archaeology to the registry's `ended` records. Watchtower may later read `/stats`' session roll-up instead of maintaining its own scan-derived list (follow-on, not in scope).

### Plugin changes

`hooks.json` adds `SessionStart` → new `scripts/cli-session-start-hook.py` (same fire-and-forget POST shape as agent-status; payload: session_id, cwd, and the hook's `source` field distinguishing startup/resume/clear). Version-bump the plugin. Sessions on hosts with the old plugin degrade gracefully to today's discover-on-first-call behavior via the MCP-call safety net.

## Open questions

1. **Sweep vs. push for SessionStart.** SessionEnd uses marker files (surviving server restarts); should SessionStart do the same for symmetry, or is fire-and-forget POST acceptable given the MCP-call safety net catches missed births? Proposed: POST only — a missed birth self-heals, unlike a missed death.
2. **Subagent sessions.** Do Task-tool subagents fire SessionStart/SessionEnd with distinct ids? If yes, the roster needs a parent link or a filter (`is_subagent`) to avoid flooding. Needs a quick empirical check before implementation.
3. **Retention as resume inventory.** 72h of ended sessions may be too short for "resume that thing from last week." Alternative: prune from RTDB but let the *spawn resume flow* keep consulting Watchtower's transcript scan for the long tail. Leaning: keep registry short-horizon; long-tail resume stays a Watchtower-assisted flow.
4. **Multiple CLIs.** The turn-end hook already takes `--cli claude`; if Gemini sessions should appear in the roster, `SessionRecord` grows a `cli` field now (cheap) even if only Claude feeds it initially. Proposed: add the field now.
