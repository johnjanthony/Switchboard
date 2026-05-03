# Switchboard Task-Tracking Design (RBMIT-lite)

**Status:** Active. Approved 2026-05-03. Migrated from `2026-05-03-task-tracking-proposal.md` §A.

This document is the authoritative design spec for how task tracking works in this repo: what artifacts exist, what they contain, how IDs are allocated, how items move through their lifecycle, and how John interacts with the system. The companion proposal at [`2026-05-03-task-tracking-proposal.md`](2026-05-03-task-tracking-proposal.md) retains the migration data (§B) and migration plan (§C) for audit history; design changes happen here.

---

## 1 Why not full RBMIT

The RBMIT draft proposes per-item Markdown files under `.tasks/active/{features,bugs}/` and `.tasks/completed/{features,bugs}/`, with YAML frontmatter and an `INDEX.md` master registry. We reject this for Switchboard for three reasons:

1. **Four sources of truth for status.** Filename pattern, folder location, YAML `status:` field, and the row in `INDEX.md` all encode the same fact. The draft instructs agents to keep them in sync but offers no enforcement. In a single-developer repo with two-agent collab sessions, the desync risk is real and the recovery cost is non-trivial.
2. **Loss of cross-cutting narrative.** Existing backlog items reference each other heavily (e.g. "Android: App Actions for home Assistant" cites overlap with the Auto entry; "Pause button" references H8/H9/H10 invariants from prior journal sweeps). One narrative file lets edits stay local; cross-references survive renames because they're proximate. Atomized files turn every reference into an inter-file link that rots silently.
3. **No friction to solve.** `feature-backlog.md` is one Read call away from total visibility into open work. Agents read it and operate from it without difficulty in observed practice. Fragmentation trades this for an N-file glob-then-read-each pattern that is strictly worse for triage.

## 2 Core principles

- **Narrative is the artifact.** `feature-backlog.md` and `PROJECT-JOURNAL.md` continue to be the primary documents. They contain the "why," the "trigger to pick up," the "subset of," the "explicitly out of scope" — all the editorial context that makes the backlog usable.
- **Add structure inline, don't fragment.** Each backlog section gets a one-line ID frontmatter block immediately under the heading. Agents grep for IDs; humans skim the narrative.
- **Single tabular ledger for shipped work.** A new `docs/completed-ledger.md` provides the missing "what shipped, when, in what commit" view without disturbing the journal's chronology.
- **Split into separate files only when warranted.** Items whose narrative exceeds ~80 lines, or which spawn explicit sub-items (e.g. Multi-Surface Voice's SB-01..SB-10), get their own file in `docs/initiatives/`. The threshold is a guideline, not a hard rule.
- **Filesystem state is authoritative.** `feature-backlog.md` and `completed-ledger.md` are the source of truth. There is no separate `INDEX.md`.

## 3 ID convention

- **Format:** `T-NNN` (T for "ticket"). Three-digit zero-padded, monotonic, single namespace, no reuse. Examples: `T-001`, `T-042`, `T-118`.
- **Allocator:** Take `max(existing T- IDs) + 1`. Existing IDs live across `feature-backlog.md`, `docs/completed-ledger.md`, and any files under `docs/initiatives/`. Scan, increment, assign.
- **Collab safety:** When two agents in a collab session might both allocate IDs concurrently, allocate in a single-agent task and pass the assignments. Don't race. (For initial migration this is moot — one agent does the entire pass.)
- **No reuse on delete.** If an item is dropped, its ID is retired. The ledger / git history retains the ID's prior meaning forever.

**The `bug` tag.** Items that are genuine current defects (specific, reproducible broken behavior) carry a `bug` entry in their `tags:` field. Preventive work, hardening, refactors, and net-new capability are *not* bugs even when they reference defect-shaped failure modes. The tag is a discriminator for triage queries ("show me current bugs"), not a routing primitive. There is no separate `B-NNN` namespace; everything is `T-NNN` and the tag carries the discriminator.

## 4 Inline frontmatter format

Each backlog section gets a single-line HTML comment immediately under its heading:

```markdown
### Wear OS: notification tap should open the watch app on the relevant channel
<!-- id: T-012 | status: open | surfaced: 2026-05-01 | priority: med | tags: client,wear -->

**Surfaced 2026-05-01.** ...
```

**Format rules:**
- HTML comment so it renders as nothing in Markdown viewers but is fully greppable.
- Pipe-separated `key: value` pairs.
- Required keys: `id`, `status`, `surfaced`, `priority`.
- Optional keys: `tags` (comma-separated, no spaces), `assignee` (rare; usually omitted in this single-dev repo), `blocked-by` (ID), `supersedes` (ID).
- Order is conventional but not enforced; readers should parse by key.

**Why HTML comment, not YAML frontmatter:** YAML frontmatter is a whole-file convention — one block per file. Backlog sections live many-per-file, so per-section frontmatter must be inline. HTML comments are the standard inline-but-invisible mechanism in Markdown.

## 5 Status vocabulary

| Status         | Meaning                                                              |
|----------------|----------------------------------------------------------------------|
| `open`         | Surfaced, not yet started, no blocker.                               |
| `in-progress`  | Work has started; commits or design docs exist.                      |
| `blocked`      | Work paused on external dependency; `blocked-by:` points at the cause. |
| `shipped`      | Implementation complete and merged. Item moves out of backlog into ledger. |
| `deferred`     | Surfaced but explicitly not picked up; revisit trigger documented.   |

`shipped` items do not stay in `feature-backlog.md`; they migrate to `docs/completed-ledger.md` via the completion ritual (§8). `deferred` items live in the "Explicitly deferred" tail of the backlog with frontmatter to make them parseable.

Rejected proposals are tracked in [`decisions-rejected.md`](decisions-rejected.md), not in the backlog. They are decisions, not work items — no IDs, no frontmatter, just preserved reasoning so the same idea isn't re-litigated.

## 6 Where things live

```
docs/
├── task-tracking-design.md                       # this spec, post-approval (top-level meta-doc)
├── initiatives/                                  # new — per-item files for large items
│   ├── T-NNN-multi-surface-voice.md             # already exists today, gets renamed/IDed
│   └── T-NNN-android-auto.md                    # if/when expanded enough to split
├── completed-ledger.md                           # new — tabular shipped-work record
├── decisions-rejected.md                         # new — preserved reasoning for rejected proposals
├── feature-backlog.md                            # existing — gets inline frontmatter
├── superpowers/
│   └── specs/                                    # unchanged — server/client mechanics specs
└── ...

PROJECT-JOURNAL.md                                # existing — UNCHANGED in role
AGENTS.md                                         # gets new "Tracking conventions" section
```

The spec lives at the top level of `docs/`, deliberately not under `docs/superpowers/specs/`. The `superpowers/specs/` folder holds dated server/client mechanics specs (away-mode, listener supervision, etc.); the task-tracking system is meta-documentation that governs how all of those specs are tracked. Different layer, different home.

The journal is not displaced. It remains the chronological audit trail of decisions, sweeps, and milestones. The ledger is **additive** — it gives a tabular view that the chronological journal does not.

## 7 The completed-ledger format

`docs/completed-ledger.md` is a Markdown table, append-only:

```markdown
# Switchboard Completed Work Ledger

Tabular record of shipped features and fixes. Append on ship; never edit historical rows
except to correct provably wrong data (typo in commit hash, etc.).

| ID    | Title                                       | Shipped     | Commit   | Journal    | Files / Notes                              |
|:------|:--------------------------------------------|:------------|:---------|:-----------|:-------------------------------------------|
| T-115 | Bring-your-own collab session               | 2026-04-23  | 737a355  | 2026-04-23 | server/collab.py, skill/SKILL.md           |
| T-114 | Per-channel rate limiting                   | 2026-04-23  | b5c039b  | 2026-04-23 | server/rate_limiter.py                     |
| ...   | ...                                         | ...         | ...      | ...        | ...                                        |
```

Rows are sorted ascending by `Shipped` date; within a day, by git author-date timestamp of the cited commit.

**Column rules:**
- `ID` — the item's stable ID.
- `Title` — verbatim title from the backlog section at ship time. (If the title was edited mid-flight, use the final form.)
- `Shipped` — the date the change merged to `main`/`develop`, in `YYYY-MM-DD`.
- `Commit` — the merge commit or representative commit. Multi-commit features list the merge commit; the journal entry remains the human-readable audit trail.
- `Journal` — date of the matching `PROJECT-JOURNAL.md` entry, or the literal string `missing` if none exists.
- `Files / Notes` — short pointer; not a full diff.

**One row per logical item.** A sweep that ships eight bug-fixes in one commit produces eight rows (one per item) all citing the same commit. A single feature that lands in three commits produces one row citing the merge commit; the prior commits are findable via `git log --grep='\[T-NNN\]'` or by reading the journal entry.

## 8 Lifecycle and rituals

**Surfacing a new item:**
1. Allocate the next ID (scan, increment).
2. Append a new section to `feature-backlog.md` under the appropriate heading, with inline frontmatter set to `status: open`.
3. No journal entry required for surfacing alone — only for *decisions about* the item.

**Starting work:**
1. Flip frontmatter `status: in-progress`.
2. Optional: journal entry if the start represents a decision (e.g. "we decided to build T-020 now and not T-021").

**Shipping:**
1. Verify the work is merged.
2. Flip frontmatter `status: shipped` momentarily — this is transient.
3. **Cut** the section out of `feature-backlog.md`.
4. **Append** a row to `docs/completed-ledger.md` referencing the commit and the journal entry.
5. Add a journal entry (if not already present) describing the ship.

**Deferring an item (might come back):**
1. Flip frontmatter to `status: deferred` and document the revisit trigger inline.
2. Optionally move the section to the "Explicitly deferred" tail of `feature-backlog.md`, or leave in place — the status field is the signal.

**Rejecting a proposal (won't be done):**
1. Cut the section out of `feature-backlog.md` entirely.
2. Move the body to [`decisions-rejected.md`](decisions-rejected.md) as its own `## Heading` section, preserving the reasoning verbatim.
3. The original ID is retired (per §3 no-reuse rule). Rejected items are not tracked tickets and have no frontmatter in their new home.

**Splitting an item to its own file:**
1. When narrative grows past ~80 lines or sub-items proliferate, create `docs/initiatives/T-NNN-slug.md`.
2. Move the body there; keep a short stub in `feature-backlog.md` with the frontmatter and a pointer to the file.
3. The stub should be enough for triage; full context lives in the file.

## 9 Commit-message convention (recommended, not enforced)

Commits implementing a tracked item are encouraged to prefix with `[T-NNN]`:

```
[T-012] Wear OS: notification tap deep-links to channel
```

This makes `git log --grep='\[T-012\]'` an O(1) lookup of all commits for an item. Not all commits need the prefix — pure cleanup, dependency bumps, formatting, etc. don't map cleanly to an item and shouldn't be force-fitted.

**Pre-existing commits are not rewritten.** History stays as-is. The ledger correlation done in the original migration (see [`2026-05-03-task-tracking-proposal.md`](2026-05-03-task-tracking-proposal.md) §B) fills the gap for past work; the prefix convention only governs go-forward commits.

## 10a Agent-driven review and prioritization (the interaction model)

The primary mechanic for **John reviewing or reprioritizing pending work** is conversational, not editorial. He does not read or edit `feature-backlog.md` directly to triage; he asks an agent.

**Canonical interactions:**

- *"Show me what's open."* → Agent reads `feature-backlog.md`, parses inline frontmatter, returns a tabular summary grouped by priority. No file mutation.
- *"What should I pick up next?"* → Agent reads the backlog, considers `priority`, `surfaced` date, `blocked-by`, and any explicit "Trigger to pick up" narrative. Returns a recommendation with reasoning. No file mutation.
- *"Move T-020 to the top."* → Agent updates T-020's `priority` field in the inline frontmatter (e.g. `low → high`). The backlog file's section ordering is **not** changed — priority is the signal, not file order.
- *"Bump T-015, T-016, T-017 to high."* → Same pattern, applied to multiple items in one edit.
- *"What shipped this week?"* → Agent reads `completed-ledger.md`, filters by `Shipped` date. No file mutation.
- *"Why didn't we ship T-008 yet?"* → Agent reads the T-008 narrative (the `Trigger to pick up` line, any blocking explanations). Returns the reasoning verbatim. No file mutation.
- *"Show me current bugs."* → Agent reads `feature-backlog.md`, filters frontmatter by `tags:` containing `bug`. No file mutation.

**Why this is the right primary mechanic:**

The single-narrative-file design enables fast agent reads (one Read call covers everything) and atomic agent edits (one Edit call updates a field). A fragmented filesystem would force multi-file globs and multi-file edits, making conversational triage slower and more error-prone — exactly the opposite of what John needs.

**Implications for the design:**

- **Priority is a first-class field.** Agents must respect `priority` as the primary ranking signal. File order is convenience grouping (by `Server` / `Client` / `Combined`), not ranking.
- **No separate "ranked list" file.** Generating a prioritized view on demand is cheaper than maintaining a synthetic ranking artifact that has to stay in sync.
- **Agents are responsible for surface-level summarization.** "Show me the list" returns a tight summary, not a paste of the file. Use the narrative for context when needed; don't drown John in detail.
- **Edits log themselves via git.** When an agent moves T-020 from `low` to `high` on John's command, the resulting commit is the audit trail. No separate change log needed.

This section is the contract; subsequent agents reading the spec should treat conversational triage as the *expected* John-side workflow.

## 10 Agent-facing protocol

A short paragraph for `AGENTS.md`:

> ### Tracking conventions
>
> - **Open items** live in [`docs/feature-backlog.md`](docs/feature-backlog.md). Each section has inline frontmatter (`<!-- id: T-NNN | status: ... -->`) — grep by ID for fast lookup.
> - **Shipped items** are recorded in [`docs/completed-ledger.md`](docs/completed-ledger.md) with commit and journal pointers, sorted by `Shipped` date.
> - **Rejected proposals** (preserved reasoning, no IDs) live in [`docs/decisions-rejected.md`](docs/decisions-rejected.md). These are decisions, not work items — never re-promote to the backlog without explicit reconsideration.
> - **Decisions, sweeps, milestones** continue to log in [`PROJECT-JOURNAL.md`](PROJECT-JOURNAL.md) chronologically.
> - **Per-item files** under `docs/initiatives/` exist only for items with substantial narrative or sub-items.
> - **ID allocation:** `T-NNN`, monotonic three-digit, single namespace. Take max+1 across all artifacts.
> - **`bug` tag:** items that are current reproducible defects carry `bug` in their `tags:` field. Preventive / defensive work does not.
> - **Commit prefixes:** `[T-NNN]` recommended for shipping commits.
> - **Review and prioritization** are conversational — John asks ("show me the list", "move X to the top"); agents read/edit `feature-backlog.md` accordingly. `priority` is the ranking signal; file order is grouping only.
> - Spec for the system: [`docs/task-tracking-design.md`](docs/task-tracking-design.md).

## 11 Trade-offs and what we're explicitly not doing

**Not doing:**
- A `.tasks/` hidden directory. Visible folder names per the existing convention.
- An `INDEX.md` synthetic index. Source files are the index; grep is the query language.
- One file per item, mandatory. Only when warranted.
- A YAML-only data model. Markdown narrative is the artifact; YAML/HTML-comment is the structured handle.
- Auto-tooling. No scripts to validate frontmatter or sync state. If we find ourselves wanting one, build it then; don't pre-build.

**Trade-offs we accept:**
- Frontmatter drift across the backlog is possible — an agent might edit the heading and forget the `id` line. We accept this; the cost of drift is low (item is still findable by title), and a periodic linter can be added later if needed.
- The ledger requires manual append on each ship. We accept this; the journal already requires manual append, and the ledger row is a 60-second task at ship time.
- "When to split into a separate file" is judgment, not rule. We accept this; over-rigid thresholds produce more bad splits than judgment does.

## 12 Open questions for John — resolved in first review

| # | Question | Resolution |
|---|----------|-----------|
| 1 | Right home for the spec? | `docs/task-tracking-design.md` (top-level `docs/`, not under `superpowers/specs/`). |
| 2 | Is the ledger the right artifact? | Yes; primary review/prioritization mechanic is **agent-driven conversational interaction** with `feature-backlog.md`. Ledger serves Goal A (efficient historical analysis); the conversational model serves Goal B (easy review/prioritization). See §10a. |
| 3 | Mass-prefix shipped commits? | No — agreed. |
| 4 | Keep or delete original RBMIT `.txt`? | Delete at migration time. |
