# Design Spec: Obsidian Vault Improvements for Agent Use

**Date:** 2026-07-22
**Revised:** 2026-07-22 — added freshness guards (hook-chain verification + staleness warning) and promoted the CLAUDE.md agent-guidance update from a one-line touch-up to a full deliverable (John's review).
**Status:** Approved (design reviewed in session; spec pending John's read-through)

## Context & Problem Statement

The repo's generated Obsidian vault (`graphify-out/obsidian/`, ~2,700 notes exported from the production-only graph view `graph-src.json`, 2,449 nodes) is the browsable knowledge surface over the Switchboard codebase. A working session using it for design grounding (the 2026-07-22 needs-you-indicator review) produced a calibrated verdict: **useful as a map, weak as a knowledge source** — and, for agent use specifically, a net token *cost* on most questions rather than a saving.

Root causes, verified against the graph JSON and the export pipeline:

1. **Notes carry no content.** Every note body is a typed connection list; the informational payload lives in the note *title* (for rationale notes, a filename-truncated docstring). An agent reads the note, learns where to look, then pays for the source read anyway — vault reads are additive, not substitutive. This is the single biggest defect.
2. **198 labels exist as duplicate nodes.** Each has a fully-qualified node (real `source_file`, e.g. id `server_session_registry_sessionregistry`) plus a bare unresolved-reference twin (empty `source_file`, e.g. id `sessionregistry`). The bare twins surface in the vault as ambiguous `Name.md` / `Name_1.md` pairs, some with the canonical-looking filename and the *empty* `source_file`.
3. **Test-flavored community names label production notes** ("Stats Endpoint tests", "Progress Keepalive tests"). Communities were detected on the *full* graph (tests are ~52% of nodes) and inherited by the test-stripped src view; the bare twins inherit especially wrong ones.
4. **Junk primitive nodes** (`bool`, `int`, `string`, `DateTime`, `Action`, ...) add noise edges to every C# note and clutter the visual graph.
5. **No content-level pointer to source.** `source_file`/`source_location` sit in frontmatter but no greppable/clickable body line.
6. **The 28 flow hyperedges are invisible.** `Start Here.md` mentions them but nothing links them; the rationale notes (the highest-value type) have no index.

## Goals & Success Criteria

- **Substitutive note reads:** a code note answers "what is this / what does it do / where is it" without opening the source file. Acceptance: excerpt section present on >90% of code notes with a resolvable `source_file:location`.
- **Trustworthy graph:** zero remaining bare-duplicate nodes that have exactly one qualified sibling; zero test-flavored community names in the src view; denylisted primitive nodes absent. These fixes land in `graph-src.json` itself, so `graphify query --graph graphify-out/graph-src.json` benefits equally.
- **Discoverable flows:** generated Flow Index and Rationale Index notes, linked from `Start Here.md`.
- **Pipeline-safe:** the whole refresh (view build + export + enrichment) stays zero-LLM, deterministic, and fast enough for the post-commit hook (enrichment budget: a few seconds on this repo).

## Architecture: Two-Layer Fix

```text
full graph.json
      |
      v
scripts/graphify-src-view.py        <- LAYER 1: graph hygiene (test-strip, as today, PLUS:)
      |                                 - merge bare duplicate nodes into qualified siblings
      |                                 - drop denylisted primitive nodes
      |                                 - recompute + rename communities on the filtered graph
      v
graphify-out/graph-src.json         <- consumed by BOTH `graphify query --graph` and the vault
      |
      v
scripts/refresh-obsidian-vault.py   <- LAYER 2: note enrichment (export + dedot, as today, PLUS:)
      |                                 - excerpt section per note (docstring/signature/section text)
      |                                 - greppable `Source: path:line` body line
      |                                 - generated Flow Index.md + Rationale Index.md
      v
graphify-out/obsidian/
```

Boundary rule: `graphify-src-view.py` owns *what the graph is*; `refresh-obsidian-vault.py` owns *how notes render*. Graphify itself is a fixed black-box dependency (decided in design review: in-repo only, no upstream changes); its exporter is still invoked as a library (`graphify.export.to_obsidian`), and everything here wraps it.

Shared pure logic lives in an importable helper module (new file: `scripts/vault_pipeline_lib.py`): the entry scripts are hyphen-named and cannot be imported by tests. Entry scripts stay thin.

## Layer 1: Src-View Hygiene (`scripts/graphify-src-view.py`)

### 1a. Duplicate-node merge

Rule: a node with **empty `source_file`** whose `norm_label` matches **exactly one** node with a non-empty `source_file` is merged into that node — its edges are redirected (existing duplicate edges and would-be self-loops dropped), then the bare node is removed. Ambiguous bare nodes (two or more qualified siblings) and orphans (no qualified sibling) are left untouched: no guessing. Hyperedge member lists are rewritten through the same id mapping.

### 1b. Junk-node filter

An explicit `JUNK_LABELS` denylist constant (reviewable, extendable): primitive and BCL/stdlib type names — `bool`, `int`, `long`, `float`, `double`, `string`, `str`, `char`, `byte`, `object`, `dict`, `list`, `set`, `tuple`, `void`, `DateTime`, `DateTimeOffset`, `TimeSpan`, `Guid`, `Uri`, `Action`, `Func`, `Task`, `CancellationToken`, `Exception`, `Color`, `EventArgs`, `IntPtr`. A node is dropped when its `norm_label` (case-insensitive) is denylisted **and** its `source_file` is empty — a real in-repo class that happens to share a name is never dropped.

### 1c. Community recompute + deterministic naming

After filtering and merging, run community detection on the resulting graph (`networkx.community.louvain_communities`, fixed `seed` for run-to-run stability) instead of inheriting full-graph assignments — inheritance is the root cause of test-flavored names on production nodes. Each community is named after its **highest-degree member node's label** (degree measured in the whole filtered graph, not the community subgraph; tie-break: lexicographic), disambiguated with a numeric suffix on collision. Node attrs `community` / `community_name` are rewritten, and a src-specific labels file (`graphify-out/.graphify_labels_src.json`, community id -> name) is emitted for the vault exporter; the full-graph `.graphify_labels.json` is left alone.

The script prints a hygiene summary (nodes merged / dropped / communities formed) so a regression is visible in hook output.

## Layer 2: Note Enrichment (`scripts/refresh-obsidian-vault.py`)

Runs after the existing export + dedot + manifest-sync steps (filenames are final by then). Enrichment rewrites exporter-owned notes in place; idempotence is by construction — the next export overwrites, enrichment re-runs. `refresh-obsidian-vault.py` switches its community labels source to `.graphify_labels_src.json`.

### 2a. Excerpt section

Inserted between the note's title heading and its `## Connections` section, as `## Excerpt` with a language-fenced code block (language from extension map), preceded by a plain greppable line:

```text
Source: `server/session_registry.py:62`
```

Extraction policy by source type (notes grouped by `source_file`; each file read and parsed at most once per refresh):

- **Python** (`ast`): the `def`/`class` at `source_location`. Functions/methods: docstring + signature + body, capped at 30 lines total. Classes: docstring + the class line + member `def` signature lines only (no bodies), same cap.
- **C# / Kotlin / JS / PowerShell** (no AST; heuristic): the contiguous doc-comment block immediately above the location (`///`, `/** */`, `//`, `#`) plus a line window from the location, capped at 25 lines.
- **Markdown sources** (rationale/concept nodes extracted from specs/docs): the heading-bounded section containing the location — from the nearest heading at-or-before the line to the next heading of the same or higher level — capped at 40 lines, rendered as quoted text rather than a code fence. This replaces the filename-truncated fragment as the note's payload.
- **Fallback / failure:** unparsable location, missing or moved file, out-of-range line -> skip the excerpt (keep the `Source:` line when the path at least exists), count it, continue. Enrichment must never abort the refresh.

Caps are deliberate: the vault stays lean (~2,449 notes x ~15-line average) and a truncated excerpt still ends with the source pointer for the full read.

### 2b. Generated indexes

Two notes written by the enrichment step, tagged `graphify/generated-index`, deliberately **outside** the exporter's ownership manifest (the exporter never prunes them; our script overwrites them every run):

- **`Flow Index.md`** — the graph's 28 hyperedges: one section per flow (`label`), with wikilinks to each member note (ids mapped through the same label/dedot resolution the exporter used).
- **`Rationale Index.md`** — every `type: rationale` note, grouped by community, one wikilink line each. The rationale notes are the highest-value type; this makes them enumerable.

One-time **hand edit** (part of this work, done once, manifest-protected thereafter): add links to both indexes under a "Flows & rationale" section in `Start Here.md`.

### 2c. Freshness

Vault currency rides the existing hook chain — the graphify post-commit/post-checkout hooks rebuild `graph.json`, then run `graphify-src-view.py` and `refresh-obsidian-vault.py` — so hygiene and enrichment re-apply on every commit with no new moving parts. Two guards make a broken chain loud instead of silent:

- `refresh-obsidian-vault.py` warns at startup when `graph-src.json` is older than `graph.json` (the known failure mode: `graphify hook install` re-writing the hooks and dropping the view/refresh steps).
- Implementation verifies the installed hooks actually invoke both pipeline scripts before the work is called done; a missing step is escalated to John, not patched around.

## Agent Guidance (repo CLAUDE.md)

The repo `CLAUDE.md` "Knowledge graph & Obsidian vault" section gains two directives — the committable doc deliverable of this work:

- **When to reach for the vault, with examples:** grounding in an unfamiliar subsystem (`Start Here.md`, then the indexes), structural questions (what calls X, what shares data with Y, which design docs shaped Z), locating the rationale behind a design decision, and design-spec reviews needing the component map. Notes' excerpts + `Source:` pointers substitute for opening the file on what-is-X questions; plain grep stays preferred for exact-string hunts and exhaustive sweeps.
- **Obsidian-app dependency rule (John's explicit instruction):** the `obsidian` CLI requires a running Obsidian instance and hangs indefinitely without one. If a task wants Obsidian tooling and the app is not running, STOP and ask John to start it — never fall back silently or leave a hung command in the background. Reading vault files directly (Read/Grep) needs no running app.

## Error Handling

- Layer 1 failures (malformed graph JSON) fail the script loudly — a broken src view must not silently ship, since `graphify query` consumes it.
- Layer 2 per-note extraction failures warn-and-continue with a summary count (`enriched=N skipped=M`); a refresh that enriches nothing still produces the pre-enrichment vault (today's behavior) rather than no vault.
- Both scripts remain safe to run by hand, in order: `python scripts/graphify-src-view.py && python scripts/refresh-obsidian-vault.py` (the git hooks already run this sequence).

## Considered Alternatives (rejected)

- **Everything in the refresh script:** `load_graph()` could merge/filter/rename in-memory, touching one file — but `graph-src.json` stays dirty and every `graphify query --graph` keeps the duplicates and test-named communities. Wrong altitude.
- **Replace graphify's exporter with our own note writer:** full format control, kills the dedot workaround, the manifest gymnastics, and the control-char-filename crash class. Rejected as a rewrite disproportionate to a formatting win achievable by post-processing. **Flip condition:** if exporter quirks keep accumulating workarounds in the refresh script, revisit owning the note writer outright.
- **Fix graphify upstream:** decided against in design review — improvements stay in-repo; graphify is treated as a fixed library.
- **LLM-generated summaries in note bodies:** rejected; the pipeline runs on every commit and must stay zero-cost and deterministic. Source excerpts are the honest, free substitute.

## Testing & Acceptance

Unit tests (pytest, against the importable `scripts/vault_pipeline_lib.py`):

- Merge: bare node with one qualified sibling merges (edges redirected, no self-loops, hyperedge members rewritten); ambiguous and orphan bare nodes untouched.
- Junk filter: denylisted label with empty `source_file` dropped; same label with a real `source_file` kept.
- Community naming: deterministic across two runs on the same graph; name = highest-degree member's label; collision suffixing.
- Excerpts: Python function (docstring + cap), Python class (signature listing), C# doc-comment + window, markdown section bounding, each failure fallback.
- Index generation: hyperedges -> Flow Index sections; rationale grouping.
- End-to-end fixture: mini graph + mini source tree in tmp -> run both layers -> assert a note body contains the excerpt, the `Source:` line, and the indexes exist.

Acceptance on the real repo (run once after implementation, numbers recorded in the plan's verification):

- Mergeable duplicate labels: 198 -> 0 (ambiguous leftovers reported, expected small).
- Test-flavored community names in `graph-src.json`: present -> 0.
- Excerpt coverage: >90% of code notes with resolvable source.
- `graphify query --graph graphify-out/graph-src.json` still answers (schema unchanged).
- Post-commit hook wall-clock delta: < 5 seconds.

## Out of Scope

- Changes to graphify itself, the full `graph.json`, or the query CLI.
- Obsidian Bases / visual-graph styling work beyond what the fixes naturally improve.
- The main knowledge vault (`ClaudeObsidian`); this spec covers only the generated repo vault.

## As-Built (2026-07-22)

Implemented via subagent-driven development; staged in the working tree, approved by the final whole-branch review (John commits). All acceptance criteria met except the timing budget, which was explicitly accepted.

- Duplicate-node merge: mergeable bare-duplicate labels 69 -> 0. The "198" headline in Context #2 over-counted - it included ambiguous/orphan bare twins that the design deliberately leaves untouched; the correct count of bare nodes with exactly one qualified sibling was 69. Behavior matches the spec.
- Junk filter: 121 primitive/BCL nodes dropped; 0 remain.
- Community recompute: 170 communities named on the filtered graph; 0 community names contain "test" (the test-flavored-name defect is resolved). 7 single-file communities are named after their production file node - a legitimately-named hub, not a defect.
- Excerpt coverage: 100% of resolvable-source code notes (1287/1287; target > 90%). 2068 notes enriched, 1 skipped.
- Generated Flow Index.md + Rationale Index.md; every Flow Index member resolved to a real note (0 "no note").
- Tests: 977 passing (955 baseline + 22 new), no regressions.
- Pipeline enrichment delta ~12.5s (after a single-scan dedupe of write_indexes), above the < 5s budget. Accepted: the git hooks run the rebuild in the background, so it never blocks a commit; reaching < 5s would require incremental enrichment (a design change, deliberately out of scope). A per-file AST-parse cache (spec 2a's "parsed at most once") remains as an optional cheap follow-up.

One in-flight correctness fix landed during implementation: `insert_excerpt`'s idempotency strip is bounded to the `## Connections` anchor (fence-safe) rather than the first `## ` heading, so a raw code excerpt containing a column-0 `## ` line cannot corrupt the note across refreshes.
