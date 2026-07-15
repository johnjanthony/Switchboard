# Antigravity CLI as a First-Class Switchboard Agent — Design

**Date:** 2026-07-14
**Status:** Approved by John (scope bar, sequencing, identity approach, packaging, and per-capability dispositions decided in AskUserQuestion rounds + chat approvals, 2026-07-14)
**Relationship to roadmap v2:** standalone spec; John schedules execution (does not edit `2026-07-12-switchboard-roadmap-v2.md`). Sequencing decided 2026-07-15: executes AFTER roadmap chunk 2 (away-mode integrity) lands; the implementation plan is reconciled against chunk 2's plan (`_hook_common` helpers, cwd-free turn-end hook, plugin 1.5.0 -> 1.5.1) and its prerequisite checks enforce the ordering.
**Grounding quality:** all load-bearing mechanics were live-verified against agy 1.1.2 on this workstation on 2026-07-14 (probe workspace `C:\Work\agy-probe\`); community/blog claims that contradicted the live probes were discarded

## 1. Goal and priority ladder

Add Google's Antigravity CLI (`agy`, the Gemini CLI successor) as a first-class agent in Switchboard. John's priorities:

1. **Primary (this spec's committed scope, "phase 1"):** Claude and Antigravity CLIs interact with each other and with John through the switchboard tools — conversations, ask/notify/document, collab — plus roster presence, away-mode enforcement, and best-effort lifecycle tracking.
2. **Deferred (phase 2, sketched §10):** spawning Antigravity agents from the phone.
3. **Lower (phase 3, sketched §10):** spawning Antigravity WSL agents.
4. **Stretch (parked, sketched §10):** Watchtower tracking of Antigravity context/usage.

**Parity bar (decided):** all 9 MCP tools route correctly; the session appears on the phone roster/sessions board; lifecycle state is implemented to the extent agy's hook surface allows, with degradations documented honestly (§8). Away-mode turn-end enforcement is **required** scope. `in_tool` tracking is **in** phase 1; `blocked_on_approval` stays inert (§8).

## 2. Verified platform facts (agy 1.1.2, live-probed 2026-07-14)

These are the facts the design stands on. Anything not in this list should be re-verified before being relied on.

- **Binary/config layout:** `agy.exe` under `%LOCALAPPDATA%\agy\bin\`; config home is `~/.gemini/` with CLI-specifics under `~/.gemini/antigravity-cli/` (settings, `brain/<conversationId>/` transcript dirs, `mcp/<server>/` cached tool schemas) and shared config under `~/.gemini/config/` (global `mcp_config.json`, `hooks.json`, `plugins/`, `projects/`).
- **agy does NOT read the Gemini-era `~/.gemini/settings.json` `mcpServers` or `hooks` blocks.** Verified: with switchboard present in `settings.json`, agy reported zero MCP servers. The Gemini `AfterAgent` hook block in there is dead config for agy.
- **MCP:** `mcp_config.json` (workspace `<ws>/.agents/` — verified; global `~/.gemini/config/` — file exists, load unverified, plan item V2) with `{"mcpServers": {"switchboard": {"serverUrl": "http://localhost:9876/mcp"}}}`. Verified end-to-end against the live Switchboard service: tool discovery, call, and the server's `cli_session_id required` rejection all round-tripped. Remote entries accept a `headers` map (Bearer auth for WSL later) per config format; unprobed.
- **Hooks:** `hooks.json` at both `~/.gemini/config/` and `<ws>/.agents/` (both verified loading; entries merge across files). Top-level keys are **named hook blocks**. Events: `PreToolUse`, `PostToolUse` (grouped structure: `[{"matcher": <tool-name regex>, "hooks": [{"type": "command", "command": ..., "timeout": <s>}]}]`), `PreInvocation`, `PostInvocation`, `Stop` (flat structure: `[{"type": "command", "command": ..., "timeout": <s>}]`). Wrong structure = handlers silently dropped (log line `N total handlers` in `~/.gemini/antigravity-cli/cli.log` is the tell). Commands run via `cmd /c` on Windows (plain paths work; no PowerShell `&` needed, unlike Gemini CLI); hook cwd is the directory containing `hooks.json`, so scripts must use absolute paths and take the agent's location from the payload.
- **Hook stdin payload (camelCase protojson):** common fields `conversationId`, `workspacePaths[]`, `transcriptPath`, `artifactDirectoryPath`, `modelName`. `PreToolUse` adds `toolCall: {name, args}` and `stepIdx`. MCP calls surface as `toolCall.name == "call_mcp_tool"` with `args: {ServerName, ToolName, Arguments}`. `Stop` adds `terminationReason`, `error`, `fullyIdle`, `executionNum`.
- **Hook output contracts** (from the binary's embedded docs, cross-checked with live behavior): `PreToolUse` → `{"decision": "allow"|"deny"|"ask"|"force_ask", "reason"?, "permissionOverrides"?}`; `PostToolUse` → `{}`; `PreInvocation` → `{"injectSteps": [{"ephemeralMessage": ...}|{"userMessage": ...}|{"toolCall": ...}]}` (ephemeral inject live-verified); `PostInvocation` → `{"injectSteps"?, "terminationBehavior": "force_continue"|"terminate"|""}`; `Stop` → `{"decision": "continue", "reason": ...}` blocks the stop and re-enters the loop with the reason injected as a system message (contract documented; live continue-loop is plan item V1).
- **No argument rewriting:** hooks cannot modify tool-call args. The result protobuf reserves an `overwrite` field for `PreToolUse`, explicitly "not yet implemented" — when Google ships it, Claude's silent-injector pattern ports directly (§11).
- **No SessionStart/SessionEnd hook events.** Birth/death must come from first-sighting self-heal and silence sweeps.
- **`conversationId` is the durable session identity:** it names the `brain/` transcript dir, survives process restarts, and is what `agy --conversation <id>` resumes. Hook payloads carry it on every event.
- **Identity flow proven live:** with a `PreInvocation` ephemeral message teaching the model to pass `cli_session_id`/`cwd` inside the MCP `Arguments`, the model complied on its first real attempt; the live server routed the call and returned a normal envelope. Without teaching, the model cannot comply (a deny-with-instructions alone sent it flailing for 17 invocations — root cause: the current tool docstrings say the args are "injected by the PreToolUse hook", which teaches Gemini models NOT to pass them; fixed by §6.1).
- **Headless mode:** `agy -p "<prompt>"` works (used for all probes) but cannot mint or report a conversation id — constrains phase-2 spawn to interactive tabs (§10).
- **Instruction files:** global `~/.gemini/GEMINI.md` auto-loads (chezmoi-managed, see §7). agy skills exist but are model-triggered — too weak for protocol-critical text.

## 3. Decisions

- **D1 — Identity approach: explicit args, hook-taught + hook-enforced.** `cli_session_id` = agy `conversationId`; the model passes it (and `cwd` = `workspacePaths[0]`) explicitly inside every switchboard tool call's `Arguments`. A `PreInvocation` ephemeral message teaches it every invocation; a `PreToolUse` corrector denies non-compliant switchboard calls with the correct values in the reason. Rejected: server-side `Mcp-Session-Id` transport mapping (identity churn on reconnect, nothing resumable, breaks the `cli_session_id == transcript stem == resume id` invariant, real server surgery vs near-zero delta).
- **D2 — No new routing model.** The server keeps `cli_session_id` as the sole routing key. An agy session is an ordinary session whose id happens to be a Gemini conversation UUID. `cwd` stays display-only.
- **D3 — Away-mode enforcement is required scope**, delivered via agy's `Stop` hook continue-block, reusing `turn-end-hook-away-mode.py` with a new `--cli antigravity` mode.
- **D4 — Packaging is dotfiles-first; no installer script.** All agy-side wiring (hooks.json, mcp_config.json, GEMINI.md section, stale-hook cleanup) is delivered by John's chezmoi dotfiles repo; hook/behavior scripts stay in the Switchboard repo, referenced by absolute path. Rationale: `~/.gemini/settings.json` and `~/.gemini/GEMINI.md` are already chezmoi-managed (direct target writes would be overwritten and violate the edit-the-source rule), and the dotfiles already template the old Gemini away-mode hook — the precedent exists.
- **D5 — Resume affordance: guard-only in phase 1.** Resume/auto-resume paths skip `cli == "antigravity"` sessions with a phone-visible manual-resume notice (`agy --conversation <id>` in `<cwd>`). The launcher's agy branch lands with spawn in phase 2.
- **D6 — `in_tool` in phase 1; `blocked_on_approval` inert.** `in_tool` falls out of the existing server derivation once agy POSTs `PreToolUse`/`PostToolUse` wire events. `blocked_on_approval` is `in_tool AND title_state == "star"` where `title_state` is Watchtower-ring-fed; for agy it stays `False` harmlessly until the Watchtower stretch. agy has no permission-prompt hook event to substitute.
- **D7 — Translate at the edge.** agy hook scripts POST the server's existing `/agent_status` wire vocabulary (event names + display states); the server state machine is untouched except learning an optional `cli` field. Rationale: the wire vocabulary describes lifecycle semantics agy shares; the alternative (server learns agy's event names) spreads CLI awareness into the registry for no behavioral gain.
- **D8 — Single Stop entry.** The away-mode check and the idle status POST share one Stop hook script invocation (the `--cli antigravity` mode of the turn-end script does both). Rationale: agy's merge semantics for multiple Stop handlers returning conflicting decisions are unverified; one handler sidesteps the question. (Claude keeps its two separate Stop handlers — this asymmetry is documented here deliberately.)
- **D9 — Standalone spec; John schedules.** Not a roadmap v2 chunk.

## 4. Architecture

An Antigravity session interacts with Switchboard through exactly the same server surface as Claude: the MCP tools plus the `/agent_status` and `/away-mode` HTTP routes. All adaptation lives at the agy edge.

| Piece | Location | Job |
| :--- | :--- | :--- |
| `scripts/agy-identity-hook.py` (new) | Switchboard repo | PreInvocation / PreToolUse / PostToolUse behaviors (§5) selected by `--event` arg |
| `scripts/turn-end-hook-away-mode.py` (extended) | Switchboard repo | new `--cli antigravity` mode: away-mode Stop block + idle status POST (§6) |
| `dot_gemini/config/hooks.json.tmpl` (new) | dotfiles repo | wires the five hook entries to the repo scripts (§7) |
| `dot_gemini/config/mcp_config.json` (new) | dotfiles repo | `switchboard` → `serverUrl: http://localhost:9876/mcp` |
| `dot_gemini/GEMINI.md` switchboard section (new content) | dotfiles repo | always-loaded protocol instructions (§7.2) |
| `dot_gemini/settings.json.tmpl` (edit) | dotfiles repo | delete the dead Gemini `AfterAgent` hook block |
| Server deltas (§6) | Switchboard repo | docstrings, error text, `/agent_status` `cli` field, resume guard |

## 5. Identity and status: `agy-identity-hook.py`

One stdlib-only script, three modes via `--event`; all modes fail open (any error → exit 0, empty/allow output) so Switchboard being down never breaks agy. Reads stdin as raw bytes (`sys.stdin.buffer.read()`) per the Windows encoding rule. POSTs use `SWITCHBOARD_BASE_URL` (default `http://127.0.0.1:9876`) + `SWITCHBOARD_TOKEN` bearer when set, 0.5s timeout, same posture as the existing hooks.

**`--event PreInvocation`** (flat entry, fires before every model invocation):

1. Emit `{"injectSteps": [{"ephemeralMessage": <identity text>}]}` where the text instructs: when calling any switchboard MCP tool, include `cli_session_id='<conversationId>'` and `cwd='<workspacePaths[0]>'` inside the tool's Arguments, alongside its own arguments; note that the schema's "injected by hook" line applies to Claude Code only. (Exact wording iterated during implementation; the probe-proven draft lives in `C:\Work\agy-probe\preinvocation-inject.py`.)
2. POST `/agent_status` with `{session_id: conversationId, cwd: workspacePaths[0], state: "thinking", event: "UserPromptSubmit", cli: "antigravity"}` — turn-active, `in_tool=False`, and the roster birth self-heal. The server pops the session's queued notices on `UserPromptSubmit` POSTs (popped = consumed), so the hook must deliver any returned notices to the model as a second ephemeral inject step.

**`--event PreToolUse`** (grouped entry, matcher `*`):

1. If `toolCall.name == "call_mcp_tool"` and `args.ServerName == "switchboard"`: when `args.Arguments.cli_session_id` is missing or ≠ `conversationId`, emit `{"decision": "deny", "reason": <corrective text with the exact values and 'inside the Arguments object' placement>}`; otherwise `{"decision": "allow"}`. Enforcement keys on `cli_session_id` only (`cwd` is taught but not enforced — it is a display tag).
2. Status POST with `event: "PreToolUse"` and display state derived the same way Claude's agent-status hook derives it: `ask_human` → `"clear"` (→ `awaiting_human`), `message_and_await_agent` → `"waiting"` (→ `awaiting_agent`), any other tool → `"tool:<name>"` (→ `active`, `in_tool=True`).
3. All other tools (non-MCP or non-switchboard MCP): `{"decision": "allow"}` + the `tool:<name>` status POST.

**`--event PostToolUse`** (grouped entry, matcher `*`): emit `{}`; status POST with `event: "PostToolUse"`, `state: "thinking"` (→ `active`, `in_tool=False`).

Latency note: matcher `*` costs ~2 Python spawns per tool step via `cmd /c` — the same cost profile as Claude's per-tool-call hooks, without the Git Bash overhead. Timeouts in hooks.json: 10s (generous per the hook-timeout lesson). Sanity-check interactively during implementation (plan item V5).

## 6. Server deltas (all small)

1. **Tool docstrings (`server/main.py`, all 9 tools):** replace "cli_session_id and cwd are injected by the PreToolUse hook" with CLI-neutral text: "cli_session_id and cwd identify your session. Claude Code: injected automatically by the plugin hook — do not pass them. Other CLIs (e.g. Antigravity): pass `cli_session_id=<your conversation id>` and `cwd=<your workspace root>` explicitly on every call." The probe proved the current wording actively suppresses compliance in Gemini models.
2. **`require_cli_session_id` error text (`server/gateway/handlers.py`):** append the non-Claude instruction ("if you are an Antigravity agent, retry with `cli_session_id=<your conversationId>` and `cwd=<workspace root>` inside the tool arguments").
3. **`/agent_status` route + `SessionRegistry.upsert_from_hook`:** accept optional `cli` in the POST body; when present, set `rec.cli`. Verify `cli` survives the RTDB mirror + hydration round-trip (plan item V6). `SessionRecord.cli` and the Android `RegistrySession.cli` field already exist.
4. **Resume guard (D5):** every path that would shell `claude --resume`/`--session-id` for a session record or conversation member whose registry record has `cli == "antigravity"` (phone resume_session, combine auto-resume, spawn resume) skips the launch and sends a phone notice: "Resume manually: `agy --conversation <id>` in `<cwd>`". Edge: a pruned registry record leaves `cli` unknown — current claude-flag behavior stands and misfires harmlessly (claude errors in a dead tab); accepted, documented here.
5. **`turn-end-hook-away-mode.py --cli antigravity`:** reads agy's Stop payload (camelCase; the session key is `conversationId`), queries `GET /away-mode?session_id=<conversationId>` (same endpoint, same queued-notice delivery on the response; cwd left this protocol entirely with chunk 2's T-174 and the antigravity mode never reads `workspacePaths`), and when active (or notices pending) emits `{"decision": "continue", "reason": <notices + REDIRECT_REASON_AWAY_MODE>}`. Also POSTs the idle status (`event: "Stop"`, `cli: "antigravity"`, no cwd; the identity hook's POSTs own the record's cwd) per D8. Fail-open posture follows chunk 2's contract: transport errors only; a payload without `conversationId` still queries the bare path and enforces.

Nothing else changes: conversations, combine, lookup, leave, message_and_await_agent, document delivery, sender collision handling, titles, rate limiting, hydration, and the at-desk redirect all operate on `cli_session_id` + `sender` and work unchanged (the live probe exercised the full MCP round-trip).

## 7. Dotfiles delivery (D4)

Implementation edits the dotfiles **source**; John reviews with `chezmoi diff`, applies, and commits per his normal workflow.

1. **`dot_gemini/config/hooks.json.tmpl`** (new): one named block `switchboard` with the four event entries of §5/§6.5 (PreInvocation, PreToolUse, PostToolUse, Stop), commands templated via the existing `.paths.switchboardScripts` var (same mechanism as the old Gemini hook in `settings.json.tmpl`), python binary per dotfiles convention.
2. **`dot_gemini/config/mcp_config.json`** (new): the `switchboard` `serverUrl` entry. (Global-path load is plan item V2; fallback if it fails: agy also merges workspace `.agents/` config, but global is the design intent.)
3. **`dot_gemini/GEMINI.md`** (edit): marker-delimited switchboard section, ~80-120 lines, adapted from the Claude skill: the identity rule (pass `cli_session_id`/`cwd` in every switchboard call), the away-mode protocol (no terminal output; tool call is the acknowledgment; exit only on John's explicit most-recent-prompt signal; at-desk redirect handling), sender/title conventions, status envelopes and timeout handling, collab rules summary, the staying-alive rule, rate-limit note. **The dotfiles copy is canonical** — no mirrored copy in the Switchboard repo (single source, no drift); this spec defines the initial content, future protocol changes edit dotfiles.
4. **`dot_gemini/settings.json.tmpl`** (edit): delete the dead `hooks.AfterAgent` block (agy ignores it; Gemini CLI is retired).
5. **dotfiles `CLAUDE.md`** (edit): one-line pointer back to this spec.

Trade-off accepted: instruction-text updates become dotfiles commits rather than plugin version bumps — infrequent, same cross-repo cadence as the WSL marketplace clone.

## 8. Honest degradations vs Claude (phase 1)

| Capability | Claude | Antigravity |
| :--- | :--- | :--- |
| Identity injection | silent (hook rewrites args) | taught (PreInvocation ephemeral) + enforced (PreToolUse deny-correct); silent once upstream ships `overwrite` |
| Session birth | SessionStart hook | first PreInvocation POST or first MCP call (self-heal) |
| Orderly exit → dormant member | SessionEnd marker file | none — silence sweep marks `lost`; Resume affordance appears late; member never goes cleanly dormant |
| `in_tool` | tracked | tracked (D6) |
| `blocked_on_approval` | Watchtower title_state ring | inert `False` until the Watchtower stretch; agy has no permission-prompt hook event |
| Live activity states | 4 hook events | PreInvocation/PreToolUse/PostToolUse/Stop mapped to the same vocabulary |
| Away-mode turn-end enforcement | Stop hook `block` | Stop hook `continue` + system-message reason (V1 verifies the loop) |
| Server-restart behavior | MCP tools die (stateful HTTP; known cost) | unknown — agy may auto-reconnect (V4); startup away-mode auto-clear protects agy sessions identically |
| Phone Resume / auto-resume | launcher `claude --resume` | guarded off with manual-resume notice (D5); real support in phase 2 |
| Skill delivery | plugin skill, model-triggered | always-loaded GEMINI.md section |

## 9. Testing and verification

- **pytest (suite baseline moves):** `/agent_status` `cli` threading + hydration round-trip; `require_cli_session_id` new error text; resume-guard behavior (antigravity record → notice, no launcher invocation); docstring source check optional.
- **Hook unit tests** (mirroring `tests/test_turn_end_hook.py`): agy payload adapter (camelCase → POST body), PreToolUse corrector decision matrix (non-MCP allow / non-switchboard MCP allow / missing id deny / mismatched id deny / compliant allow), ephemeral emission shape, Stop-mode antigravity output for active/inactive/notices cases, fail-open on garbage stdin.
- **Live verification checklist** (execution-phase, quota-cheap, per Live Over Mock):
  - V1: bounded Stop-continue test (marker-file one-shot continue) — the away-mode loop's live behavior.
  - V2: global `~/.gemini/config/mcp_config.json` load probe.
  - V3: interactive-mode hook parity (all 2026-07-14 probes were `-p`).
  - V4: switchboard service restart while an agy session is live — does agy's MCP manager reconnect?
  - V5: matcher-`*` hook latency feel in an interactive session.
  - V6: `cli` field RTDB mirror + hydration round-trip.
  - End-to-end: interactive agy session → join → ask_human → phone answer → resolve; then a real Claude↔agy collab conversation with John convening.

## 10. Deferred phases (sketches, not commitments)

- **Phase 2 — spawn:** Android spawn dialog gains a CLI picker; `SpawnHandler` and the pending-file schema gain a `cli` dimension; launcher branch `agy --prompt-interactive '<prompt>'` in a wt tab. agy cannot pre-mint a session id, so membership binding goes post-hoc: the launcher sets a one-shot `SWITCHBOARD_SPAWN_TOKEN` env var in the tab; `agy-identity-hook.py` includes it in its first `/agent_status` POST; the server matches the token to the pending spawn and binds the pre-created member to the real `conversationId`. Spawn prompt template gets an agy variant (no plugin/injector language). Resume upgrades from the D5 guard to a real `agy --conversation <id>` launcher branch. Away auto-enable unchanged.
- **Phase 3 — WSL agy:** `headers` Bearer on the MCP entry (format supports it; unprobed), `SWITCHBOARD_BASE_URL`/`TOKEN` through the WSL login chain (existing chezmoi pattern), agy installed in WSL. No SessionEnd markers to route (agy has none).
- **Stretch — Watchtower:** ring-scan `~/.gemini/antigravity-cli/brain/<id>/.system_generated/logs/transcript_full.jsonl` (+ `history.jsonl` index); investigate agy terminal-title attention markers to feed `title_state` (unlocks `blocked_on_approval`); quota via the undocumented statusline command payload or reverse-engineered Cloud Code API — no stable public surface exists today. Parked; triggers: John starts using agy heavily enough to want it on the widget, or Google documents a usage API.

## 11. Upstream watch

- **`overwrite` on PreToolUse results:** reserved in agy's protobuf, "not yet implemented". When shipped, `agy-identity-hook.py --event PreToolUse` rewrites `Arguments` silently (Claude parity), the ephemeral teaching becomes optional, and the docstring guidance relaxes. Check `agy changelog` on updates.
- **Plugin marketplaces:** agy has a nascent CC-style mechanism (`agy plugin install <plugin>@<marketplace>`, `agy plugin link <mp> <target>` for what appears to be local-marketplace registration, `plugin.json` manifests, `agy plugin import claude`), but no public registry and no official anatomy docs (probed 2026-07-14; `plugin list` empty on this box). A native switchboard agy plugin could later consolidate the hooks/MCP/skill wiring into one install unit — revisit when Google documents plugins, noting that the always-loaded GEMINI.md section and the `settings.json.tmpl` cleanup would stay dotfiles-delivered regardless, and that CC's plugin-cache staleness lesson argues against adopting a second version-gated cache without need.
- **agy is closed-source and 2 months old**; hook contracts came from the binary's embedded docs and live probes. Re-verify §2 facts after major agy updates before building on new behavior.

## 12. Environment changes already made (2026-07-14 design session)

- agy updated 1.0.12 → 1.1.2 (John approved).
- `C:\Work\agy-probe` added to agy `trustedWorkspaces` (in `~/.gemini/antigravity-cli/settings.json` — chezmoi does not track that file).
- Probe workspace `C:\Work\agy-probe\` left in place (capture/inject/corrector scripts + `.agents/` configs) — reusable for §9's live checklist; delete after execution.
- Probe residue: a handful of throwaway agy conversations in `~/.gemini/antigravity-cli/brain/`, and one `agy`-born session record on the roster (sweeps to lost, then prunes).
- `~/.gemini/config/hooks.json` was created and deleted during probing (net zero).

## 13. Documentation ride-alongs (phase-1 deliverables)

- **`README.md` → "Wire your agent to it":** new Antigravity CLI subsection — prerequisites (`agy` ≥ 1.1.2), dotfiles-delivered wiring summary, which repo scripts the hooks reference, Claude-via-plugin vs agy-via-dotfiles contrast.
- **`CLAUDE.md`:** Setup + Hooks sections gain the agy client path; layout table gains `agy-identity-hook.py`; MCP tool surface section gains the explicit-identity sentence for non-Claude agents.
- **`skills/switchboard/SKILL.md`:** no structural change (Claude-facing), but the "deny (Gemini)" turn-end reference updates to Antigravity's continue semantics.
- **Comprehensive design spec:** as-built fold when the work lands, per convention.
- **Stale artifact cleanup:** `scripts/install-turn-end-hook.ps1` (Gemini-era installer) is obsolete once the dotfiles carry the wiring — delete it in phase 1 and note in the ledger; `~/.gemini/skills/switchboard/SKILL.md` (stale Claude skill copy) is deleted via dotfiles-adjacent cleanup (untracked file; remove by hand or via a chezmoi script, implementation's choice with John).
