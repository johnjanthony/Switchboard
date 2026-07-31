# Background-Orchestrator Delegation Runbook

How the design-tier session spawns and supervises a background Opus orchestrator that executes an implementation plan via subagent-driven development (SDD). First used 2026-07-28 (comprehensive-spec truth rewrite, 15 sub-dispatches); refined 2026-07-30 (T-250 spawn model/effort). This replaces the older "generate a kickoff prompt for John to paste into a fresh Opus session" handoff — the design agent spawns the orchestrator itself.

## The three tiers

| Tier | Who | Runs where | Responsibilities |
| ---- | --- | ---------- | ---------------- |
| Design | Fable session (the one John talks to) | Interactive session | Brainstorm → spec → plan → kickoff; spawn + supervise the orchestrator; review each task report; relay to John (terminal at desk, `notify_human`/`ask_human` in away mode); independently re-verify gate claims before repeating them |
| Orchestrator | Opus, spawned via the Agent tool | Background agent inside the design session | Read kickoff + plan + spec; run SDD task-by-task; dispatch implementer/reviewer children; own the plan file's checkboxes; report at task boundaries |
| Implementers | Fresh subagents per task (model per plan/SDD) | Children of the orchestrator | One plan task each, per the SDD skill |

## When to use it

An approved spec + written plan exist, the work is multi-task and hours-long, and John wants to step away while it runs. For small fixes (a handful of tool calls) skip all of this and just do the work — delegation multiplies cost.

## The procedure (design tier)

1. **Prereqs:** spec approved, plan written and self-reviewed, both saved under `docs/superpowers/`.
2. **Write the kickoff file** beside the plan: `docs/superpowers/plans/<date>-<topic>-kickoff.md`. It is the orchestrator's complete brief. Must contain: reading list (plan, spec, repo CLAUDE.md); the SDD skill instruction; the session-mechanics block (below); the hard-rules block (no git writes, `-SkipTests` restarts, basetemp, tabs/endings/no-em-dashes, no version bumps); scope boundaries (what is a handoff vs. an action); and the first-turn readiness gate. Copy the T-250 kickoff (`2026-07-30-spawn-model-effort-kickoff.md`) as the template.
3. **Spawn the orchestrator:**
   - Agent tool, `subagent_type: "general-purpose"`, `model: "opus"`, `run_in_background: true` (the default), a stable `name` (e.g. `t250-orchestrator`) so SendMessage can address it.
   - Prompt: "Read `<kickoff path>` and follow it exactly. It is your complete brief." (The brief lives in the file, not the prompt, so it is reviewable and reusable.)
4. **Gate the first turn.** The kickoff tells the orchestrator to end its first turn with a readiness confirmation before dispatching anything. Check its reading is right, then SendMessage the green light.
5. **Run the supervision loop** (see below) until the plan is done.
6. **Close out:** independently re-verify the headline claims (run the full suite yourself or spot-check its VERIFY output, byte-check a few files), then report to John with the live-check handoff list and the files-touched summary. John reviews and commits.

## Session mechanics (why the kickoff says what it says)

- **Notification routing:** a background agent's Agent-tool children complete-notify the PARENT session (the design tier), not the orchestrator. Verified 2026-07-28. Hence the two dispatch modes:
  - *Synchronous children* (`run_in_background: false`): the child's result returns inline to the orchestrator. Right choice for SDD's sequential per-task dispatches. No relay needed.
  - *Background children*: only for genuinely parallel waves. The orchestrator must end its turn right after dispatching; the design tier collects the children's results from its own notifications and SendMessages them back to resume it.
  - *Resuming a completed child is ALWAYS background* (verified 2026-07-30): `SendMessage` to a finished subagent resumes it from its transcript in the background - there is no synchronous form. So any fix-round sent to an existing implementer routes its result through the design tier, even when the orchestrator otherwise runs synchronous children. The orchestrator must end its turn after such a resume and wait to be fed the result.
- **Turn-boundary supervision:** the orchestrator ends its turn after every completed task (or blocker). Each turn-end raises a task notification in the design session carrying its report. Resume with `SendMessage({to: <name>, ...})` — the orchestrator keeps its full context across resumes. A new Agent call would start a fresh context; never do that mid-plan.
- **Rule 0 adaptation:** the orchestrator cannot reach John (no Switchboard tools, no terminal). Its kickoff must convert "stop and ask John" into "stop and end your turn with the failure report". The design tier decides: answer from the spec/plan, relay to John, or abort.
- **Away mode:** orchestrator progress is invisible to John unless the design tier relays it. While away mode is on, the design tier forwards each task report via `notify_human` and routes questions through `ask_human`. The design tier does NOT toggle away mode itself (John's control).
- **Do not park the loop behind John's replies** (John's direction, 2026-07-30). Ending every turn on a blocking `ask_human` stalls the relay: task notifications queue behind John's tap, and the orchestrator idles until he answers. Two mechanisms compose to fix it:
  - *Wait by blocking, not by stopping.* The away-mode Stop hook fires on ANY turn-end that is not itself parked on `ask_human` - a pending question held by another agent does NOT satisfy it (verified 2026-07-30). So while the orchestrator is running, the design tier holds its own turn open with `TaskOutput(orchestrator_task_id, block=true, timeout=600000)` and simply re-calls it when the timeout slice expires. The orchestrator's turn-end returns inline; no Stop event ever fires; nothing queues behind John.
  - *John's interject channel.* A tiny background "parking attendant" agent (haiku) holds a standing `ask_human` ("reply anytime to interject; work does not wait on this") and returns John's reply verbatim as its result. The reply is delivered alongside the design tier's next tool result (worst case one TaskOutput slice later). Re-spawn it after each reply or timeout. Status still flows one-way via `notify_human`; escalations that genuinely need John use a direct blocking `ask_human`.
  - When there is genuinely nothing to block on (execution finished, John away), end the turn on a direct `ask_human` as the hook demands.
- **Restarts:** a plan task that restarts the switchboard service severs the design session's MCP tools momentarily (they self-heal; away mode persists). The orchestrator holds no MCP connections and is unaffected.

## Trust rules (from John's protocol)

- Subagent reports are hearsay: the design tier re-verifies gate claims (test counts, "all green", byte-checks) before repeating them to John — run the command again or read the artifact.
- The design tier never lets the orchestrator commit; ledger hash fields stay `(pending)` until John commits.
- If the orchestrator's report and the plan/spec contradict, that is a Rule-0 stop at the design tier: surface to John, don't pick silently.

## Generation handoff (long plans)

An orchestrator's context grows by roughly 40-50k tokens per task (T-250 observed ~313k after 7 tasks). Past ~300k, every turn re-reads that context at full cost and an involuntary harness compaction — which loses detail uncontrollably, mid-task — becomes the dominant risk. The fix is a deliberate handoff to a fresh orchestrator (gen-2) at a clean task boundary. First exercised 2026-07-30 (T-250, gen-1 → gen-2 after Task 7 of 10).

1. **Pick the boundary.** Only ever between tasks, right after the design tier has accepted a task report. Never mid-task, never with a fix round open.
2. **Gen-1's final act** (sent as its last resume message): audit the ledger for self-containedness — a fresh reader of kickoff + plan + spec + ledger alone must be able to continue correctly — then write a handoff addendum (`handoff-gen2.md` beside the ledger) carrying only what lives in its head: per-task file touches, repo quirks, conventions it established, dispatch patterns that worked, and the exact next action. Explicitly: audit by RE-READING the ledger, not from memory of writing it (T-250's audit found four real gaps that way, including a stale "NEXT" pointer that would have misdirected gen-2 to a completed task).
3. **Design tier verifies both artifacts** before spawning: read the addendum in full, read the ledger's authoritative-state section, spot-check the binding items (rulings, final-review conditions, open lists), byte-check endings.
4. **Spawn gen-2** with a distinct name (`<topic>-orchestrator-2`), same Agent-tool parameters, and a prompt giving the reading list in order (kickoff, ledger, addendum, plan, spec, repo CLAUDE.md), a note that the ledger supersedes the kickoff's "start at Task 1", the inherited binding items to confirm, and the same first-turn readiness gate as a fresh spawn.
5. **Gate readiness, then dispatch.** Gen-2's first turn must restate current state, the binding items it located, and its intended first dispatch. Check it against your own reading of the ledger before the green light.

What makes this cheap is ledger discipline DURING the run: if every ruling, condition, and queue item was forced into the ledger at the moment it happened, the handoff is an audit plus a short brief, not a reconstruction. Gen-1 stays addressable by name after handoff (a completed agent resumes on SendMessage) if a question only it can answer surfaces, but treat that as a last resort — its answer is hearsay like any subagent's.

## Failure modes seen so far

| Symptom | Cause | Handling |
| ------- | ----- | -------- |
| Orchestrator idle, no report | It dispatched background children and ended its turn | Collect child results from your notifications; SendMessage them back with the next-step instruction |
| Child notification arrives but orchestrator still "running" | Normal: children notify the parent while the orchestrator continues | Hold the result; the orchestrator's own turn-end report supersedes it |
| Design session MCP tools vanish mid-run | A task restarted the service | Wait for auto-reconnect (~31s); away mode and the orchestrator are unaffected |
| Orchestrator asks a question meant for John | Kickoff framing gap | Answer from spec/plan if the answer is already decided there; otherwise relay via `ask_human` |

## Record of uses

- 2026-07-28 — comprehensive-spec truth rewrite (opus orchestrator, 15 sub-dispatches in verifier/rewriter/auditor waves, background children + relay loop). Outcome: committed `1ed564d`.
- 2026-07-30 — T-250 spawn model/effort (opus orchestrator, SDD over a 10-task plan, synchronous children + per-task turn-end reports). Outcome: recorded in the T-250 ledger row when closed.
