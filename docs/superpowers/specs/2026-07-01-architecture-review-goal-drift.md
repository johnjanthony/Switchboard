# Architecture Review: Goal Drift Findings — 2026-07-01

**Date:** 2026-07-01
**Status:** Review findings (no implementation)
**Reviewed tree:** `dc72478` (GitHub main; one commit behind local work)
**Scope:** Overall architecture only. Security findings, bug hunting, and robustness defects are **explicitly out of scope** — a pending local commit addresses the security/high-priority backlog and this review deliberately steers clear of that territory. This document asks one question: where has the implementation veered from the project's stated goals, and which of those veers deserve a course correction versus a docs correction?
**Author:** Claude (external review at John's request), from a full read of the repo, its dated specs, `CLAUDE.md`, and `README.md`.

## Method

The project's goals are unusually well documented: the dated specs under `docs/superpowers/specs/` record each pivot, with explicit supersession notices (2026-04-18 → 2026-04-19) and implementation-delta sections (2026-05-19). "Drift" here means a place where the *current* code or product shape contradicts a *still-asserted* principle — not a documented, deliberate pivot. The Telegram → Firebase/Android pivot, the channel → Conversation reframe, and the global-away-mode collapse are all documented decisions and are not treated as drift.

## Findings

### D1. "Locally-hosted" is no longer true in the sense the founding specs meant it

The README's first line calls Switchboard "a locally-hosted MCP server," and the founding spec's non-goals said plainly: *cloud hosting (localhost only)*. Today Firebase is a hard startup dependency — the server exits with a ConfigError if RTDB credentials are absent, and there is no Android-disabled run mode (README states this explicitly). Every conversation message, member roster, away-mode flag, and (per the 2026-06-25 design) rings/quota/status snapshot transits and persists in Google's cloud. FCM delivers the pushes.

The compute is local; the *state and availability* are not. This happened incrementally — Firebase arrived as the Android transport, then became the persistence layer (hydration), then the command bus (dispatch loops), then the telemetry fan-out — and no spec ever revisited the "localhost only" principle it was eroding. There may be no desire to reverse it (the four-surface product is clearly worth it), but the drift should be resolved *one way or the other*:

- **Docs correction (cheap):** reposition honestly — "local gateway, cloud-synchronized state" — and record the principle change in a dated spec so future designs argue from the real constraint.
- **Architecture correction (optional, larger):** a degraded local-only mode (in-memory conversations, Operator-only surface, no push). Worth a brainstorm only if there's a real scenario — Firebase outage, credential rotation gaps, or working offline — that has actually bitten. Note the `ConversationStore` protocol and the `MessengerBackend` trait split (2026-05-01) mean the seam for a second backend already exists; the drift is a product decision, not a missing abstraction.

### D2. "Switchboard exists specifically for away mode" — the code has outgrown the axiom

`CLAUDE.md` states the axiom twice. The implementation increasingly votes against it:

- Rings/quota/status fan-out is deliberately **always-on** — the 2026-06-25 spec explicitly reversed the earlier "rings only in away mode" decision.
- `notify_human` and `send_document_human` deliver at-desk (the error string is routing guidance, not a failure).
- The Operator cockpit, Watchtower widget, agent-status indicators, and pending-question badges are ambient supervision surfaces with no away-mode dependency at all.

Switchboard has become a **mission-control hub for a multi-agent workstation** — sessions, conversations, telemetry, and command dispatch — of which away-mode question routing is the founding feature, not the definition. This matters because design arguments are still being made from the away-mode axiom (e.g., the SKILL.md protocol burden, the at-desk redirect semantics) while the system's actual center of gravity has moved. Recommendation: update `CLAUDE.md`'s framing, and let the session-lifecycle spec (companion doc, same date) formalize the hub identity.

### D3. The agent-facing protocol has drifted from "agents only pass sender and tool arguments"

The README promises simplicity: session-id routing is injected by hook; *"agents only pass `sender` and tool arguments."* That holds for `ask_human`/`notify_human`. It does not hold for convening. The 2026-05-19 redesign itself diagnosed the prior collab protocol's fragility ("the H8/H9/H10 turn-end hook invariants exist solely to compensate") — and then the open/enter/lobby-hold mechanics reintroduced the same *class* of complexity one level up:

- **Mode-dependent tool behavior.** `open_conversation` is non-blocking for a bound caller and blocking for an unbound one. `message_and_await_agent` in a sole-alive conversation blocks if the conversation is the open marker but returns `__CONVERSATION_EMPTY__` *and mutates membership* if it isn't. The same call does different things based on server state the agent cannot see. LLM callers are exactly the callers who will get this wrong under context pressure; SKILL.md's length is the measure of the burden.
- **Stringly-typed protocol.** `__TIMEOUT__`, `__CONVERSATION_ENDED__ (force-ended)`, `"ok. open_conversation = <id>\nPeer 'X' joined."` — sentinel strings with meaningful suffixes and embedded IDs that agents must parse from prose. Structured returns (JSON with a `status` field) would cut SKILL.md's sentinel documentation substantially and survive model changes better.
- **The singleton open marker.** At most one joinable conversation globally is a coordination bottleneck (two concurrent convenings are impossible) and is the direct cause of the mode-dependent branching above.

This is the drift behind the "how do I simplify bringing agents together" question, and it gets a full design in the companion spec `2026-07-01-convening-simplification-design.md`. The short version: convening should be primarily a *human/server* act (pick sessions from a roster), not an agent-executed rendezvous protocol.

### D4. Dual identity: sender-keyed rosters over session-keyed routing

`ConversationMember.cli_session_id` is documented as "primary routing key," yet `members_active` is keyed by `sender` (a display name), pendings are keyed by `(conversation_id, sender)` using the *raw* agent-supplied sender, and `PendingRequest` carries `cli_session_id` precisely because the raw sender "can differ from the member's disambiguated sender" (registry.py's own comment). Sender-collision disambiguation (`Claude Win` → `Claude Win 2`) then mutates the identity key at join time.

This works, but it is two identity systems where the original design implies one: **identity = `cli_session_id`; `sender` = display attribute.** Keying rosters and pendings by session id would delete the raw-vs-disambiguated wrinkle, simplify session-end cleanup (already matching "by identity rather than by name"), and is a precondition for sessions becoming first-class entities in the lifecycle registry. Suggested as an internal refactor to fold into the session-registry work rather than a standalone effort.

### D5. Restart amnesia is drifting from deferred debt to standing contradiction

Pending `ask_human` futures dying on restart was an explicit founding non-goal, reaffirmed in 2026-05-19 as T-001 with the operational rule "never restart during collab." Two things have changed since: (a) the server now runs as an always-on NSSM service whose restarts are not always chosen (`restart-service.ps1` gates on pytest, but crashes and host reboots don't ask), and (b) the system's ambition has grown to ambient, always-on supervision (D2) where "the hub forgot every in-flight question" is a more jarring failure than it was for a desk-side tool. No new finding here beyond: the *priority* of T-001 should be re-argued against the hub identity, not the 2026-04 tool identity. The `request_id` correlation and Firebase-persisted question slots suggest pending-question reconstruction at hydration is tractable if John chooses to promote it.

### D6. The service-identity discrepancy shapes the architecture and is still unresolved

The 2026-06-25 spec *verified* that the service runs as LocalSystem despite `install-service.ps1` intending the interactive user, and correctly derived the "Watchtower is the sole sensor" reframe from it. That was the right call for rings/quota. But the discrepancy itself remains parked, and it is load-bearing: it is *the* reason the server cannot read transcripts, cannot reach WSL, and must receive session telemetry by push. Any future feature that assumes the server can see John's files will silently re-collide with it. Recommendation: record it as a first-class architectural constraint (a short ADR-style note, or a decided line in `CLAUDE.md`), including the decision *not* to fix it if that's the decision — so it stops being a rediscovered surprise.

### Non-drift observations (documented pivots, working as intended)

For completeness, three things that might look like drift but are documented decisions: the Telegram removal (2026-04-19's chosen surface, later superseded by the Android app — the founding specs carry supersession notices); the global-away-mode collapse (the per-cwd model was built, then explicitly retired in the 2026-05-19 non-goals — churn, but recorded churn); and spawn's growth from "launch a fresh session" to fresh/resume/combine (T-027 got its own brainstorm and spec, as the process intends). The `--resume`-based recovery idea from the earliest design survives inside spawn (resume dormant members) rather than as ask-recovery — a reasonable landing spot.

## Summary of recommendations

1. **Docs corrections (do these regardless):** reframe `CLAUDE.md`/README around the hub identity (D2); record the "cloud-synchronized state" principle change (D1); write the LocalSystem constraint down as decided (D6).
2. **Adopt the session-lifecycle registry** (companion spec) — it resolves D4's identity question, gives D2's hub identity its missing primitive, and is the enabler for convening simplification.
3. **Simplify convening** (companion spec) — human-driven convening from the session roster as the primary path; structured tool returns; retire mode-dependent branching and the singleton open marker on the agent path (D3).
4. **Re-triage T-001** (pending-question survival) against the always-on hub identity (D5). No design proposed here; just a priority re-argument.
