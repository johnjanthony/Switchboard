# Spawn: Conversation-Aware Redesign — Design

**Date:** 2026-05-20
**Branch context:** `develop`, paired with [`2026-05-19-conversations-collab-redesign-design.md`](2026-05-19-conversations-collab-redesign-design.md) (the parent conversations redesign). The two designs ship together — T-027 surfaces an architectural cleanup that ripples back into the parent's channel/routing model.
**Status:** design approved, ready for implementation plan
**Backlog item:** T-027 ("Bring back spawn with conversation-aware redesign"); folds in T-016 ("Spawn dialog: resume last session").

## Problem

The conversations redesign removes spawn from the Android client because today's spawn presupposes things the new model retires:

- A FAB on Page A whose "Page A" is a channel list (Page A is now a conversation list).
- A per-cwd "channel as session identity" routing key (channels survive only as agent identity; routing was due to become conversation_id).
- Per-cwd away-mode flip-on at spawn time (per-cwd overrides retire).
- A collision dialog keyed on `channels/<cwd>/messages/` being non-empty (channels subtree is wiped).
- A `_COLLAB_INSTRUCTION` template that tells both agents "cwd is the shared session key" (it isn't anymore).

Server-side spawn code stays in the parent design as a no-op pending T-027. This design brings spawn back, wired into the conversations model, and along the way surfaces a structural improvement to that model: **`cli_session_id` is a better primary channel/routing key than `canonical_cwd`**. The improvement is folded back into the parent design (see Section "Parent design amendments"); the two designs are co-dependent.

The redesign also delivers **session resume** — when an agent's CLI session ends, John can pick up exactly that session (both Claude's own conversation history and the corresponding Switchboard conversation context) from his phone.

## Goals and non-goals

### Goals

- Spawn FAB + spawn dialog return on Page A; default UX matches today's mental model (project picker + MRU, optional prompt).
- **Each spawn launches exactly one Claude agent.** The dialog picks a surface (Windows or WSL), a project (cwd relative to that surface's spawn root), an optional initial prompt, and optionally a target conversation to join.
- **Cross-cwd / cross-surface collab from the phone is supported and is a requirement.** A spawn can be directed into an existing Active conversation (via the dialog's "Add to existing" option), and the new agent's cwd does not need to match the cwds of the conversation's existing members. Cross-cwd collab is composed by spawning agents one at a time into the same conversation, plus the combine mechanic (below).
- **A combine mechanic merges two existing conversations.** Available from the phone (long-press a conversation row → "Combine into…" → pick the target) and from a terminal via the `combine_conversations(source_id, target_id)` MCP tool. Source's members move to target; source ends.
- A **resume dialog**, invoked from a long-press "Resume" menu item on Page A rows whose members are all dormant, restores both each member's Claude CLI session memory (via `claude --resume <session_id>`) and Switchboard conversation continuity (new conversation linked to the prior via `continued_from`). The only user input is an optional new prompt. **Single-agent and N-member conversations both resume through this same flow** — the launcher opens one fresh CLI terminal session per prior member (each on that member's original surface, each loading that member's prior CLI session via `--resume`), and all those terminals route into a single new Switchboard conversation.
- Resume always targets the **stored cli_session_id on the prior conversation's members** — never a most-recent-in-cwd fallback. Conversations without captured `cli_session_id` are non-resumable.
- **WSL agents launch via Windows Terminal targeting WSL** — `wt new-tab -- wsl -e bash -lc "cd <wsl-path> && claude ... --session-id <uuid>"`. The two surfaces use **independent working trees**: Windows agents launch under `<windows_spawn_root>/<project>` (e.g. `C:\Work\<project>`); WSL agents launch under `<wsl_spawn_root>/<project>` (e.g. `~/work/<project>` inside the WSL distro — a separate Linux-side clone, NOT the drvfs-mounted view of the Windows path).
- Spawn-collision dialog retires; only block when the prior conversation's session_id is currently active in another conversation (resume case) or when WSL is unavailable for a WSL-targeted spawn.
- Page A history rows render normally; resumed conversations appear as fresh rows carrying `continued_from: <old_id>` metadata.
- **Spawn from the phone auto-enables global away mode** — `global_away_mode` is set to True as part of spawn dispatch if not already on. Rationale: spawn-from-phone strongly implies John is away from his desk; toggling explicitly via the phone before every spawn is unnecessary friction. A toast notification on Page A confirms the flip ("Away mode enabled"). If John wants to spawn while at-desk (rare power-user workflow), he can immediately flip away off via the phone's existing away-mode toggle; spawned agents will then experience at-desk-redirect on their `ask_human` calls per the parent design's global gating. (Optional refinement deferred: spawn dialog adds a "Don't enable away mode" checkbox for the rare case; v1 always-enables.)

### Non-goals

- **Gemini phone-spawn.** Gemini retains full Switchboard support via terminal launch (it can `open_conversation`, `enter_conversation`, participate in conversations, use every Switchboard tool), but the parent design's session_id-as-channel ripple makes Gemini agents temporarily unable to call Switchboard at all until Gemini's hook system gains an equivalent injection capability. Acknowledged and accepted; deferred.
- **Gemini resume mechanics.** Not implemented today.
- **Auto-validating that the WSL-side project exists.** The server doesn't `wsl test -d` the WSL-side project path before launching. If the user picks "Switchboard" but their WSL clone is at a different path (or missing entirely), the wt tab opens, `cd` fails, claude doesn't start, and no member registers. Same observable failure as picking a Windows-side project that doesn't exist; accepted v1 behavior. Mirror is the user's responsibility.
- **WSL distro selection.** The default `wsl.exe` distro is used. Multi-distro setups requiring an explicit `--distribution` flag are out of scope for v1; if friction surfaces, add a server config `wsl_distro` setting.
- **WSL prerequisite verification at spawn time.** The server caches `wsl_home_resolved` at startup via `wsl.exe -e bash -lc 'echo $HOME'`. If that resolution failed and a WSL-targeted spawn is requested, the spawn fails fast with the documented error message; otherwise, runtime failure modes match the Windows-only case.
- **Multiple-prior-sessions picker.** Long-press a specific row to resume that one; no separate "browse and pick from a list of older sessions" affordance.
(Spawn flipping global away-mode is now an explicit feature — see Goals.)
- **Older agent installs without the plugin's hook** — they can't call Switchboard once the parent design's session_id-as-channel amendment ships, because `cli_session_id` becomes a required parameter. Plugin install is the documented path.
- **Server-assigned sender names.** Senders are agent-supplied (the spawn prompt encourages picking a unique label; John can suggest names in the prompt). The server doesn't enforce uniqueness — sender is just a display label.

## Design

### Session-ID capture mechanism

**A `PreToolUse` hook reads each Claude session's `session_id` and `cwd` from the hook input JSON and merges them into every switchboard MCP tool call's input.** The agent never knows or supplies its own session_id — the hook injects on its behalf.

**Verified behavior** (see [`scripts/verify/`](../../../scripts/verify/) for the reproducible test scripts):

- `PreToolUse` hooks fire on every tool call, including MCP tool calls, when the matcher field is omitted.
- The `matcher` field is unreliable across the deployed Claude Code version — the safe pattern is to omit it and let the hook script self-filter on `tool_name`. Setting a regex matcher caused internal Claude Code errors on tool calls that didn't match (`"undefined is not an object (evaluating 'A.match')"`).
- The hook's `updatedInput` field **replaces** the tool input, despite docs claiming it merges. The hook must explicitly copy every field of the original `tool_input` into the returned `updatedInput`, then add the injected fields.
- Backslash escaping in `command` strings is broken on Windows — paths with `\` get stripped. Use forward slashes (Python accepts them on Windows).
- Hook input contains `session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`, `tool_name`, `tool_input`, `tool_use_id` at top level. `cwd` is the agent's actual cwd at the time of the call (canonicalization is the server's concern).

**Hook script** (`hooks/cli-session-injector-hook.py` — new, bundled in the plugin):

```python
import json
import sys

payload = json.load(sys.stdin)
tool_name = payload.get("tool_name", "")
if not tool_name.startswith("mcp__switchboard__"):
    # Not a switchboard call — exit cleanly with no output;
    # claude treats this as "allow, no modification".
    sys.exit(0)

tool_input = payload.get("tool_input", {}) or {}
merged = dict(tool_input)
merged["cli_session_id"] = payload.get("session_id")
merged["cwd"] = payload.get("cwd")

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "updatedInput": merged,
    }
}))
```

**Plugin install delta** (`hooks/hooks.json` — extends the existing PreToolUse entry and adds a new SessionEnd entry):

```json
"PreToolUse": [
    { "hooks": [
        { "type": "command", "command": "python ${CLAUDE_PLUGIN_ROOT}/scripts/agent-status-hook.py", "timeout": 2 },
        { "type": "command", "command": "python ${CLAUDE_PLUGIN_ROOT}/scripts/cli-session-injector-hook.py", "timeout": 2 }
    ]}
],
"SessionEnd": [
    { "hooks": [
        { "type": "command", "command": "python ${CLAUDE_PLUGIN_ROOT}/scripts/cli-session-end-hook.py", "timeout": 5 }
    ]}
]
```

PreToolUse hooks fire on the same event; ordering doesn't matter (different output paths — `cli-session-injector-hook.py` outputs `updatedInput`; `agent-status-hook.py` doesn't). SessionEnd is its own event (see "Session lifecycle monitoring" below).

### Tool surface (parent design amendment)

Switchboard MCP tools change in two structural ways:

1. **Drop the agent-supplied `channel` parameter.** The agent never passes cwd or any cwd-derived key.
2. **Add `cli_session_id` (required) and `cwd` (required) as hook-injected parameters.** Required because they're hook-injected and the server depends on them for routing.

Plus three new tools and two modified ones to support the new collab-composition model:

| Tool | Status | Purpose |
|---|---|---|
| `ask_human(sender, question, ..., cli_session_id, cwd)` | existing | Ask John a question (away-mode-gated per parent). |
| `notify_human(sender, message, ..., cli_session_id, cwd)` | existing | FYI notification (away-mode-gated per parent). |
| `send_document_human(sender, path, ..., cli_session_id, cwd)` | existing | Deliver a file. |
| `message_and_await_agent(sender, message, ..., cli_session_id, cwd, title?)` | existing | Speak to peers; `message` required (non-empty). Same shape as the parent design. Newly-arrived members use `enter_conversation()` to wait for an intro from peers without speaking first; they don't need a no-speak form of this tool. |
| `leave_conversation(sender, parting_message, cli_session_id, cwd)` | existing | Explicit leave; removes member from conversation. |
| `open_conversation(sender, title?, cli_session_id, cwd)` | **new** | Promote the caller's current conversation to be the global `openConversation` singleton — allowing other agents to self-join via `enter_conversation()`. If another conversation was open, that one loses the marker. Optional `title` updates the conversation's title. `sender` lets the agent declare/update its display name as part of the call (consistent with the rest of the tool surface). |
| `enter_conversation(sender, cli_session_id, cwd)` | **modified** | Unified "join + listen for intro" tool. `sender` is the agent's declared display name — required because this call may add the agent as a new member to a conversation, and the member entry needs a sender label. See the parent design's "Tool surface" section for the full branching behavior. |
| `combine_conversations(source_id, target_id, cli_session_id, cwd)` | **new** | Move all members of `source_id` into `target_id`. `source_id` ends. Available from agents too (not just the phone) — useful in dialogue with John, e.g. "merge me with my partner's session." See "Combine mechanic" below. |
| `lookup_conversation_ids(cwd?, sender_contains?, title_contains?, cli_session_id, cwd)` | **new** | Return a list of `conversation_id`s matching the filter criteria (at least one of `cwd`, `sender_contains`, `title_contains` required). Lets agents resolve concrete `conversation_id`s — e.g. "find the conversation titled 'switchboard plugin work'" or "find the conversation containing a member named 'Claude-WSL'." |
| `set_away_mode(value, cli_session_id, cwd)` | existing | Flip the global away-mode flag. |

Calls missing `cli_session_id` are rejected at the MCP boundary with:

```
ERROR: cli_session_id required. This call appears to come from a Claude
session without the switchboard plugin's PreToolUse hook installed, or
from a non-Claude agent. Switchboard tools require hook-injected
session_id under the v2 routing model.
```

### Conversation state model (parent design amendment)

The parent design's Open/Closed/Ended state machine collapses to **Active / Ended** plus a **global pointer**:

- **Active** — has at least one member (alive or dormant). Accepts new members via server-side mechanisms (spawn-into-existing, combine, resume). Does not by itself accept agent-initiated self-joins.
- **Ended** — terminal. No members. Persists in Firebase as history. Reachable only via:
  - Explicit `leave_conversation` by the last alive member (removes the last live member; if no dormant members either, the conversation ends; the leaving member's session falls back per the **session-fallback rule** below).
  - Force-end (John taps a UI control; all members removed and each falls back per the **session-fallback rule**; conversation ends).
  - Being the `source_id` of a `combine_conversations` call (members move to target; source ends; dormant members move via combine_resume — see Combine mechanic).
  - All members hit the unrecoverable `clear`/`compact` SessionEnd state simultaneously — extremely rare; treated as a hard fault requiring force-end.

**Session-fallback rule.** When a session is removed from a conversation (via `leave_conversation`, force-end, or any path other than combine — which moves them to target instead of removing), the session is NOT orphaned:

- **If `global_away_mode == True`**: the session is re-bound to its **home conversation** (each session has a `home_conversation_id` set at first switchboard contact — typically the spawn-pre-bound conversation, or the auto-created Active conversation from a first xxx_human call). If the home conversation is still Active, the session re-binds to it and is added back as a member (alive). If the home is Ended (because it ended at some point while this session was away from it), the server creates a new Active conversation for the session and updates the session's home pointer to the new one. The session's CLI context is unchanged; only its switchboard routing falls back.
- **If `global_away_mode == False`**: the session is unbound. Subsequent `ask_human` / `notify_human` calls from this session get at-desk-redirected per the parent design's global gating; the agent's output reaches John via the terminal until away mode flips on again. (If away later flips on while the session is still alive and unbound, the next switchboard MCP call auto-creates a new Active conversation per the existing "first xxx_human from an unbound session" rule, and updates the session's home pointer.)

The home pointer is a per-session server-side variable: `_session_home_conversation_id: dict[cli_session_id, conversation_id]`. Persisted to Firebase under `cli_sessions/<session_id>/home_conversation_id` so it survives restart.

**`openConversationId: conversation_id | None` — server-side global pointer.** At most one Active conversation is "the open one" at any time. The `openConversation` is the conversation that agents can self-join via `enter_conversation()`. The pointer is set/replaced by the `open_conversation()` MCP tool (caller's current conversation becomes the open one) and is cleared automatically when:

- The conversation it points to transitions to Ended.
- Another `open_conversation()` call promotes a different conversation (replacement).
- (Optional in v1, not specified here) A dedicated `close_conversation()` tool — deferred unless friction surfaces.

**Member states.**

- **Alive** — `cli_session_id` is currently bound in `_session_to_conversation_id`; the agent is running and responsive.
- **Dormant** — `cli_session_id` is captured on the member entry but not currently bound in `_session_to_conversation_id`; the agent's CLI session has exited (SessionEnd fired) but the member is retained for revival via resume/combine.
- **Permanently lost** — the underlying CLI session is unrecoverable (`clear`/`compact` SessionEnd reason). Member is retained for visibility but can't be revived.

**Lifecycle transitions:**

```
Active <----> Active (members come and go, dormant<->alive via resume/combine)
   |
   v
Ended (terminal)
```

No state transitions back from Ended.

### Session lifecycle monitoring

Three Claude Code hook events together cover the session lifecycle from switchboard's perspective:

| Hook event | Switchboard use | Already plumbed? |
|---|---|---|
| `PreToolUse` | Inject `cli_session_id` and `cwd` on every switchboard MCP call (this design). Plus tool-status updates (existing `agent-status-hook.py`). | New (this design) + existing |
| `Stop` | Turn-end; tells switchboard the session is idle (existing `turn-end-hook-away-mode.py` + `agent-status-hook.py`). | Existing |
| `SessionEnd` | **Session has exited.** Switchboard marks the member as dormant (session no longer alive but member retained in the conversation, with cli_session_id stored so the member can be revived via resume or combine). | **New (this design)** |

A new `cli-session-end-hook.py` is added to the plugin's hook bundle, registered on `SessionEnd`. It POSTs to a new server endpoint `POST /cli-session/end` with `{session_id, reason}`. Hook input contains `session_id` plus a `reason` field with values `logout | prompt_input_exit | clear | compact | other` (per Claude Code docs).

**Server handler (`POST /cli-session/end`)** with payload `{session_id, reason}`:

1. Look up `conversation_id = _session_to_conversation_id.pop(session_id, None)`.
2. If no binding: no-op (session was never registered, or already cleaned up).
3. Otherwise, mark the member as **dormant** (do NOT remove from conversation):
   - Find the conversation's member entry keyed by this `session_id`.
   - Set `member.alive = False`; record `member.session_ended_at = now()` and `member.session_end_reason = reason`.
   - Append a system message to the conversation log: `"<sender>'s session ended (<reason>); member is now dormant."`
   - The member entry stays in `members_active` with the `alive=False` flag. (Member is still considered a conversation participant; just not currently responsive.)
   - If any blocked members in the wait queue, wake the FIFO-oldest with the system message in their payload — peers should know one of their colleagues went dormant.
   - Cancel any pending `ask_human` futures for `(conversation_id, session_id)` so the phone doesn't keep waiting.
4. **The conversation does NOT transition to Ended** even if all members are now dormant. Conversations only transition to Ended via explicit `leave_conversation` from the last alive member, force-end from John, or being the `source` of a `combine_conversations` call. Dormant members can be revived via the **resume** mechanic (long-press) or via **combine** (when a combine moves a dormant member, that member's session is auto-resumed as part of the combine).

**`reason` field interpretation.** `logout` / `prompt_input_exit` / `other` → standard dormant-mark with the reason annotated. `clear` and `compact` are different — the prior session_id is being abandoned (`clear` resets context; `compact` rewrites the session). In those cases the member is effectively gone: the new claude process (if any) has a NEW session_id that won't match. For `clear`/`compact` reasons, the server marks the member as dormant AND records that the underlying session is permanently unrecoverable (a flag like `member.session_lost_permanently = True`). Such members are non-resumable; if all members of a conversation are in this state, the conversation effectively can't be brought back. Force-end is the only cleanup path.

**Limitations.** SessionEnd is best-effort:

- It fires on clean exits (`/exit`, Ctrl+D, terminal closed gracefully).
- It does **not** fire on SIGKILL, machine crash, BSOD, network loss while running. Those leave a member as "stale alive" (the server still thinks they're alive but they're actually dead). Indistinguishable from a momentarily-unresponsive member; eventual mitigation is [T-003 (Collab session garbage collection)](../../tracking/backlog.md). The SessionEnd hook handles the dominant orderly-exit case immediately.

**Parent design amendment.** The parent design's "Termination triggers" list does NOT gain SessionEnd. Conversations are durable across session lifecycles in the new model — agents leave and come back via resume/combine.

### Resume mechanic

**Same flow for single-agent and multi-member conversations.** Both are resumed via the same long-press menu item with the same dialog UX and the same eligibility checks. The dialog's only user input is an optional new prompt; everything else (member count, sender names, per-member CLI session_ids, per-member surface, per-member cwd) is read from the prior conversation's members. The launcher opens N fresh CLI terminal sessions (Windows Terminal tabs) — one per prior member — each on the member's original surface, each running `claude --resume <stored_session_id>`. All N terminals route into a **single** new Switchboard conversation (one Page B message window on the phone, with N members joining it).

**Resume target.** A long-pressed Active conversation that has at least one dormant-and-resumable member, OR an Ended conversation with at least one such member. v1 is long-press only; an FAB-path "find most recent resumable in cwd" affordance is a follow-on if friction surfaces.

**Resume eligibility.** A conversation is resumable iff **at least one** member meets the per-member resumable criteria:

- Member has a non-null `cli_session_id`.
- Member `alive == False` (dormant) AND `session_lost_permanently == False`.
- Member's `cli_session_id` is NOT currently mapped in `_session_to_conversation_id` (sanity check; usually trivially true if dormant).

Members that meet all three are **resumable**. Members that fail any are skipped during resume — they stay where they are.

**Partial resume semantics.** When resume executes, ONLY the resumable subset of members fork into the new continuation conversation. Specifically:

- Alive members stay in source (they didn't go dormant; nothing to resume).
- Permanently-lost members stay in source (un-revivable).
- Members currently active elsewhere are skipped (their session is in another conversation; resuming would cause a binding conflict).
- Dormant-and-resumable members move into the new continuation conversation via `claude --resume <id>`.

**Source disposition:** if ALL members were resumable (so all moved out), source Ends as part of the resume. If at least one member stays (alive, permanently-lost, or active-elsewhere), source remains Active with those members; the new conversation appears alongside it as a forked continuation. Both rows show on Page A; the new one has `continued_from = source.id` pointing back.

If NO member meets the resumable criteria (e.g., all members alive, or all permanently-lost), the long-press Resume menu item is shown but greyed, with a tooltip explaining (`"no resumable members — alive members can't be resumed; permanently-lost members can't be revived"`).

**Sibling-branch case is intentionally not blocked.** If another conversation already exists with `continued_from: <this_id>`, the chain has already been resumed once. Re-resuming creates a sibling branch. v1 allows this. Flagged for future tightening if it surfaces as friction.

**Session-file retention limit.** Claude Code prunes session files (`~/.claude/projects/<dir-hash>/<session_id>.jsonl`) after `cleanupPeriodDays` (default **30 days**) per [Claude Code Sessions docs](https://code.claude.com/docs/en/sessions.md). A conversation whose members went dormant 31+ days ago cannot be resumed even if all the Switchboard-side metadata is intact — the underlying CLI session file is gone.

v1 handles this **the simple way**: don't proactively stat the file on the server side. When the launcher invokes `claude --resume <session_id>` and the file is missing, claude exits with an error. The wt tab closes (or sits with the error visible). The agent never connects to switchboard, so no member ever revives in the new conversation. Page A shows the new (continuation) conversation as a fresh row with the "Resuming '<title>' (continued from <source.id>)" system message but no agent activity. John sees the failure as "agent didn't come back" rather than getting a pre-flight error.

This is intentionally lo-fi for v1 — a pre-flight `wsl test -f` / `Test-Path` check across both surfaces adds platform-specific logic for a rare case. Phone-side mitigation is a **warning indicator on the Page A row** when any of the conversation's members has had no observed activity for **25+ days** — a heads-up that resume eligibility is approaching the cleanup window. Implementation: the conversation row's metadata renders a `⚠️` (or equivalent) badge when `max(member.session_ended_at) > now - 5 days` for any member whose `session_ended_at` is older than 25 days. Tooltip: `"Session approaching Claude Code's 30-day session cleanup window. Resume may fail."`

Future enhancement (deferred): operator can bump `cleanupPeriodDays` in their Claude Code settings to extend the window for known-long-lived workflows. Documented as an operator concern; spec doesn't manage it.

**Resume mints a new conversation.** The prior conversation is not reopened. Instead:

1. New conversation_id; `state = Active`; `title = prior.title`; `continued_from = prior.id`. The prior conversation's `openConversation` marker (if any) does NOT carry over.
2. Page A shows the new conversation as a fresh row; Page B (eventually) can render a `"continued from <prior_title>"` footer with a tap-to-load-predecessor affordance.
3. CLI continuity is via `claude --resume <session_id>` per member, executed by the launcher; **verified** that `--resume` reuses the prior session_id (the file `~/.claude/projects/<dir-hash>/<session_id>.jsonl` grows in place, no new file created).
4. If the prior conversation was Active (all-dormant), it ends as part of the resume (force-end-equivalent — its members have been migrated to the new conversation).

### Combine mechanic

The combine mechanic moves all members of a source conversation into a target conversation, effectively merging two conversations into one. Available from the phone (long-press a row → "Combine into…") and from agents via the `combine_conversations(source_id, target_id)` MCP tool.

**Use cases.**

- John has been chatting with Claude-A in conversation X about topic 1; with Claude-B in conversation Y about topic 2; he wants them to discuss the intersection. Long-press X → Combine into → pick Y → confirm. X's members move to Y; X ends.
- Two agents working independently realize their tasks overlap; one agent calls `combine_conversations(my_conv_id, partner_conv_id)` to merge them.

**Source and target naming convention.** "Source" = the conversation whose members move out (and which ends). "Target" = the conversation members move into (and which absorbs the source's members). From the long-press UX, the long-pressed row is the source; the picker selects the target. From the MCP tool, args are explicit.

**Eligibility.**

- Source must be Active (not Ended).
- Target must be Active (not Ended).
- Source ≠ Target.
- Source's members may be a mix of alive and dormant; dormant members are auto-resumed as part of combine (launcher opens new CLI terminal sessions for them via `--resume`).
- Permanently lost members (`session_lost_permanently`) cannot be moved; they remain in the source's `members_history` for visibility. If ALL of source's members are permanently lost, the source has nothing to move; combine rejects.

**Combine implementation.** For each member of source:

1. **If member is alive** (`cli_session_id` in `_session_to_conversation_id`): rewrite `_session_to_conversation_id[cli_session_id] = target_id`. Move the member entry from `source.members_active` to `target.members_active`. Reset `member.last_seen_seq = len(target.messages)` so the moved member's next wake delivers only future messages (target's prior history is not replayed into their wake payload).
2. **If member is dormant**: launch `claude --resume <cli_session_id> --dangerously-skip-permissions` on the member's stored surface (per the per-agent spawn-pending file pattern from resume). When the resumed Claude makes its first MCP call, the PreToolUse hook injects `cli_session_id`; server pre-binds it to `target_id` (set BEFORE launching, same pattern as resume); the member arrives in target.
3. **If member is permanently lost**: skip (leave in source.members_history).

**Server-side message markers:**

- Target gains a system `agent_msg`: `"Merged with '<source_title>' (N members, M source messages absorbed)."`
- Source gains a final system `agent_msg`: `"Merged into '<target_title>'"` and transitions to Ended.

**Source message log preservation.** Source's `messages/` subtree stays in its Firebase node as Ended history. NOT concatenated into target's log. John can still scroll source's Page B to see the discussion history that led to the merge. (Future enhancement could allow chronological log-merging if friction surfaces.)

**Intro prompt for moved members.** After combine, the server uses the existing `inject_queue/<conversation_id>/` Firebase node (parent design) to inject a prompt into each moved member:

```
You've been moved into conversation '<target_title>'. To receive an
intro from the agents already in this conversation, call
enter_conversation() — that queues you in the wait queue without
writing a speak event, and the next peer speak will deliver to you.
After receiving context, introduce yourself via
message_and_await_agent and proceed.
```

The agent reads the inject on its next turn, calls `enter_conversation()`, blocks; on the next peer speak event the moved agent wakes with the peer's message. This handshake gets newcomers oriented to the target conversation's existing context.

The semantics: `enter_conversation()` is the "I want to be in a conversation and listen for context" tool. For an agent already in a conversation (e.g., post-combine), the server detects that the caller's `cli_session_id` is already bound to a conversation and just queues them in that conversation's wait queue without any migration. For an agent not yet bound or in a non-`openConversation`, the tool's other branches kick in (join the open, migrate from current to open).

**Open conversation marker handling.** If source was the `openConversationId`, that pointer clears on source-end. Target is unaffected (target's open status, if any, stays). If the user wants target to be open for further joins, they must explicitly invoke `open_conversation` from within target.

### Spawn dialog UX (fresh)

Opened via the FAB on Page A. Each spawn launches exactly one Claude agent, on a chosen surface (Windows or WSL), in a chosen project, optionally joining an existing conversation.

```
┌──────────────────────────────────────────┐
│ Spawn Claude Session                     │
├──────────────────────────────────────────┤
│                                          │
│ Surface          (*) Windows  ( ) WSL    │
│                                          │
│ Project              [develop      ▼]    │
│                                          │
│ Initial Prompt (optional)                │
│ ┌──────────────────────────────────────┐ │
│ │                                      │ │
│ │                                      │ │
│ └──────────────────────────────────────┘ │
│                                          │
│ Conversation                             │
│   (*) Create new                         │
│   ( ) Add to existing:                   │
│       [select Active conversation… ▼]    │
│                                          │
│                  [Cancel]      [Spawn]   │
└──────────────────────────────────────────┘
```

**Fields.**

- **Surface**: radio control. Default Windows. Determines launch path and project-path resolution. Disabled (with tooltip) if WSL is selected and `wsl_home_resolved` is unavailable on this host.
- **Project**: MRU dropdown with per-item delete. Just a project name — the server is surface-aware and maps the name to `<windows_spawn_root>/<project>` for Windows or `<wsl_home_resolved>/<wsl_spawn_root_segment>/<project>` for WSL at spawn time. The picker doesn't change contents when the user toggles the Surface radio — the assumption is that the two surfaces' working trees share project names by convention. If a chosen name doesn't resolve on the selected surface, the spawn fails visibly when `cd` errors (covered by the "no auto-validation" non-goal).
- **Initial Prompt**: free-form text, optional. Passed verbatim to the spawned agent. Use it to direct the agent ("start a new collab session for X", "join the open collab", "suggest a sender name unique from claude-win-A", etc.).
- **Conversation**:
  - **Create new**: server pre-mints a new Active Conversation; the spawned agent will be the sole initial member. (To make it the `openConversation`, instruct the agent via the prompt — e.g., "call `open_conversation('title')` so other agents can join you.")
  - **Add to existing**: dropdown lists all currently-Active conversations (showing title + member-roster summary). Server pre-binds the spawned agent's `cli_session_id` to the selected conversation_id so the agent joins on first MCP call.
- Spawn button enabled when Project is non-empty AND a Conversation option is selected (either Create new or Add to existing with a target picked).

**No sender field.** Sender is agent-supplied. The spawn prompt template encourages the agent to pick a unique name (with guidance from John's prompt if any) and instructs the agent on the conversation-context if "Add to existing" was used (so the agent knows what peers it'll find there).

**No spinner.** Multi-agent collabs are composed by spawning more than once (each spawn adds one agent to the conversation) or via combine.

### Resume dialog UX

Opened from the Page A long-press menu's "Resume" item on a row whose members are all dormant (or whose conversation is Ended). Pre-filled from the prior conversation's metadata; the only user input is an optional new prompt.

```
┌──────────────────────────────────────────┐
│ Resume Conversation                      │
├──────────────────────────────────────────┤
│                                          │
│ "Fix the failing canonicalization tests" │
│ Claude-Win, Claude-WSL · 47 msgs         │
│ Last activity 2h ago                     │
│                                          │
│ New prompt (optional)                    │
│ ┌──────────────────────────────────────┐ │
│ │                                      │ │
│ │                                      │ │
│ └──────────────────────────────────────┘ │
│                                          │
│                [Cancel]      [Resume]    │
└──────────────────────────────────────────┘
```

Title above the input is the prior conversation's `title`. Member roster lists each member's agent-supplied `sender`. Surfaces and cwds are visible in the conversation row's detail surface, not crowded into this dialog header.

### Combine dialog UX (long-press target picker)

Opened from the Page A long-press menu's "Combine into…" item on any Active conversation. The long-pressed row is the source.

```
┌──────────────────────────────────────────┐
│ Combine '<source title>' into…           │
├──────────────────────────────────────────┤
│                                          │
│ Select target conversation:              │
│ ┌──────────────────────────────────────┐ │
│ │ ○ <target 1 title>                   │ │
│ │     Claude-WSL · 12 msgs · 4m ago    │ │
│ │ ○ <target 2 title>                   │ │
│ │     Claude-Win-A, Gemini · 87 msgs   │ │
│ │ …                                    │ │
│ └──────────────────────────────────────┘ │
│                                          │
│                [Cancel]   [Combine]      │
└──────────────────────────────────────────┘
```

List shows every Active conversation other than the source. Picker is single-select. Confirm dialog appears before the actual merge (`"Combine '<source>' into '<target>'? Source will end; its members move to target."`).

### Page A long-press menu

Existing items (Hide / Unhide / End) gain two new items:

```
┌──────────────────────────────────────┐
│ Resume                               │  ← shown when all members dormant
│ Combine into…                        │  ← shown on Active conversations
│ Hide / Unhide                        │
│ End conversation                     │  (Active conversations only)
└──────────────────────────────────────┘
```

- **Resume** — shown when the conversation has at least one member and every member is dormant (or the conversation is Ended). Greyed when any member has `session_lost_permanently == True` or any member's session is currently active in another conversation; the tooltip names the specific blocker.
- **Combine into…** — shown on all Active conversations. Always enabled (no preconditions on the source side beyond "Active and has at least one movable member").
- **End conversation** — Force-end. Available on all Active conversations. Confirmation dialog notes that current members will fall back to their home conversation (if away mode is on) or to terminal output (if off) — never orphaned.

### Server-side spawn flow (fresh)

1. **Phone writes structured spawn command** to Firebase:
   ```
   spawn_commands/<push_id>/
     type                       "fresh"
     surface                    "windows" | "wsl"
     project                    (str)
     prompt                     (str | null)
     target_conversation_id     (str | null — null means "create new")
     issued_at                  (iso-8601)
   ```
2. **Server dispatch picks up** the `fresh` record.
3. **Validation:**
   - Rate-limit (60s between any two spawns).
   - `_user_has_interactive_session()` (quser check — schtasks needs a logged-in desktop session).
   - `surface in ("windows", "wsl")`.
   - If `surface == "wsl"` and `wsl_home_resolved` is unavailable → reject with `"WSL spawn requested but WSL is not available on this host."`
   - `project` resolves to a directory under the surface's spawn root.
   - If `target_conversation_id` is provided: the target must be Active (not Ended). Reject otherwise.
4. **Auto-enable away mode.** If `global_away_mode == False`, set it to True. Persist to Firebase (`global_settings/away_mode`). Subsequent steps proceed in away-mode-on state. The phone surfaces a toast/banner confirming the flip.
5. **Resolve surface-specific project path:**
   - `surface == "windows"`: `project_path = config.windows_spawn_root / project` (e.g. `C:\Work\Switchboard`).
   - `surface == "wsl"`: `project_path = config.wsl_home_resolved + "/" + config.wsl_spawn_root_segment + "/" + project` (e.g. `/home/john/work/Switchboard`).
6. **Mint or resolve Conversation:**
   - If `target_conversation_id` is set: use it; the spawned agent joins this existing conversation. No new conversation is created.
   - Otherwise: mint new `conversation_id = uuid4()`; state = Active; `members_active = {}`; title derived from prompt's first line (truncated to 80 chars) or `"Spawning Claude in <project>"`; opening system `agent_msg` written to `conversations/<id>/messages/`. No `openConversationId` change — if the user wants this new conversation to be open, the agent's prompt directs them to call `open_conversation()`.
7. **Pre-generate per-agent metadata.** `cli_session_id = uuid4()`. No sender assignment server-side (agent picks their own via the prompt template's guidance).
8. **Bind session_id to conversation.** `_session_to_conversation_id[cli_session_id] = conversation_id`.
9. **Write spawn-pending file** for the launcher:
   ```json
   {
     "type": "fresh",
     "conversation_id": "<id>",
     "agents": [
       {
         "surface": "windows",
         "cli_session_id": "<uuid>",
         "prompt": "<formatted prompt>",
         "project_path": "C:/Work/Switchboard",
         "join_existing": false
       }
     ]
   }
   ```
   For the add-to-existing case, `join_existing` is true and the prompt template includes the target's title, current member roster (with each member's alive/dormant status), and a short window of recent non-system messages so the spawned agent knows who they're joining. The prompt directs the agent to call `enter_conversation(sender='<your_name>')` as its first switchboard action — which queues the new member in the target's wait queue without writing a speak event and delivers the recent conversation log when the next peer speaks. Conversation_id is NOT included in the prompt: the agent never passes it (routing is session_id-keyed and hook-injected), so surfacing the raw UUID would be noise.
10. **Trigger launcher.** `schtasks /run /tn SwitchboardSpawn`.
11. **Launcher iterates `$params.agents`** (just one element for fresh spawns) and branches on `$agent.surface` to pick the launch shell — see Launcher script section below.
12. **Claude starts** with the pre-determined `cli_session_id` (verified — see [Test 1](../../../scripts/verify/test1-session-id.ps1)). The plugin's PreToolUse hook fires on the first switchboard MCP call; injects `cli_session_id` and `cwd`. Server resolves `_session_to_conversation_id[cli_session_id]` → routes the call into the conversation.
13. **First MCP call adds the member entry.** Server creates the member with `(sender, cli_session_id, cwd, surface, alive=True)`. `sender` comes from the call's `sender` parameter (agent-supplied per prompt guidance). For a `join_existing` spawn, the agent's prompt template directs it to call `enter_conversation(sender='<your_name>')` first — that queues the new member in the conversation's wait queue and delivers the recent log on the next peer speak event. The agent introduces itself via `message_and_await_agent` only AFTER receiving that context. This matches the `enter_conversation` semantics in the parent 2026-05-19 design ("join + listen for intro") and avoids an unsolicited speak that would land before the joining agent had any conversational grounding.

### Server-side spawn flow (resume)

1. **Phone writes:**
   ```
   spawn_commands/<push_id>/
     type                     "resume"
     source_conversation_id   (str)
     prompt                   (str | null)
     issued_at                (iso-8601)
   ```
2. **Server dispatch picks up.**
3. **Re-validate eligibility** at dispatch time:
   - `source.state == Active` AND all members dormant, OR `source.state == Ended`.
   - Every member's `cli_session_id` is non-null AND `session_lost_permanently == False`.
   - None of those session_ids are currently in `_session_to_conversation_id`.
4. **Cancel any pending futures** keyed by any of the source's session_ids.
5. **Mint new Conversation:**
   - New `conversation_id`; `state = Active`; `title = source.title`; `continued_from = source.id`; members empty.
   - Opening `agent_msg`: `"Resuming '<title>' (continued from <source.id>)."`
   - The new conversation does NOT inherit any `openConversation` marker the source may have held.
6. **End the source conversation** if it was Active (all-dormant). Source → Ended. (If it was already Ended, no change.)
7. **Bind prior session_ids to the new conversation.** For each `member in source.members`:
   - `_session_to_conversation_id[member.cli_session_id] = new_conversation_id`.
8. **Spawn-pending file** — carries `surface` and `project_path` verbatim from each prior member (so a Windows member resumes on Windows; a WSL member resumes on WSL). Sender is also carried verbatim — agents will continue to use the names they had:
   ```json
   {
     "type": "resume",
     "conversation_id": "<new_id>",
     "continued_from": "<source_id>",
     "agents": [
       {"surface": "windows", "cli_session_id": "<prior uuid>", "prompt": "<resume template + optional new context>", "project_path": "C:/Work/Switchboard", "prior_sender": "Claude-Win"},
       {"surface": "wsl",     "cli_session_id": "<prior uuid>", "prompt": "<resume template + optional new context>", "project_path": "/home/john/work/Switchboard", "prior_sender": "Claude-WSL"}
     ]
   }
   ```
   `prior_sender` is informational — the agent's CLI `--resume` will restore its own context including how it identifies itself; this field lets the prompt template remind the agent ("you were previously known as X").
9. **Launcher invokes** the surface-appropriate launch command with `--resume <cli_session_id>` in place of `--session-id <cli_session_id>`.
10. **Resumed Claude starts.** Per [Test 2](../../../scripts/verify/test2-resume.ps1), `--resume` reuses the same session_id (the original `<id>.jsonl` file grows in place). The PreToolUse hook injects the same `cli_session_id` on the first MCP call; the pre-bound `_session_to_conversation_id` resolves cleanly. Each resumed member adds themselves back as alive on their first MCP call (the member-add-on-first-call rule).

### Server-side combine flow

Triggered by either a phone-side `combine_commands` Firebase record or an agent's `combine_conversations(source_id, target_id)` MCP tool call. Both paths land in the same handler.

1. **Validate:**
   - `source_id != target_id`.
   - Both conversations exist and are Active.
   - Source has at least one member not in `session_lost_permanently` state.
2. **Compute member list to move.** Source's alive + dormant members. Permanently lost members stay in source (preserving the audit trail; they don't move).
3. **For each alive member:**
   - Rewrite `_session_to_conversation_id[cli_session_id] = target_id`.
   - Remove the member entry from `source.members_active`; add it to `target.members_active` with `last_seen_seq = len(target.messages)` (so their next wake gets only future messages).
   - Push an inject message into `inject_queue/<target_id>/`: the moved-agent intro prompt (see Combine mechanic section above).
4. **For each dormant member:**
   - Pre-bind `_session_to_conversation_id[cli_session_id] = target_id`.
   - Write a per-agent spawn-pending file as if this were a resume of just that one member:
     ```json
     {
       "type": "combine_resume",
       "conversation_id": "<target_id>",
       "from_source": "<source_id>",
       "agents": [
         {"surface": "<member.surface>", "cli_session_id": "<member.cli_session_id>", "prompt": "<combine-intro template>", "project_path": "<member.project_path>", "prior_sender": "<member.sender>"}
       ]
     }
     ```
   - Trigger the launcher to revive that member. On first MCP call, the resumed member adds themselves to `target.members_active` (alive=True, last_seen_seq = current target len).
5. **Source-side cleanup:**
   - Append system `agent_msg` to source: `"Merged into '<target_title>'"`.
   - Transition source to Ended.
   - If source was the `openConversationId`, clear that pointer.
6. **Target-side cleanup:**
   - Append system `agent_msg` to target: `"Merged with '<source_title>' (N members absorbed, M source messages preserved in source's Ended history)."`
7. **Wake any blocked members of target** with the FIFO talking-stick — peers should know about the new arrivals immediately.

**Permanently lost members** in source: stay in source. Source is Ended; their entries remain in source's `members_history` for visibility but are inaccessible.

**Race avoidance.** Combine acquires both source and target's conversation locks (existing parent design's per-conversation locks) before mutating. This serializes against concurrent combine, leave, spawn-into-existing, and message handlers. The locking order is `(min(source_id, target_id), max(source_id, target_id))` to avoid AB-BA deadlock if two combines run concurrently.

### Launcher script changes (`scripts/spawn-launcher.ps1`)

- Drop the gemini branch.
- Read unified spawn-pending JSON; iterate `$params.agents` (typically 1 for fresh spawns, N for resume, 1 per dormant member for combine_resume). For each agent, branch on `$agent.surface` to pick the launch shell (PowerShell for Windows, WSL bash for `wsl`); branch on `$params.type` to pick `--session-id` (fresh) vs `--resume` (resume / combine_resume).

```powershell
$sessionFlag = if ($params.type -in @("resume", "combine_resume")) { "--resume" } else { "--session-id" }

foreach ($agent in $params.agents) {
    $escapedPath   = $agent.project_path.Replace("'", "''")
    $escapedPrompt = $agent.prompt.Replace("'", "''").Replace('"', '\"')
    $sessionId     = $agent.cli_session_id
    $surface       = $agent.surface

    if ($surface -eq "wsl") {
        # WSL launch: wt new-tab opens a tab running `wsl -e bash -lc "<cmd>"`.
        # project_path is already an absolute Linux path (e.g. /home/john/work/Switchboard).
        # No ~ expansion needed in bash. Single-quote escaping uses '...'\''...'.
        $bashSafePrompt = $agent.prompt -replace "'", "'\\''"
        $bashCmd = "cd '$($agent.project_path -replace "'", "'\\''")' && claude '$bashSafePrompt' $sessionFlag '$sessionId' --dangerously-skip-permissions"
        Start-Process -FilePath "wt" -ArgumentList "new-tab", "--", "wsl", "-e", "bash", "-lc", $bashCmd
    } else {
        # Windows launch: same shape as today.
        $cli = "claude '$escapedPrompt' $sessionFlag '$sessionId' --dangerously-skip-permissions"
        $command = "Set-Location '$escapedPath'; $cli"
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        Start-Process -FilePath "wt" -ArgumentList "new-tab", "--", "powershell.exe", "-EncodedCommand", $encoded
    }
    Start-Sleep -Milliseconds 500
}
```

Single foreach, surface-aware branch, surface-and-type-aware launch command. Three `type` values now flow through this script: `fresh` (`--session-id`, 1 agent), `resume` (`--resume`, N agents), `combine_resume` (`--resume`, 1 dormant agent at a time per combine). The launcher doesn't need to distinguish `resume` from `combine_resume` operationally — both use `--resume` and the same per-agent fields.

### Prompt templates

- **`_FRESH_INSTRUCTION_NEW_CONVERSATION`** (fresh spawn, no `target_conversation_id`): "John is away. All communications go through the switchboard. The plugin's PreToolUse hook injects `cli_session_id` and `cwd` on every switchboard call — you only pass `sender` and the tool's own args. **Pick a unique sender name** for yourself — short, distinct. If John named you in this prompt, use that name. Otherwise pick something like `Claude-Win-A`, `Claude-WSL-B`, or whatever fits the work. If John has asked you to start a collab session, call `open_conversation('<title>')` after your first acknowledgment so other agents can join you. Otherwise proceed with John's task."
- **`_FRESH_INSTRUCTION_JOIN_EXISTING`** (fresh spawn, with `target_conversation_id`): "John is away. You're being spawned into an existing Switchboard conversation titled '<target_title>' with current members [<roster>]. The plugin's PreToolUse hook injects your `cli_session_id` and `cwd` automatically. **Pick a unique sender name** that doesn't collide with the existing roster (if John gave you a name in this prompt, use that). Your first switchboard call should be `message_and_await_agent` with a brief intro of yourself (e.g., 'Hi, I'm <sender>, joining at John's direction. <optional new context>'). Wait for a peer response before proceeding."
- **`_RESUME_INSTRUCTION`** (resume / combine_resume): "You are resuming a prior switchboard session via `claude --resume`. Your CLI context (including your prior sender name and conversation history with your peers) is restored. The conversation has continued from where it left off; you may receive an intro from peers or new context from John. Address any new direction in this prompt, then proceed."
- **`_COMBINE_INTRO_INJECT`** (server-injected via `inject_queue` when a member is moved): "You've been moved into conversation '<target_title>' by a combine operation. Existing members: [<roster>]. To receive an intro from peers already here, call `enter_conversation()` — that queues you in the wait queue without writing a speak event, and the next peer speak will deliver to you. After receiving context, introduce yourself via `message_and_await_agent` and proceed."

## Parent design amendments (applied)

The amendments this design drove into the parent ([`2026-05-19-conversations-collab-redesign-design.md`](2026-05-19-conversations-collab-redesign-design.md)) have been **applied directly in the parent doc**. The parent now reflects the post-amendment design state; this section is a historical changelog summarizing what changed.

**State model collapsed.** Open / Closed / Ended → Active / Ended + global `openConversationId` pointer. `collab: bool` attribute removed; "global constraint: at most one Open" replaced by the pointer; seed-blocks-until-second mechanic retired; non-collab away-flip-off termination trigger retired. Member states gain `alive` / `dormant` / `permanently_lost` flags; SessionEnd marks dormant (not leave).

**Channel-as-cwd routing replaced by channel-as-session_id.** `_channel_to_conversation_id` → `_session_to_conversation_id`. `canonicalize_cwd` is now display-only. `ConversationMember` schema replaces `channel` with `cli_session_id` and adds `cwd` (informational), `surface`, `alive`, `session_lost_permanently`, `session_ended_at`, `session_end_reason`. New `cli_sessions/<session_id>/home_conversation_id` per-session home pointer for the session-fallback rule.

**Session-fallback rule introduced.** Members are never orphaned by leave or force-end. Removed sessions re-bind to their home conversation (away mode on) or unbound terminal output (away mode off). Combine is exempted (moves members instead).

**New / modified MCP tools.** All tools drop the agent-supplied `channel` parameter and gain hook-injected `cli_session_id` and `cwd`. New: `open_conversation(sender, title?)`, `combine_conversations(source_id, target_id)`, `lookup_conversation_ids(cwd?, sender_contains?, title_contains?)`. Modified: `enter_conversation(sender)` — unified "join + listen for intro" with branching behavior; `leave_conversation` loses the "cannot leave while in away mode" guard. `message_and_await_agent` unchanged (message still required; listen-without-speak served by `enter_conversation()`).

**New hooks bundled.** `cli-session-injector-hook.py` (PreToolUse, no matcher, self-filter on `mcp__switchboard__*`, explicit merge into `updatedInput`). `cli-session-end-hook.py` (SessionEnd → `POST /cli-session/end` → dormant-mark).

**New Firebase nodes.** `global_settings/open_conversation_id`, `cli_sessions/<session_id>/home_conversation_id`, `combine_commands/<push_id>/`. `conversations/<id>/state` simplified to `"active" | "ended"`. `conversations/<id>/collab` removed.

**Spawn UI returns.** This design (T-027) brings back the spawn FAB and dialog with new surface picker (Windows/WSL), single-agent-per-spawn semantics, optional add-to-existing-conversation, plus the resume and combine mechanics. The parent's prior "spawn UI removed" stance retires.

**Termination triggers updated.** Last-alive-member-leaves (with no dormant remaining), force-end (with session-fallback for all members), combine source-side, and resume-of-all-dormant are the only paths to Ended.

**T-029 ("Move conversations into a new room") subsumed** by the combine mechanic in this design; not added to the backlog.

See the parent doc for the current authoritative design. This changelog exists for audit; the parent is the source of truth.

## Verification

Three verification scripts under [`scripts/verify/`](../../../scripts/verify/) reproduce the empirical findings that the design depends on:

| Script | Question answered | Result |
|---|---|---|
| [`test1-session-id.ps1`](../../../scripts/verify/test1-session-id.ps1) | Does `claude --session-id <uuid>` honor the flag and use that UUID as the session_id? | **PASS** — session file `<uuid>.jsonl` created in `~/.claude/projects/<cwd-hash>/` |
| [`test2-resume.ps1`](../../../scripts/verify/test2-resume.ps1) | Does `claude --resume <id>` reuse session_id or mint a continuation? | **PASS — id reused** — original `<id>.jsonl` grew in place, no new file |
| [`test3-hook-injection/run-test.ps1`](../../../scripts/verify/test3-hook-injection/run-test.ps1) | Does a PreToolUse hook's `updatedInput` arrive at the MCP server merged with the original tool input? | **PASS with caveats** — works, but `updatedInput` REPLACES (must merge in script); matchers unreliable (use no-matcher + self-filter); Windows backslash escaping breaks paths (use forward slashes) |

Scripts are checked in alongside this design as reproducible evidence. Re-run them on a new Claude Code version to confirm the assumptions hold.

## Testing strategy

In-process integration tests covering:

- **Fresh spawn lifecycle**: single-agent Windows spawn (creates Active, member added on first MCP call), single-agent WSL spawn (same flow on WSL surface), spawn with `target_conversation_id` joining an existing conversation, WSL spawn rejection when `wsl_home_resolved` is unset.
- **Open / enter_conversation flow**: agent A calls `open_conversation(title)` → `openConversationId` set to A's conversation; agent B in another conversation calls `enter_conversation()` → B migrates to A's conversation, B's prior conversation Ends if B was sole alive member; second `open_conversation()` by C replaces the singleton.
- **Combine flow**: source with mixed alive/dormant members, target with alive members; alive members rewire via `_session_to_conversation_id`; dormant members launched via `combine_resume` spawn-pending; source Ends; intro inject lands in `inject_queue/<target>/`; concurrent combine on disjoint pairs serializes correctly per the lock ordering rule.
- **SessionEnd dormant-marking**: orderly `/exit` marks member dormant (`alive=False`); conversation stays Active; resume eligibility check passes for the now-dormant member; `clear`/`compact` reasons additionally set `session_lost_permanently=True` and resume rejects.
- **Resume lifecycle**: eligibility evaluation across (all-dormant Active) and (Ended) source states; prior session_id binding to new conversation; `continued_from` linkage rendered on Page A; sibling-branch resume allowed.
- **Resume eligibility edge cases**: any-member missing `cli_session_id` greys the menu item; any-member `session_lost_permanently` greys; any-member active elsewhere greys; all-alive (no member dormant yet) greys.
- **Hook contract**: a unit-style test of `cli-session-injector-hook.py` reads a fixture hook input, verifies the output JSON merges all original `tool_input` fields with the injected `cli_session_id` and `cwd`; verifies the self-filter (non-switchboard tools produce no output). A separate test of `cli-session-end-hook.py` verifies the POST payload shape.
- **MCP boundary rejection**: a switchboard MCP call missing `cli_session_id` returns the documented error; an `enter_conversation` call with no `openConversationId` AND caller-not-already-bound returns the no-open-conversation error; an `open_conversation` call from a session not in any conversation returns the no-current-conversation error.
- **`enter_conversation()` branches**: caller already in conversation X → queue in X's wait queue without writing a speak event; caller unbound + openConversationId set → join open + queue; caller in X ≠ openConversationId + openConversationId set → migrate X→open + queue (X may end if caller was sole alive member); caller in X + openConversationId is null → just queue (no migration).

Firebase mocked per existing `tests/` patterns.

## Feature & UX preservation audit

### Preserved verbatim

- MRU project picker with per-item delete on the spawn dialog.
- 60-second rate limit on spawns.
- `quser` interactive-session precheck (still relevant — schtasks needs a logged-in user).
- `_user_has_interactive_session()` "no one is logged in" failure path with the same user-facing message.
- `spawn_root` configuration and project-path resolution (including the one-level glob fallback for non-direct-child project names).
- The `spawn_root.resolve().relative_to(...)` security check against path-escape.
- Server-side `_cancel_prior_pending` semantics — adapted to key by `cli_session_id` instead of cwd; called on resume to clear any orphaned futures from the prior member's session_ids.
- The unique spawn-pending file naming pattern (`spawn-pending-<id>.json`) and the launcher's atomic-rename claim mechanism.
- Per-spawn ack message written to the conversation immediately on dispatch (so the conversation row appears on Page A without waiting for the agent to launch).
- The H8/H9/H10 turn-end hook invariants from the parent design — unaffected; turn-end logic looks up agent's conversation via `_session_to_conversation_id[cli_session_id]` instead of via cwd.

### Deliberately changed

- `_handle_spawn` no longer flips `cwd_override = True`. Per-cwd overrides retire (parent design).
- `_maybe_handle_spawn_collision` retires. The spawn-collision Firebase channel and the Android `SpawnCollisionDialog.kt` retire with it.
- `_handle_collab_spawn` retires entirely — spawn is always single-agent. Multi-agent conversations are composed via spawn-into-existing and combine.
- Prompt templates rewrite per "Prompt templates" above. The old `_BASE_INSTRUCTION` and `_COLLAB_INSTRUCTION` constants go away.
- `_parse_spawn_flags` retires — replaced by structured `spawn_commands` records. The `--claude --gemini --collab` flag parsing and the `--agents=N` / `--relay` rejection branches go away.
- `_get_backend_name` retires (always Claude; senders are agent-supplied).
- Spawn sidecar `collab-sessions.json` retires — parent design's in-memory Conversation registry replaces it; the "session was lost" notice path is subsumed by the parent design's accepted "server restart loses in-flight conversations" stance.
- The "global constraint: at most one Open conversation" rule retires. Replaced by the `openConversationId` pointer + the agent-driven `open_conversation()` tool, which can be re-pointed at will.
- The "Closed conversations always have one member; first xxx_human auto-creates Closed" rule retires. Replaced by "Active conversations have N members (N ≥ 1); first xxx_human from an unbound session auto-creates Active."
- `enter_conversation` semantics change: no `conversation_id` arg, no `title` arg, no `sender` arg (sender is implicit from agent). It only joins the singleton open. Promotion of "make my conversation the open one" is the new `open_conversation` tool.
- `enter_conversation()` semantics expand to cover the "join + listen for intro" role. Newly-arrived members (post-combine or post-spawn-into-existing) call it to receive context from peers without speaking first. `message_and_await_agent` is unchanged (still requires non-empty message).
- SessionEnd no longer auto-leaves members; marks them dormant instead. Conversations are durable across session exits.

### Newly added

- `_session_to_conversation_id` map (parent design amendment).
- `openConversationId: conversation_id | None` server-side pointer; persisted at `global_settings/open_conversation_id`.
- `_session_home_conversation_id: dict[cli_session_id, conversation_id]` server-side map, persisted at `cli_sessions/<session_id>/home_conversation_id`. Each session's home is set on first switchboard contact (typically the spawn-pre-bound conversation, or the auto-created first-xxx_human Active conversation). Used by the session-fallback rule on leave/force-end.
- Auto-enable of `global_away_mode` on spawn dispatch when currently False; phone surfaces a confirmation toast.
- `cli_session_id`, `cwd`, `surface`, `alive`, `session_lost_permanently`, `session_ended_at`, `session_end_reason` fields on every Conversation member.
- `continued_from` field on Conversation, optional, references prior conversation_id when resume-spawned.
- `spawn_commands` Firebase node accepts `type: "fresh" | "resume"` records with the surface + project + target_conversation_id schema in this design.
- `combine_commands` Firebase node — phone-side trigger for the combine flow.
- New MCP tools: `open_conversation`, `combine_conversations`, `lookup_conversation_ids`.
- WSL launch path in the launcher script (`wt new-tab -- wsl -e bash -lc "..."`).
- Config knob `wsl_spawn_root_segment` (default: `work`, joined onto the WSL home dir at startup-resolved time → e.g. `/home/john/work`). Existing `spawn_root` renamed `windows_spawn_root` for symmetry.
- Server startup task: one-shot `wsl.exe -e bash -lc 'echo $HOME'` invocation to cache `wsl_home_resolved`. Failure is non-fatal at startup (Windows-only spawns continue to work); a later WSL-targeted spawn triggers the documented rejection.
- `hooks/cli-session-injector-hook.py` plugin script (PreToolUse: injects `cli_session_id` + `cwd` into every switchboard MCP call).
- `hooks/cli-session-end-hook.py` plugin script (SessionEnd: marks the member dormant).
- `POST /cli-session/end` server endpoint (consumed by the SessionEnd hook).
- Page A long-press "Resume" menu item; long-press "Combine into…" menu item; "End conversation" force-end action (parent design has this concept; new force-end-with-dormant-members behavior).
- `SpawnSessionDialog.kt` rewritten for single agent + surface picker + optional add-to-existing.
- `SpawnResumeDialog.kt` (pre-fills from a prior conversation, accepts only optional new prompt).
- `CombineDialog.kt` (target picker after long-pressing source).
- `inject_queue` payload schema for combine-intro prompts.
- Page A "stale session" warning indicator (⚠️ badge) on rows whose youngest member has `session_ended_at > 25 days ago` — heads-up that Claude Code's 30-day `cleanupPeriodDays` window is approaching and resume may fail soon.

### Known regressions, accepted

- Gemini agents lose Switchboard access until Gemini's hook system gains equivalent capability.
- Older Claude installs without the plugin's hook can't call Switchboard once the parent design ships.
- Spawn from terminal while at desk: spawned agents' `ask_human` get at-desk-redirected (per parent design's global-only away mode). Not an actual workflow regression — spawn-from-phone, the primary use case, implies away mode is on.
- SessionEnd `clear` / `compact` reasons mark the underlying CLI session as permanently lost — affected members cannot be revived via resume or combine. Force-end is the only cleanup, but the underlying CLI session is gone regardless.
- **30-day session-file aging.** Per Claude Code's `cleanupPeriodDays` default, session `.jsonl` files are pruned after 30 days. A conversation older than that (or whose youngest dormant member crossed the threshold) is effectively non-resumable even though Switchboard's metadata is intact. Failure surfaces only at launch time (`claude --resume <missing>` errors and the wt tab closes; the new conversation row shows the system "Resuming…" message but no agent activity). Page A's 25-day warning indicator gives John a 5-day heads-up; operators can extend the window by bumping `cleanupPeriodDays` in their Claude Code settings.

## Open questions / future work

- **Page B chained-conversation rendering** — should the new conversation visually link to its predecessor (header chip, scroll-into-predecessor affordance)? v1 leaves the chain as separate Page A rows; UX clustering is a follow-on.
- **FAB-path resume** — should an FAB-path "find most recent resumable in cwd" affordance return? v1 drops it (long-press only); add if friction surfaces.
- **Multi-prior-sessions picker** — for a cwd with many Ended conversations, a richer picker would let John pick any older session. v1 says "long-press the specific row." Defer to friction.
- **Per-spawn rate limiting and resume/combine-specific rate limiting** — today's 60s applies globally. Could partition by operation type. Defer.
- **Cross-host resume / combine** — out of scope per parent design's T-025 deferral; the conversation_id + cli_session_id model is naturally cross-host compatible if Firebase transport ships later.
- **Resume of a forked/branched chain** — v1 allows sibling-branch resume; if abuse patterns emerge, add a tightening rule.
- **Visual indicator of `openConversationId`** on Page A — which row is currently the open one? Some visual treatment (badge, accent border) is probably needed for the prompt mechanic to be discoverable. v1 sketches the data model; UX treatment is left to plan-stage refinement.
- **Server-side detection of stale-alive members** (parent design T-003 GC overlap) — if a member's `cli_session_id` is bound but the underlying process is dead (no SessionEnd fired), we have no way to revive that member. A heartbeat / GC sweep can detect and mark such members dormant after a timeout. Defer to T-003.
- **`close_conversation` tool** — explicit "this conversation no longer wants new joiners" tool that clears `openConversationId`. v1 relies on the implicit-clear behaviors (replacement by another `open_conversation`, source-ends via combine, conversation Ends). Add if friction surfaces.
- **Cross-conversation move for a single member** — agent-driven "I want to move FROM my current conversation TO conversation X" without full conversation merge. v1 has `enter_conversation()` (move to the open one) but no general "move to arbitrary conversation" affordance for agents. Server can do this via spawn-into-existing for a new agent, but agent-driven self-migration to a non-open target isn't a tool. Defer.

## Backlog additions

No new backlog items from this design. The "Move existing conversations into a new room" item (previously sketched as T-029) is fully realized by the combine mechanic in this design and is NOT added to the backlog.

## Supersedes / relates to

- **Pairs with:** [`2026-05-19-conversations-collab-redesign-design.md`](2026-05-19-conversations-collab-redesign-design.md) — the parent conversations redesign. Both designs ship together; this one drives substantial amendments to the parent (state model collapse to Active/Ended + openConversationId pointer; session_id-as-channel; member dormant-marking).
- **Supersedes:** the "Spawn (deferred)" section of the parent design.
- **Folds in:** T-016 ("Spawn dialog: resume last session") — the resume mechanic is fully designed here.
- **Resolves architecturally:** T-026 (Windows ↔ WSL canonicalization gap, remaining sub-cases) — canonicalization is no longer load-bearing for routing.
- **Simplifies:** T-025 (cross-workstation A2A) — the routing-key collision problem is solved; Firebase transport is the remaining work if T-025 is ever picked up.
- **Subsumes:** the previously-sketched T-029 (Move existing conversations into a new room) — replaced by the combine mechanic.
