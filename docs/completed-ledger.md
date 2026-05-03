# Switchboard Completed Work Ledger

Tabular record of shipped features and fixes. **Append on ship; never edit historical rows** except to correct provably wrong data (typo in commit hash, etc.).

This ledger is the source-of-truth tabular view of completed work. It is additive to [`../PROJECT-JOURNAL.md`](../PROJECT-JOURNAL.md) — the journal remains the chronological narrative; the ledger is the queryable index. One row per logical item: a sweep that ships eight items in one commit produces eight rows citing the same commit, and a feature that lands across three commits produces one row citing the merge / representative commit (prior commits are findable via `git log --grep='\[T-NNN\]'` or by reading the journal entry).

Rows are sorted ascending by `Shipped` date; within a day, by git author-date timestamp of the cited commit. Reading top-to-bottom is a real chronology of what landed when.

Format and lifecycle rules: see [`task-tracking-design.md`](task-tracking-design.md) §7.

| ID    | Title                                                       | Shipped     | Commit    | Journal     | Files / Notes                              |
|:------|:------------------------------------------------------------|:------------|:----------|:------------|:-------------------------------------------|
| T-101 | Initial implementation of Switchboard v1                    | 2026-04-19  | `dbb35a1` | 2026-04-19  | Covered the 14 implementation tasks.       |
| T-102 | I5 + I6 closed; docs sweep                                  | 2026-04-19  | `d622745` | 2026-04-19  |                                            |
| T-103 | NSSM Windows service scripts                                | 2026-04-20  | `93d2934` | 2026-04-20  |                                            |
| T-104 | ForceReply, never-stop-asking, SDDL fix                     | 2026-04-20  | `d9d7cd7` | 2026-04-20  |                                            |
| T-105 | Agent CLI spawn via scheduled task                          | 2026-04-21  | `1241ac5` | 2026-04-21  |                                            |
| T-106 | HTML formatting and document delivery                       | 2026-04-21  | `7a54a34` | 2026-04-21  |                                            |
| T-107 | Inline keyboard with suggestion buttons                     | 2026-04-21  | `17e5afa` | 2026-04-21  |                                            |
| T-108 | Android app UI, Markdown rendering, push                    | 2026-04-21  | `fbe9c5a` | 2026-04-21  |                                            |
| T-109 | Unified channel routing                                     | 2026-04-22  | `c8f932e` | 2026-04-22  |                                            |
| T-110 | Telegram integration removed                                | 2026-04-22  | `f5c8c9b` | 2026-04-22  |                                            |
| T-111 | Android command-line build pipeline                         | 2026-04-23  | `22736d8` | 2026-04-23  |                                            |
| T-112 | Gemini CLI session spawning                                 | 2026-04-23  | `f3cd2d9` | 2026-04-23  |                                            |
| T-113 | Documentation consolidation                                 | 2026-04-23  | `d568503` | 2026-04-23  |                                            |
| T-114 | Per-channel token-bucket rate limiting                      | 2026-04-23  | `b5c039b` | 2026-04-23  |                                            |
| T-115 | Bring-your-own collab session                               | 2026-04-23  | `737a355` | 2026-04-23  |                                            |
| T-116 | Away-mode enforcement shipped                               | 2026-04-23  | `7b9b00d` | 2026-04-23  |                                            |
| T-117 | Channel hide + away-mode toggle                             | 2026-04-24  | `82a2a21` | 2026-04-24  |                                            |
| T-118 | High-quality adaptive icon                                  | 2026-04-24  | `0ccdb4c` | 2026-04-24  |                                            |
| T-119 | Cwd-as-channel + per-cwd away mode                          | 2026-04-24  | `217649f` | 2026-04-24  | PR #2 (`fa722df`)                          |
| T-120 | Wear OS app — base functionality, deployed to physical app  | 2026-04-25  | `66e3b92` | missing     | Retro backfill — see Anomalies.            |
| T-121 | Wear OS — message order fix + markdown rendering            | 2026-04-25  | `eadf178` | missing     | Retro backfill — see Anomalies.            |
| T-122 | Wear OS — push notifications                                | 2026-04-25  | `b348753` | missing     | Retro backfill — see Anomalies.            |
| T-123 | Android — restore suggestion buttons in phone app           | 2026-04-26  | `a4410e2` | missing     | Retro backfill — see Anomalies.            |
| T-124 | Wear OS — away-mode confirmation + bulk-respond UI          | 2026-04-26  | `7e15470` | missing     | Retro backfill — see Anomalies.            |
| T-125 | Slice-M follow-ups                                          | 2026-04-27  | `48b4d0c` | 2026-04-27  |                                            |
| T-126 | Android — in-app markdown viewer, file-download UX, bubble refresh, wear away-mode pill | 2026-04-27  | `e698f5b` | missing     | Retro backfill — see Anomalies.            |
| T-127 | Collab termination protocol, parallel open                  | 2026-04-27  | `f8766ba` | 2026-04-27  |                                            |
| T-128 | Multi-sender reply UX                                       | 2026-04-27  | `0ef2668` | 2026-04-27  |                                            |
| T-129 | Cancel prop, stateful HTTP, schema repair                   | 2026-04-30  | `8a3110f` | 2026-04-30  |                                            |
| T-130 | Android clipboard copy                                      | 2026-04-30  | `f538a66` | 2026-04-30  |                                            |
| T-131 | Listener supervision (M1) + /healthz (M2)                   | 2026-05-01  | `55a7cfc` | 2026-05-01  |                                            |
| T-132 | MessengerBackend trait split (H4)                           | 2026-05-01  | `9dee6b4` | 2026-05-01  |                                            |
| T-133 | Dead-code cleanup sweep                                     | 2026-05-01  | `e56d43b` | missing     | Retro backfill — see Anomalies.            |
| T-134 | Spawn no-login precondition gate                            | 2026-05-02  | `dbcf18e` | 2026-05-02  |                                            |
| T-135 | Android message polish: dedupe, timestamps                  | 2026-05-02  | `2800311` | 2026-05-02  |                                            |

## Anomalies

Drift between source artifacts (journal, backlog, commit history) noted during the 2026-05-03 migration sweep. Recorded here for posterity; not a TODO list.

- **Journal/reality drift on `a1ec780` ("Tech review pass…", 2026-04-27).** The commit implements several fixes from the same day's 23-item tech review, but the matching `PROJECT-JOURNAL.md` entry explicitly states *"None tackled here; ticketed for future sessions."* Per the migration rules we **do not rewrite history** — the journal entry stays as-is. Treat that journal claim as known-incorrect when reading historical context; the diff is the source of truth for what `a1ec780` actually shipped.
- **Wear OS development ghost-shipped without journal coverage.** Commits `66e3b92`, `eadf178`, `b348753`, `7e15470`, and the Wear-affecting half of `e698f5b` introduced the Wear OS app and its core UX without dated `PROJECT-JOURNAL.md` entries at the time. Backfilled in this ledger as T-120..T-122, T-124, and T-126 with `Journal: missing`.
- **Untracked client surface (`e698f5b`, T-126).** The Markdown viewer + file-download UX was neither surfaced in `feature-backlog.md` before shipping nor journaled at ship time. This is the largest single drift event in the audit and motivates `[T-NNN]` commit-prefix discipline going forward (see design §9).
- **Journal-date trails author-date on three early rows (`1241ac5`, `7a54a34`, `217649f`).** These commits landed late on the night before their journaled date; the journal entry was written retrospectively the next morning. The ledger reports the journal-aligned `Shipped` date (matching the original B.2 seed) and the row-internal note flags the actual author-date. Sort order within those days uses author-date.
