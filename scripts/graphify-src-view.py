"""Derive the production-only graph view (graph-src.json) from graph.json.

Test code is roughly half the full knowledge graph, which drowns architecture
queries in test scaffolding. This filter strips test-code nodes and their
edges into graphify-out/graph-src.json for use with the graphify CLI's
--graph flag. The full graph.json remains the source of truth and still
answers which-tests-cover-X questions.

Paths resolve relative to this script's repo root, so it can run from any
cwd. Invoked automatically by the graphify post-commit/post-checkout hooks
after each rebuild; safe to run by hand at any time.
"""
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / os.environ.get("GRAPHIFY_OUT", "graphify-out")
SRC_GRAPH = OUT_DIR / "graph.json"
DST_GRAPH = OUT_DIR / "graph-src.json"


def is_test_path(source_file):
	sf = (source_file or "").replace("\\", "/")
	if not sf:
		return False
	if sf.startswith("tests/") or "/tests/" in sf:
		return True
	if "/src/test/" in sf or "/src/androidTest/" in sf:
		return True
	name = sf.rsplit("/", 1)[-1]
	return name.startswith("test_") or name.endswith(".test.js")


def main():
	if not SRC_GRAPH.exists():
		print(f"graph-src-view: {SRC_GRAPH} not found; nothing to do")
		return 0
	graph = json.loads(SRC_GRAPH.read_text(encoding="utf-8"))
	nodes = graph.get("nodes", [])
	links = graph.get("links", [])

	kept_nodes = [n for n in nodes if not is_test_path(n.get("source_file", ""))]
	kept_ids = {n["id"] for n in kept_nodes}
	kept_links = [e for e in links if e.get("source") in kept_ids and e.get("target") in kept_ids]

	kept_hyper = []
	for h in graph.get("hyperedges", []):
		members = [m for m in h.get("nodes", []) if m in kept_ids]
		if len(members) >= 2:
			kept_hyper.append({**h, "nodes": members})

	out = dict(graph)
	out["nodes"] = kept_nodes
	out["links"] = kept_links
	out["hyperedges"] = kept_hyper
	if isinstance(out.get("graph"), dict) and "hyperedges" in out["graph"]:
		out["graph"] = {**out["graph"], "hyperedges": kept_hyper}

	linked = set()
	for e in kept_links:
		linked.add(e["source"])
		linked.add(e["target"])
	isolated = sum(1 for n in kept_nodes if n["id"] not in linked)

	DST_GRAPH.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
	print(f"graph-src-view: {len(nodes)} -> {len(kept_nodes)} nodes ({len(nodes) - len(kept_nodes)} test nodes stripped), "
		f"{len(links)} -> {len(kept_links)} edges, {isolated} now-isolated production nodes kept")
	return 0


if __name__ == "__main__":
	sys.exit(main())
