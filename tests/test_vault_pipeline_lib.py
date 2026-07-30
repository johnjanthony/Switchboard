"""Tests for scripts/vault_pipeline_lib.py (imported via sys.path: the entry
scripts are hyphen-named, the lib is the importable home for pipeline logic)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import vault_pipeline_lib as vpl


def _node(nid, label, source_file=""):
	return {"id": nid, "label": label, "norm_label": label.lower(), "source_file": source_file}


def _link(s, t, relation="references"):
	return {"source": s, "target": t, "relation": relation}


def test_merge_redirects_bare_duplicate_into_qualified_sibling():
	nodes = [_node("bare", "SessionRegistry"), _node("q", "SessionRegistry", "server/session_registry.py"),
		_node("o", "Other", "x.py")]
	links = [_link("o", "bare")]
	hyper = [{"id": "h", "label": "H", "nodes": ["bare", "o"]}]
	kn, kl, kh, merged = vpl.merge_bare_duplicates(nodes, links, hyper)
	assert merged == 1
	assert {n["id"] for n in kn} == {"q", "o"}
	assert kl == [{"source": "o", "target": "q", "relation": "references"}]
	assert kh[0]["nodes"] == ["q", "o"]


def test_merge_leaves_ambiguous_and_orphan_bare_nodes():
	nodes = [_node("bare", "Dup"), _node("q1", "Dup", "a.py"), _node("q2", "Dup", "b.py"), _node("lone", "Orphan")]
	kn, kl, kh, merged = vpl.merge_bare_duplicates(nodes, [], [])
	assert merged == 0
	assert len(kn) == 4


def test_merge_drops_self_loops_and_dedupes_collapsed_edges():
	nodes = [_node("bare", "X"), _node("q", "X", "x.py"), _node("o", "O", "o.py")]
	links = [_link("bare", "q"), _link("o", "bare"), _link("o", "q")]
	kn, kl, kh, merged = vpl.merge_bare_duplicates(nodes, links, [])
	assert merged == 1
	assert kl == [{"source": "o", "target": "q", "relation": "references"}]


def test_junk_dropped_only_without_source_file():
	nodes = [_node("j", "DateTime"), _node("real", "DateTime", "src/DateTime.cs"), _node("o", "O", "o.py")]
	links = [_link("o", "j"), _link("o", "real")]
	kn, kl, kh, dropped = vpl.drop_junk_nodes(nodes, links, [])
	assert dropped == 1
	assert {n["id"] for n in kn} == {"real", "o"}
	assert len(kl) == 1


def test_junk_shrinks_hyperedge_below_two_members_removes_it():
	nodes = [_node("j", "bool"), _node("o", "O", "o.py")]
	hyper = [{"id": "h", "label": "H", "nodes": ["j", "o"]}]
	kn, kl, kh, dropped = vpl.drop_junk_nodes(nodes, [], hyper)
	assert dropped == 1
	assert kh == []


def test_name_communities_picks_hub_and_disambiguates_collisions():
	nodes = [_node("a", "Hub", "a.py"), _node("b", "Leaf", "b.py"), _node("c", "Hub", "c.py")]
	links = [_link("a", "b")]
	membership = {"a": 0, "b": 0, "c": 1}
	names = vpl.name_communities(nodes, links, membership)
	assert names[0] == "Hub"
	assert names[1] == "Hub 2"


def test_recompute_communities_is_deterministic_and_separates_cliques():
	pytest.importorskip("networkx")
	nodes = [_node(x, x, "f.py") for x in ("a", "b", "c", "d", "e", "f")]
	links = [_link("a", "b"), _link("b", "c"), _link("a", "c"),
		_link("d", "e"), _link("e", "f"), _link("d", "f")]
	m1 = vpl.recompute_communities(nodes, links)
	m2 = vpl.recompute_communities(nodes, links)
	assert m1 == m2
	assert m1["a"] == m1["b"] == m1["c"]
	assert m1["d"] == m1["e"] == m1["f"]
	assert m1["a"] != m1["d"]


def test_parse_location():
	assert vpl.parse_location("L62") == 62
	assert vpl.parse_location("watchtower/src/SessionModel.cs") is None
	assert vpl.parse_location(None) is None


PY_SRC = '''import os


def hello(name):
	"""Greet someone.

	Longer explanation line."""
	print(name)
	return name


class Greeter:
	"""Holds greeting state."""

	def __init__(self, name):
		self.name = name

	def greet(self):
		return self.name
'''


def test_python_excerpt_function_includes_docstring_and_body():
	out = vpl.python_excerpt(PY_SRC, 4)
	assert out.startswith("def hello(name):")
	assert '"""Greet someone.' in out
	assert "return name" in out


def test_python_excerpt_class_lists_member_signatures_without_bodies():
	out = vpl.python_excerpt(PY_SRC, 13)
	assert out.startswith("class Greeter:")
	assert '"""Holds greeting state."""' in out
	assert "def __init__(self, name):" in out
	assert "def greet(self):" in out
	assert "self.name = name" not in out


def test_python_excerpt_caps_and_marks_truncation():
	body = "def big():\n" + "".join(f"\tx{i} = {i}\n" for i in range(60))
	out = vpl.python_excerpt(body, 1, cap=10)
	assert len(out.splitlines()) == 11
	assert out.splitlines()[-1] == "# ... truncated"


def test_python_excerpt_returns_none_off_definition():
	assert vpl.python_excerpt(PY_SRC, 1) is None  # the import line: no def/class
	assert vpl.python_excerpt("not ( valid python", 1) is None


CS_SRC = """namespace X;

/// <summary>
/// Greets people.
/// </summary>
public sealed class Greeter
{
	public string Name { get; }
}
"""


def test_window_excerpt_grabs_doc_comment_above_and_body_below():
	out = vpl.window_excerpt(CS_SRC, 6)
	assert "/// Greets people." in out
	assert "public sealed class Greeter" in out
	assert "public string Name { get; }" in out


def test_window_excerpt_out_of_range_returns_none():
	assert vpl.window_excerpt(CS_SRC, 999) is None


MD_SRC = """# Title

intro

## Section A

body a1
body a2

### Sub of A

sub text

## Section B

body b
"""


def test_markdown_section_is_heading_bounded_and_quoted():
	out = vpl.markdown_section(MD_SRC, 7)  # inside Section A
	lines = out.splitlines()
	assert lines[0] == "> ## Section A"
	assert "> body a2" in lines
	assert any("Sub of A" in l for l in lines)      # deeper heading stays inside
	assert not any("Section B" in l for l in lines)  # same-level heading bounds it


NOTE = '''---
source_file: "pkg/mod.py"
type: "code"
community: "Pkg"
location: "L1"
tags:
  - graphify/code
---

# hello()

## Connections
- [[Other]] - `references` [EXTRACTED]

#graphify/code
'''


def test_parse_note_frontmatter_reads_quoted_keys():
	fm = vpl.parse_note_frontmatter(NOTE)
	assert fm == {"source_file": "pkg/mod.py", "type": "code", "location": "L1", "community": "Pkg"}
	assert vpl.parse_note_frontmatter("# no frontmatter\n") == {}


def test_note_h1():
	assert vpl.note_h1(NOTE) == "hello()"


def test_insert_excerpt_before_connections_and_idempotent():
	section = vpl.excerpt_section("pkg/mod.py", 1, "def hello():\n\tpass", "python")
	once = vpl.insert_excerpt(NOTE, section)
	assert once.index("## Excerpt") < once.index("## Connections")
	assert "Source: `pkg/mod.py:1`" in once
	assert "```python" in once
	twice = vpl.insert_excerpt(once, section)
	assert twice.count("## Excerpt") == 1
	assert twice == once


def test_insert_excerpt_strip_is_fence_safe():
	# window_excerpt emits raw source lines, so a fenced body can hold a
	# column-0 '## ' line; the strip must not mistake it for the next section.
	section = vpl.excerpt_section("a.cs", 5, "public class X\n## not a heading\n{ }", "csharp")
	note = "---\ntype: \"code\"\n---\n\n# x\n\n## Connections\n- [[Y]]\n"
	once = vpl.insert_excerpt(note, section)
	twice = vpl.insert_excerpt(once, section)
	assert twice == once
	assert twice.count("## Excerpt") == 1
	assert twice.count("## Connections") == 1


def test_insert_excerpt_without_connections_appends():
	bare = "---\ntype: \"code\"\n---\n\n# x\n"
	out = vpl.insert_excerpt(bare, vpl.excerpt_section("a.py", None, None, ""))
	assert out.rstrip().endswith("Source: `a.py`")


def test_enrich_vault_end_to_end(tmp_path):
	repo = tmp_path / "repo"
	vault = tmp_path / "vault"
	(repo / "pkg").mkdir(parents=True)
	vault.mkdir()
	(repo / "pkg" / "mod.py").write_text('def hello(x):\n\t"""Say hello."""\n\treturn x\n', encoding="utf-8")
	note = vault / "hello().md"
	note.write_text(NOTE, encoding="utf-8")
	ghost = vault / "ghost.md"
	ghost.write_text('---\nsource_file: "gone/away.py"\ntype: "code"\nlocation: "L1"\n---\n\n# ghost\n', encoding="utf-8")
	manual = vault / "Start Here.md"
	manual.write_text("# Start Here\n", encoding="utf-8")

	enriched, skipped = vpl.enrich_vault(vault, repo)
	assert (enriched, skipped) == (1, 1)
	text = note.read_text(encoding="utf-8")
	assert "Source: `pkg/mod.py:1`" in text
	assert '"""Say hello."""' in text
	assert manual.read_text(encoding="utf-8") == "# Start Here\n"  # untouched: no source_file

	enriched2, _ = vpl.enrich_vault(vault, repo)  # idempotent re-run
	assert note.read_text(encoding="utf-8").count("## Excerpt") == 1


def test_write_indexes(tmp_path):
	vault = tmp_path / "vault"
	vault.mkdir()
	(vault / "hello().md").write_text(NOTE, encoding="utf-8")
	(vault / "Why hello.md").write_text(
		'---\nsource_file: "docs/spec.md"\ntype: "rationale"\ncommunity: "Pkg"\nlocation: "L1"\n---\n\n# Why hello\n',
		encoding="utf-8")
	nodes = [{"id": "hello", "label": "hello()", "source_file": "pkg/mod.py"}]
	hyper = [{"id": "flow1", "label": "Hello Flow", "nodes": ["hello", "ghost-node"]}]

	vpl.write_indexes(vault, nodes, hyper)

	fi = (vault / "Flow Index.md").read_text(encoding="utf-8")
	assert "# Flow Index" in fi and "## Hello Flow" in fi
	assert "[[hello()]]" in fi and "ghost-node (no note)" in fi
	ri = (vault / "Rationale Index.md").read_text(encoding="utf-8")
	assert "## Pkg" in ri and "[[Why hello]]" in ri
	assert "graphify/generated-index" in fi and "graphify/generated-index" in ri


def test_python_excerpt_class_includes_field_lines():
	src = (
		'class Rec:\n'
		'\t"""Doc."""\n'
		'\n'
		'\tcli_session_id: str\n'
		'\tcwd: str = ""\n'
		'\tCONST = 5\n'
		'\n'
		'\tdef go(self):\n'
		'\t\treturn 1\n'
	)
	out = vpl.python_excerpt(src, 1)
	assert "cli_session_id: str" in out
	assert 'cwd: str = ""' in out
	assert "CONST = 5" in out
	assert "def go(self):" in out
	assert "return 1" not in out


def test_enrich_vault_parses_each_python_source_once(tmp_path, monkeypatch):
	import ast as ast_mod
	repo = tmp_path / "repo"
	vault = tmp_path / "vault"
	(repo / "pkg").mkdir(parents=True)
	vault.mkdir()
	(repo / "pkg" / "mod.py").write_text(
		'def one():\n\treturn 1\n\n\ndef two():\n\treturn 2\n\n\ndef three():\n\treturn 3\n', encoding="utf-8")
	for name, loc in (("one()", "L1"), ("two()", "L5"), ("three()", "L9")):
		(vault / f"{name}.md").write_text(
			f'---\nsource_file: "pkg/mod.py"\ntype: "code"\nlocation: "{loc}"\n---\n\n# {name}\n', encoding="utf-8")
	calls = []
	real_parse = ast_mod.parse
	monkeypatch.setattr(ast_mod, "parse", lambda *a, **k: calls.append(1) or real_parse(*a, **k))
	enriched, skipped = vpl.enrich_vault(vault, repo)
	assert (enriched, skipped) == (3, 0)
	assert len(calls) == 1
	assert "return 2" in (vault / "two().md").read_text(encoding="utf-8")


def test_insert_excerpt_none_strips_existing():
	# A None section strips any prior "## Excerpt" and adds nothing, so a note
	# with no extractable content keeps only its "## Connections".
	section = vpl.excerpt_section("a.py", 1, "def hello():\n\tpass", "python")
	withexc = vpl.insert_excerpt(NOTE, section)
	assert "## Excerpt" in withexc
	stripped = vpl.insert_excerpt(withexc, None)
	assert "## Excerpt" not in stripped
	assert "## Connections" in stripped
	assert vpl.insert_excerpt(stripped, None) == stripped
	eof = vpl.insert_excerpt('---\ntype: "code"\n---\n\n# x\n', section)
	assert "## Excerpt" in eof
	assert "## Excerpt" not in vpl.insert_excerpt(eof, None)


def test_enrich_vault_suppresses_bodyless_excerpt(tmp_path):
	repo = tmp_path / "repo"
	vault = tmp_path / "vault"
	(repo / "docs").mkdir(parents=True)
	vault.mkdir()
	(repo / "docs" / "spec.md").write_text("# Title\n\nbody\n", encoding="utf-8")
	# Concept node: source resolves but the location is a section id, not L<n>,
	# so nothing is extractable and no "## Excerpt" should be written.
	note = vault / "Concept.md"
	note.write_text(
		'---\nsource_file: "docs/spec.md"\ntype: "concept"\nlocation: "SS7"\n---\n\n# Concept\n\n## Connections\n- [[X]]\n',
		encoding="utf-8")
	enriched, skipped = vpl.enrich_vault(vault, repo)
	assert (enriched, skipped) == (0, 1)
	out = note.read_text(encoding="utf-8")
	assert "## Excerpt" not in out
	assert "## Connections" in out
	# A previously-written empty excerpt is stripped on the next run.
	note.write_text(
		'---\nsource_file: "docs/spec.md"\ntype: "concept"\nlocation: "SS7"\n---\n\n# Concept\n\n'
		'## Excerpt\n\nSource: `docs/spec.md`\n\n## Connections\n- [[X]]\n', encoding="utf-8")
	vpl.enrich_vault(vault, repo)
	assert "## Excerpt" not in note.read_text(encoding="utf-8")


def test_enrich_vault_resolves_section_location(tmp_path):
	repo = tmp_path / "repo"
	vault = tmp_path / "vault"
	(repo / "docs").mkdir(parents=True)
	vault.mkdir()
	(repo / "docs" / "spec.md").write_text(
		"# Spec\n\n## 3. MCP tools exposed\n\nask_human blocks until reply.\n\n## 4. Routes\n\nbody\n",
		encoding="utf-8")
	(vault / "ask_human Tool.md").write_text(
		'---\nsource_file: "docs/spec.md"\ntype: "concept"\nlocation: "SS3"\ncommunity: "1"\n---\n'
		"# ask_human Tool\n\n## Connections\n\n- [[other]]\n",
		encoding="utf-8")
	enriched, skipped = vpl.enrich_vault(vault, repo)
	assert enriched == 1
	note = (vault / "ask_human Tool.md").read_text(encoding="utf-8")
	assert "## Excerpt" in note
	assert "Source: `docs/spec.md:3`" in note
	assert "> ask_human blocks until reply." in note


def test_enrich_vault_absent_location_stays_suppressed(tmp_path):
	repo = tmp_path / "repo"
	vault = tmp_path / "vault"
	(repo / "docs").mkdir(parents=True)
	vault.mkdir()
	(repo / "docs" / "spec.md").write_text("# Spec\n\nbody\n", encoding="utf-8")
	(vault / "Concept.md").write_text(
		'---\nsource_file: "docs/spec.md"\ntype: "concept"\ncommunity: "1"\n---\n'
		"# Concept\n\n## Connections\n",
		encoding="utf-8")
	enriched, skipped = vpl.enrich_vault(vault, repo)
	assert enriched == 0 and skipped == 1
	assert "## Excerpt" not in (vault / "Concept.md").read_text(encoding="utf-8")


def _shaky(source, target, relation, confidence="INFERRED"):
	return {"source": source, "target": target, "relation": relation,
		"confidence": confidence, "confidence_score": 0.5}


def _verdict(source, target, relation, verdict, note="checked against source"):
	return {"source": source, "target": target, "relation": relation,
		"verdict": verdict, "date": "2026-07-29", "note": note}


def test_apply_verdicts_rejected_drops_edge():
	links = [_shaky("a", "b", "calls"), _shaky("c", "d", "uses")]
	kept, stats = vpl.apply_verdicts(links, [_verdict("a", "b", "calls", "rejected")])
	assert [(e["source"], e["target"]) for e in kept] == [("c", "d")]
	assert stats["rejected"] == 1 and stats["confirmed"] == 0
	assert stats["stale"] == [] and stats["unverdicted"] == 1


def test_apply_verdicts_confirmed_flips_confidence_with_provenance():
	links = [_shaky("a", "b", "calls", confidence="AMBIGUOUS")]
	kept, stats = vpl.apply_verdicts(links, [_verdict("a", "b", "calls", "confirmed")])
	e = kept[0]
	assert e["confidence"] == "EXTRACTED"
	assert e["confidence_score"] == 1.0
	assert e["original_confidence"] == "AMBIGUOUS"
	assert e["verified"] == "2026-07-29"
	assert stats["confirmed"] == 1 and stats["unverdicted"] == 0


def test_apply_verdicts_stale_verdict_reported_and_edges_untouched():
	links = [_shaky("a", "b", "calls")]
	v = _verdict("x", "y", "calls", "rejected")
	kept, stats = vpl.apply_verdicts(links, [v])
	assert len(kept) == 1 and kept[0]["confidence"] == "INFERRED"
	assert stats["stale"] == [v]


def test_apply_verdicts_matches_full_triple_only():
	links = [_shaky("a", "b", "calls"), _shaky("a", "b", "references")]
	kept, stats = vpl.apply_verdicts(links, [_verdict("a", "b", "calls", "rejected")])
	assert [(e["relation"]) for e in kept] == ["references"]


def test_apply_verdicts_multi_match_applies_to_all():
	links = [_shaky("a", "b", "calls"), _shaky("a", "b", "calls")]
	kept, stats = vpl.apply_verdicts(links, [_verdict("a", "b", "calls", "confirmed")])
	assert all(e["confidence"] == "EXTRACTED" for e in kept)
	assert stats["confirmed"] == 1


def test_apply_verdicts_ignores_solid_edges_in_unverdicted_count():
	links = [_shaky("a", "b", "calls", confidence="EXTRACTED"), _shaky("c", "d", "uses")]
	kept, stats = vpl.apply_verdicts(links, [])
	assert stats["unverdicted"] == 1


def test_load_verdicts_missing_file_is_noop(tmp_path):
	assert vpl.load_verdicts(tmp_path / "absent.json") == []


def test_load_verdicts_valid_file_roundtrips(tmp_path):
	p = tmp_path / "v.json"
	rec = _verdict("a", "b", "calls", "confirmed")
	p.write_text(json.dumps([rec]), encoding="utf-8")
	assert vpl.load_verdicts(p) == [rec]


def test_load_verdicts_malformed_fails_loudly(tmp_path):
	p = tmp_path / "v.json"
	p.write_text("{not json", encoding="utf-8")
	with pytest.raises(ValueError):
		vpl.load_verdicts(p)


def test_load_verdicts_rejects_bad_records(tmp_path):
	p = tmp_path / "v.json"
	for bad in (
		[{"source": "a", "target": "b", "relation": "calls", "verdict": "maybe", "date": "d", "note": "n"}],
		[{"source": "a", "target": "b", "relation": "calls", "verdict": "confirmed", "date": "d", "note": ""}],
		[{"target": "b", "relation": "calls", "verdict": "confirmed", "date": "d", "note": "n"}],
		{"source": "a"},
	):
		p.write_text(json.dumps(bad), encoding="utf-8")
		with pytest.raises(ValueError):
			vpl.load_verdicts(p)


def test_load_verdicts_rejects_duplicate_triples(tmp_path):
	p = tmp_path / "v.json"
	recs = [_verdict("a", "b", "calls", "confirmed"), _verdict("a", "b", "calls", "rejected")]
	p.write_text(json.dumps(recs), encoding="utf-8")
	with pytest.raises(ValueError, match="duplicate"):
		vpl.load_verdicts(p)


def test_apply_verdicts_before_communities_changes_membership():
	# Two 3-cliques joined by all nine cross pairs form a near-complete 6-node
	# graph that Louvain merges into ONE community. Rejecting all nine cross
	# edges must split it back into two, so the test asserts both directions:
	# a single leftover bridge does not discriminate (Louvain still splits two
	# dense triangles joined by one edge), but nine do.
	nodes = [{"id": x} for x in "abcdef"]
	intra = [_shaky(a, b, "calls", confidence="EXTRACTED") for a, b in
		[("a", "b"), ("b", "c"), ("a", "c"), ("d", "e"), ("e", "f"), ("d", "f")]]
	cross_pairs = [(x, y) for x in "abc" for y in "def"]
	cross = [_shaky(s, t, "calls", confidence="EXTRACTED") for s, t in cross_pairs]
	links = intra + cross

	membership_with_bridges = vpl.recompute_communities(nodes, links)
	assert membership_with_bridges["a"] == membership_with_bridges["d"]

	verdicts = [_verdict(s, t, "calls", "rejected") for s, t in cross_pairs]
	kept, stats = vpl.apply_verdicts(links, verdicts)
	assert len(kept) == 6
	assert stats["rejected"] == 9

	membership_after_reject = vpl.recompute_communities(nodes, kept)
	assert membership_after_reject["a"] != membership_after_reject["d"]


FENCED_DOC = "\n".join([
	"# Title", "",
	"## 1. Scope", "body", "",
	"```" + "text", "## 2. not a heading", "```", "",
	"## 2. High-level architecture", "arch body", "",
	"### 6.1 Web UI", "web body", "",
	"### 6.2 ntfy", "ntfy body", "",
	"## Architecture", "plain heading body",
])


def test_markdown_headings_skips_fenced_blocks():
	heads = vpl.markdown_headings(FENCED_DOC)
	texts = [t for _, _, t in heads]
	assert "2. not a heading" not in texts
	assert ("1. Scope" in texts) and ("6.2 ntfy" in texts)


def test_resolve_ss_id_matches_numbered_heading():
	heads = vpl.markdown_headings(FENCED_DOC)
	line = vpl.resolve_section_location("SS6.2", heads)
	assert FENCED_DOC.splitlines()[line - 1] == "### 6.2 ntfy"


def test_resolve_ss_id_does_not_match_deeper_number():
	heads = vpl.markdown_headings(FENCED_DOC)
	# SS6 must not land on 6.1/6.2 when no plain "6" heading exists.
	assert vpl.resolve_section_location("SS6", heads) is None


def test_resolve_section_n_matches_dot_form():
	heads = vpl.markdown_headings(FENCED_DOC)
	line = vpl.resolve_section_location("section 2", heads)
	assert FENCED_DOC.splitlines()[line - 1] == "## 2. High-level architecture"


def test_resolve_heading_text_unique_case_insensitive():
	heads = vpl.markdown_headings(FENCED_DOC)
	line = vpl.resolve_section_location("architecture", heads)
	assert FENCED_DOC.splitlines()[line - 1] == "## Architecture"


def test_resolve_heading_text_ambiguous_returns_none():
	doc = "## Setup\n\n## Setup\n"
	heads = vpl.markdown_headings(doc)
	assert vpl.resolve_section_location("Setup", heads) is None


def test_resolve_junk_locations_return_none():
	heads = vpl.markdown_headings(FENCED_DOC)
	for junk in ("docs/foo.md", "a.md, b.md", "", None, "L12x"):
		assert vpl.resolve_section_location(junk, heads) is None


FENCE_LENGTH_DOC = "\n".join([
	"````text", "## inside", "```", "````", "", "## after",
])


FENCE_TYPE_DOC = "\n".join([
	"```text", "## inside", "~~~", "```", "", "## after",
])


def test_markdown_headings_fence_length_rule():
	heads = vpl.markdown_headings(FENCE_LENGTH_DOC)
	texts = [t for _, _, t in heads]
	assert "inside" not in texts
	assert "after" in texts


def test_markdown_headings_fence_type_mismatch():
	heads = vpl.markdown_headings(FENCE_TYPE_DOC)
	texts = [t for _, _, t in heads]
	assert "inside" not in texts
	assert "after" in texts
