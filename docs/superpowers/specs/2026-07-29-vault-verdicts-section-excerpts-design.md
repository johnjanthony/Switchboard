# Design Spec: Graph Edge Verdicts + Section-Anchor Excerpts (T-246 / T-243)

**Date:** 2026-07-29
**Status:** Approved (design reviewed by John via phone, away-mode session; spec pending read-through)

## Context & Problem Statement

Two deferred items from the vault program (backlog Tooling section), picked up together because a single probe answer decides their relationship.

**T-246 — unvalidated INFERRED edges.** Graphify audit-labels every edge EXTRACTED / INFERRED / AMBIGUOUS, but no pass has ever validated the shaky ones: 208 edges in the production view `graph-src.json` (198 INFERRED + 10 AMBIGUOUS) sit with the same standing as extracted facts, and `graphify query` answers built on them inherit the uncertainty silently. Profile of the 208: 122 semantic (92 from `.md`, 30 from `.png`), 86 AST (42 Kotlin, 40 Python, 2 C#, 2 JS); top relations `calls` (74), `conceptually_related_to` (43), `semantically_similar_to` (31), `references` (26).

**The probe answer (T-246's recorded first step).** Graphify offers **no correction round-trip for edge verdicts**:

- No review/confirm CLI exists. The only "corrected" machinery (`save-result --outcome corrected` + `reflect`) is a Q&A work-memory loop that writes `LESSONS.md` — node-level usefulness signals, never edge mutations.
- Hand-edits to `graph.json` do not survive. Source-verified in `build.py build_merge`, `watch.py _rebuild_code`, and the installed hooks: the post-commit hook runs an incremental rebuild that **drops and re-extracts every changed file's nodes and edges**, and the post-checkout hook runs a **full code-corpus re-extraction** — every AST edge in the graph is replaced on any branch switch. `graphify-out/` is gitignored besides, so edits there are unversioned and machine-local.
- Node-level verdicts are a non-problem: nodes in the src view carry no `confidence` field (verified: 0 INFERRED nodes).

Corrections must therefore live in **our pipeline**: a git-tracked overlay re-applied deterministically on every rebuild — exactly the "graphify is a fixed black box; in-repo wrappers own the view" architecture the 2026-07-22 vault spec established.

**T-243 — section-located notes have no excerpt.** Concept/rationale/document notes largely carry no `## Excerpt` because graphify stores their locations as section identifiers, not `L<n>` lines, so `markdown_section` never runs (spec 2026-07-22 §2a; Option A shipped the empty-block suppression, this is Option B). Measured profile of the 934 concept/rationale/document nodes in `graph-src.json` (2026-07-29): 430 `L<n>` (extract today), 380 absent (no anchor), 52 `SS`-ids, 39 `section N`, 33 heading-text or junk. Addressable set ≈ 120 notes.

**Format decode (probed against real nodes):** both id formats match **numbered headings**, not ordinals — `SS6.2` on `2026-04-18-switchboard-design.md` resolves to `### 6.2 ntfy`; `section 2` on the comprehensive spec resolves to `## 2. Conversation model`. The resolver matches a heading whose text begins with the section number; documents without numbered headings simply never match.

**Shared-machinery answer:** none. T-246 is Layer 1 (view hygiene), T-243 is Layer 2 (note rendering). They share only `vault_pipeline_lib.py`, the test suite, and the hook chain. One spec, two independent implementation tracks.

## Goals & Success Criteria

- **Verdicted graph:** every current src-view INFERRED/AMBIGUOUS edge confirmed or rejected with recorded evidence. Confirmed edges become first-class in query ranking and vault labels; rejected edges disappear from the view; future unverdicted arrivals stay visible via a hygiene-summary count.
- **Durable round-trip:** verdicts survive every rebuild (post-commit incremental, post-checkout full) by construction — the overlay is re-applied each run from a git-tracked file.
- **Real excerpts for section-located notes** via the existing `markdown_section` extractor.
- **Pipeline constraints unchanged:** zero-LLM, deterministic, hook-budget-friendly.

## Architecture

Extends the two-layer pipeline from the 2026-07-22 spec:

```text
.graphify-verdicts.json (repo root, git-tracked)
      |
      v
scripts/graphify-src-view.py        <- LAYER 1: test-strip -> dup-merge -> junk-filter
      |                                          -> VERDICT OVERLAY (new) -> community recompute
      v
graphify-out/graph-src.json         <- consumed by `graphify query --graph` and the vault
      |
      v
scripts/refresh-obsidian-vault.py   <- LAYER 2: export + dedot + excerpts
      |                                          (excerpt path gains the section-anchor resolver)
      v
graphify-out/obsidian/
```

The overlay runs **after** merge/filter (verdict ids reference post-merge src-view ids — what the sweep reads) and **before** community recompute (communities form on the corrected edge set). The full `graph.json` is never touched; graphify owns it.

## T-246 Track: Verdict Overlay (Layer 1)

### Verdicts file

`.graphify-verdicts.json` at the repo root, git-tracked (precedent: `.graphifyignore` is committed root-level pipeline config). A JSON array of records:

```json
{
  "source": "server_main_run",
  "target": "server_logging_jsonl_jsonllogger",
  "relation": "calls",
  "verdict": "confirmed",
  "date": "2026-07-29",
  "note": "run() constructs JsonlLogger at server/main.py L742"
}
```

- `verdict` is `confirmed` or `rejected`; `note` (one-line evidence) is required on both.
- Identity is the exact `(source, target, relation)` triple. If several edges match (defensive; the graph is not a multigraph), the verdict applies to all.

### Application semantics

- **Rejected:** edge removed from the view.
- **Confirmed:** `confidence` flipped to `EXTRACTED`, `confidence_score` raised to 1.0, provenance kept as `original_confidence` (the prior value) and `verified` (the record's date). Rationale: graphify's consumers key on the confidence value (`analyze.py` conf-bonus, surprise ordering, vault audit labels), so only a flip makes confirmed edges genuinely first-class; a custom value would be tolerated but inconsistently ranked, and keeping INFERRED plus a flag would leave queries treating the edge as shaky — defeating the purpose.
- **Stale verdicts** (matching no edge): warned in the hygiene summary with their notes, retained in the file — John prunes deliberately, never auto-dropped. An edge that survives re-extraction keeps its verdict by identity; if the relation vanishes from the code, the AST stops emitting the edge and the verdict goes stale and loud.
- **Hygiene summary additions:** confirmed applied / rejected applied / stale verdicts / remaining unverdicted shaky edges (the drift signal for future sweeps).

### The sweep (final implementation task)

Verdict all 208 current src-view INFERRED/AMBIGUOUS edges against source: read the edge's `source_file` at `source_location`, verify the claimed relation holds (a `calls` edge: the callee is actually invoked there; `conceptually_related_to`: the section actually discusses the target). Agent-judgment work, not automation; machinery lands first, the sweep validates it for real, and the file ships populated. John reviews the rejects list. Future sweeps target the unverdicted remainder reported by the hygiene summary. Scope decision (John): src view only — the full graph's 730 shaky edges include test-code noise that no consumer surface reads.

## T-243 Track: Section-Anchor Resolver (Layer 2)

A pure function in `vault_pipeline_lib.py`: `resolve_section_location(location, heading_tree) -> line | None`, feeding the existing excerpt path so `markdown_section` runs unchanged (40-line cap, quoted rendering, existing skip-and-count fallback).

- **Heading tree:** parsed once per source file, riding the existing per-file read; the heading scan is fence-aware (a `## ` line inside a code fence is not a heading — the same hazard the 2026-07-22 `insert_excerpt` fix guarded).
- **`SS`-ids** (`SS7`, `SS6.2`): strip `SS`, match the heading whose text begins with that dotted number followed by a separator (`.`, space, or end).
- **`section N`** (case-insensitive): same number-prefix match with `N`.
- **Heading text** (`Architecture`): unique case-insensitive exact match against heading texts; zero or multiple matches skip.
- **Junk locations** (file paths and similar) and any resolution failure: skip-and-count, never abort.
- **Absent locations (380 nodes) stay suppressed** (John, design review): no anchor exists, and label-to-heading fuzzy matching is rejected — concept labels are synthesized names, and a fuzzy match would fabricate excerpts that look authoritative.

## Error Handling

- Malformed `.graphify-verdicts.json` fails Layer 1 loudly (same policy as malformed graph JSON: a broken view must not silently ship). A missing file is a no-op — the overlay is optional machinery.
- Resolver failures warn-and-continue per note with a summary count, per the existing Layer 2 policy.

## Considered Alternatives (rejected)

- **Edit `graph.json` directly:** wiped by the post-checkout full rebuild; unversioned (gitignored). The probe's central finding.
- **`save-result` / `reflect` as the verdict channel:** node-level Q&A memory; never touches edges; not a graph correction.
- **Custom confidence value (`CONFIRMED`):** unknown values fall through graphify's `.get(conf, default)` paths inconsistently; flip-to-EXTRACTED with provenance attributes keeps consumers first-class and the audit trail intact.
- **Keep INFERRED + `verified` flag:** consumers keep treating the edge as shaky; defeats T-246's purpose.
- **Fuzzy label-to-heading matching for absent locations:** fabricates authority; explicitly rejected.
- **A generated "Unverdicted Edges" review note:** YAGNI — the hygiene-summary count suffices, and sweeps read `graph-src.json` directly.

## Testing & Acceptance

Unit tests (pytest, against `vault_pipeline_lib.py`):

- Overlay: reject drops the edge; confirm flips confidence + score + provenance; stale verdict warned and retained; multi-match applies to all; malformed file fails loudly; missing file no-op; unverdicted count correct.
- Resolver: each format resolves on a fixture markdown; fence-guard holds; ambiguous heading-text skips; junk location skips; missing file skips.
- End-to-end mini fixture extended: verdicts file + section-located node → view built with verdicts applied, note enriched with a section excerpt.

Acceptance on the real repo (same predicate before and after, per the acceptance-numbers lesson):

- Unverdicted shaky edges in `graph-src.json`: 208 → 0 at sweep completion; rejected edges absent from the view; confirmed edges EXTRACTED with provenance.
- Section-format notes with excerpts: ~0 → ~120 (exact counts recorded at implementation time with the operative predicate).
- `graphify query --graph graphify-out/graph-src.json` still answers (schema unchanged).
- Hook wall-clock delta: negligible (overlay is O(edges); heading parse rides the existing file read).

## Out of Scope

- Changes to graphify itself, the full `graph.json`, or the query CLI.
- Node-level verdicts (no node confidence field exists).
- Excerpt recovery for absent-location nodes.
- Automated verdicting; re-verdicting cadence policy (the hygiene count makes drift visible; when to sweep again is John's call).
- The main knowledge vault (`ClaudeObsidian`).

## As-Built (2026-07-29)

Implemented same-day via subagent-driven development (git-read-only session: staged in the working tree, John commits). Final whole-branch review (fable): APPROVED, 0 Critical / 0 Important. All spec decisions held; two plan-level defects were caught by task reviews and fixed in one round each:

- The plan's community-ordering test was vacuous (Louvain separates two 3-cliques with or without a single bridge edge) — rewritten as a discriminating K6 both-directions test with a negative control.
- The plan's fence scanner captured a fixed 3-character marker, corrupting state on 4+-backtick outer fences — now implements the CommonMark closing-fence rule (same character, length >= opener), with regression tests.

Acceptance (same predicates before/after):

- Section-located notes with excerpts: 0 → 110 of 148 (52 SS-id + 39 section-N + 19 heading-text; 38 junk/ambiguous stay suppressed, absent-location by design). 3/3 spot-checks resolve to the correct section.
- Shaky edges: 208 → 0 unverdicted (198 confirmed, 10 rejected; every rejection independently re-verified against source by the orchestrator; 8/8 sampled confirmations verified at their cited lines). View edges 4029 → 4019; `graphify query --graph graph-src.json` answers.
- Tests: file suite 26 → 48; full suite 1034 passed. Hook delta negligible (overlay is O(edges); heading parse rides the existing per-file read).

The final review's one follow-up candidate (duplicate-triple verdict records silently first-win) was closed in the same session at John's direction: `load_verdicts` rejects duplicate triples with a ValueError naming the offending edge (+1 test; the shipped verdicts file is dupe-free).
