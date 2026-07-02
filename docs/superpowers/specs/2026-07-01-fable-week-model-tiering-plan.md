# Fable Week: Tiered-Model Execution Plan with Superpowers

**Goal:** Extract maximum value from one week of Claude Fable 5 access (capped at 50% of plan limits) by routing each model to the work where it has the highest marginal value, using the superpowers plugin's subagent-driven-development (SDD) workflow as the execution engine.

**Target environment:** Windows workstation (Git for Windows required), Claude Code.

---

## 1. Model Tiering Strategy

| Tier | Model | Role | Why |
|------|-------|------|-----|
| Design | **Fable** | Brainstorming, writing plans, adversarial design review | Plan quality is the single biggest lever in SDD — implementer subagents work from the plan with no session history. Capability-dense, token-light. |
| Orchestration | **Opus** | SDD coordinator session | Judgment-dense but token-lean: reads the plan, dispatches tasks, evaluates reports. Implementation noise stays in subagent contexts, so the coordinator never accumulates a fat context. |
| Review | **Fable** (or Opus fallback) | Per-task reviewer subagents | Reviewer sees a diff + spec, not the whole session. Every task gets Fable-grade judgment without Fable paying for any typing. |
| Execution | **Sonnet** | Implementer subagents | Volume tier. Mechanical implementation from a well-specified plan. |

**Budget shape for the week:** ~60% of Fable spend on design/plans/review, ~30% on one-shot attempts at problems that stumped Opus/Sonnet, ~10% held in reserve. Opus is *not* the daily driver — it competes with Fable for the same plan budget.

---

## 2. Prerequisites

1. Claude Code up to date (`claude --version`; SDD-era superpowers needs 2.0.13+, current builds recommended).
2. Git for Windows installed and on PATH — superpowers' hooks use a polyglot wrapper that locates `bash.exe` from standard Git for Windows paths.
3. Git 2.5+ for worktree support (superpowers isolates feature work in worktrees).
4. Confirm Fable is available to your session: run `claude --model fable` and check the model indicator, or `/model` inside a session. The `fable` alias is accepted anywhere the `--model` flag values are accepted, including subagent frontmatter and per-invocation dispatch.

---

## 3. Install Superpowers

Inside Claude Code:

```
/plugin marketplace add obra/superpowers-marketplace
/plugin install superpowers@superpowers-marketplace
```

(Alternative: `/plugin install superpowers@claude-plugins-official` from the official marketplace — same core skills.)

Restart Claude Code. Verify the SessionStart bootstrap fired: a new session should show the injected superpowers prompt, and asking "Help me plan a new feature" should trigger the **brainstorming** skill (clarifying questions before any code) rather than jumping into implementation.

The workflow chain you get:

```
brainstorming → using-git-worktrees → writing-plans
    → subagent-driven-development (fresh implementer per task,
      two-stage review: spec compliance + code quality,
      broad final review at end)
    → verification-before-completion → finishing-a-development-branch
```

---

## 4. Model Routing Configuration

### How Claude Code resolves a subagent's model

Precedence, highest first:

1. `CLAUDE_CODE_SUBAGENT_MODEL` environment variable
2. Per-invocation `model` parameter on the Task/Agent dispatch
3. Agent frontmatter `model:` field (`sonnet` | `opus` | `haiku` | `fable` | full ID | `inherit`)
4. Inherit from the main conversation (the default)

**The trap:** default is `inherit`. An Opus orchestrator with no routing config spawns Opus implementers and burns budget at the top tier for mechanical work.

**Known issue:** there is an open bug report (anthropics/claude-code#44385) that the frontmatter `model:` field is sometimes ignored and subagents inherit the parent unless the model is passed explicitly on the dispatch. Treat the env var and the per-invocation parameter as the reliable levers; treat frontmatter as best-effort.

Superpowers' SDD dispatches its implementer and task-reviewer subagents via prompt files through the Task tool (not named custom agents you control), so frontmatter isn't your lever here anyway — routing happens via the env var or the per-invocation parameter.

### Profile A — Simple / hard cost ceiling (recommended starting point)

Force every subagent to Sonnet; use Fable only interactively.

PowerShell (persistent, user scope):

```powershell
[Environment]::SetEnvironmentVariable("CLAUDE_CODE_SUBAGENT_MODEL", "sonnet", "User")
```

Or per-session before launching:

```powershell
$env:CLAUDE_CODE_SUBAGENT_MODEL = "sonnet"
claude --model opus
```

- Orchestrator: Opus. All implementers *and* reviewers: Sonnet.
- Fable spend happens only in sessions you start deliberately (Section 5).
- Zero risk of accidental Fable/Opus burn by subagents.
- Cost: reviewers are Sonnet-grade. Acceptable for routine plans; escalate review manually for risky diffs.

### Profile B — Tiered (Fable reviewers)

Do **not** set `CLAUDE_CODE_SUBAGENT_MODEL` (it takes highest precedence and would override the per-invocation parameter, forcing reviewers to Sonnet too). If it's set from Profile A, clear it:

```powershell
[Environment]::SetEnvironmentVariable("CLAUDE_CODE_SUBAGENT_MODEL", $null, "User")
```

Instead, instruct the orchestrator to pass the `model` parameter on every dispatch. Add to the project `CLAUDE.md` (or `~/.claude/CLAUDE.md` for the week):

```markdown
## Subagent model routing (Fable-week policy)

When executing plans via subagent-driven-development, you MUST pass an
explicit `model` parameter on every subagent dispatch:

- Implementer subagents: model "sonnet"
- Task reviewer subagents (spec + code quality): model "fable"
- Final whole-branch reviewer: model "fable"
- Never dispatch a subagent that inherits the session model.

If a Sonnet implementer fails the same task twice, redispatch that task
once with model "opus" before surfacing it to me.
```

- Orchestrator: Opus. Implementers: Sonnet. Reviewers: Fable.
- Risk: this is prompt-level control — if the orchestrator omits the parameter, the subagent inherits Opus. Spot-check with `/usage` (or your taskbar usage widget) during the first run.
- Optional hardening: a `PreToolUse` hook matching the Task tool that rejects dispatches missing a `model` parameter — same pattern as the cerebro version-check hook.

### Escalation ladder (both profiles)

Sonnet fumbles a task → retry once on Opus → still stuck → park it on the Fable hit list (Section 5). Never let Fable grind through an agentic implementation loop; it burns the cap fastest and adds the least there.

---

## 5. Workflow Runbook

### Phase 1 — Design and plan (Fable, interactive)

```powershell
claude --model fable
```

1. Run **brainstorming** on the feature/problem. Fable's clarifying questions and alternatives are where the capability gap shows most.
2. Approve the design; let **writing-plans** produce the implementation plan (bite-sized tasks, exact file paths, verification steps). In SDD, *the plan is the context* — this is the highest-leverage Fable spend of the entire week.
3. Save the plan per your convention (`~/.claude/plans/` and/or `docs/superpowers/plans/`).
4. Optionally, one adversarial pass: ask Fable to attack its own plan for missed edge cases, ordering hazards, and Windows-specific pitfalls.
5. **End the session.** Do not execute in the Fable session.

### Phase 2 — Execute (Opus orchestrator, Sonnet workers)

```powershell
claude --model opus
```

Point it at the plan and invoke SDD ("Execute this plan using subagent-driven-development"). The coordinator dispatches a fresh implementer per task, runs the two-stage review after each, and a broad final review at the end — continuously, without check-ins, until blocked or done.

### Phase 3 — Fable hit list (front-loaded, one-shot)

Before the week starts, write down your 5–10 hardest open questions (JNI-to-Java semantic gaps, Netty direct-memory behavior, parity bugs, Switchboard/cerebro design risks). Each gets a **fresh, scoped Fable session** with only the relevant code pasted in — no repo wandering, no long threads. Short contexts stretch the cap; every turn in a long conversation re-sends everything before it.

---

## 6. Token Hygiene Rules for the Week

- One problem per session; start fresh rather than continuing a long thread.
- Fable never executes agentically. Design, review, and one-shot analysis only.
- Opus lives in the lean coordinator loop, not in fat exploratory sessions.
- Keep SDD for genuinely multi-task plans — per-task subagent dispatch multiplies total spend (each dispatch re-reads the plan and files), which is worth it for quality on big plans and wasteful on small ones. Small fixes: plain Sonnet session.
- Monitor with `/usage` daily; if Fable burn is ahead of schedule mid-week, drop reviewers to Opus (Profile B → edit the CLAUDE.md policy) or fall back to Profile A.

---

## 7. Verification Checklist

- [ ] `claude --model fable` starts and shows Fable as the active model
- [ ] Superpowers installed; new session auto-triggers brainstorming on a feature request
- [ ] Profile chosen; env var state matches it (`echo $env:CLAUDE_CODE_SUBAGENT_MODEL`)
- [ ] (Profile B) CLAUDE.md routing policy in place; first SDD run spot-checked via `/usage`
- [ ] Fable hit list written before day 1
- [ ] Plans landing in `~/.claude/plans/` / `docs/superpowers/plans/`

---

## Sources

- Superpowers repo and install: https://github.com/obra/superpowers
- Subagent model resolution and frontmatter fields: https://code.claude.com/docs/en/sub-agents
- Frontmatter model bug report: https://github.com/anthropics/claude-code/issues/44385
- SDD skill details (fresh implementer per task, two-stage review, final review): superpowers `subagent-driven-development` skill
