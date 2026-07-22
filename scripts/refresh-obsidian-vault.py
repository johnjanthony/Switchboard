"""Re-export the Obsidian vault from the production-only graph view.

One canonical pipeline, safe to run repeatedly:
  1. export graph-src.json as an Obsidian vault (one note per node) into
     graphify-out/obsidian/ - the exporter's ownership manifest means notes
     for vanished nodes are pruned and hand-written notes are never touched
  2. dedot - strip leading dots from note filenames (Obsidian hides
     dot-files; method labels like ".rec()" produce them) and rewrite
     wikilinks to match, mirroring scripts/dedot-obsidian.ps1
  3. sync the dedot renames back into the exporter's ownership manifest,
     otherwise the next export can't prune the renamed notes and every
     re-export accumulates " (method)" duplicates

Paths resolve relative to this script's repo root. Invoked by the graphify
post-commit/post-checkout hooks after each rebuild; safe to run by hand.
"""
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / os.environ.get("GRAPHIFY_OUT", "graphify-out")
SRC_GRAPH = OUT_DIR / "graph-src.json"
LABELS = OUT_DIR / ".graphify_labels_src.json"
VAULT = OUT_DIR / "obsidian"
MANIFEST = VAULT / ".graphify_obsidian_manifest.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault_pipeline_lib as vpl


def load_graph():
	import networkx as nx
	data = json.loads(SRC_GRAPH.read_text(encoding="utf-8"))
	G = nx.Graph()
	for n in data.get("nodes", []):
		attrs = {k: v for k, v in n.items() if k != "id"}
		# The exporter derives filenames from labels but only strips
		# newlines and path-unsafe punctuation; a tab (or any control
		# char) in a label is an invalid Windows filename and aborts the
		# whole export partway. Collapse control chars to spaces here.
		if isinstance(attrs.get("label"), str):
			attrs["label"] = re.sub(r"[\x00-\x1f]+", " ", attrs["label"])
		G.add_node(n["id"], **attrs)
	for e in data.get("links", []):
		G.add_edge(e["source"], e["target"], **{k: v for k, v in e.items() if k not in ("source", "target")})
	G.graph["hyperedges"] = data.get("hyperedges", [])
	communities = defaultdict(list)
	for n in data.get("nodes", []):
		if n.get("community") is not None:
			communities[int(n["community"])].append(n["id"])
	return G, dict(communities)


def load_labels():
	if not LABELS.exists():
		return None
	return {int(k): v for k, v in json.loads(LABELS.read_text(encoding="utf-8")).items()}


def dedot():
	"""Port of scripts/dedot-obsidian.ps1: rename dot-prefixed notes and
	rewrite wikilinks. Returns the {old_stem: new_stem} rename map."""
	all_md = [f for f in os.listdir(VAULT) if f.endswith(".md")]
	# Windows filenames are case-insensitive: ".IsActive()" must collide
	# with an existing "isActive().md", so compare casefolded.
	taken = {f.lower() for f in all_md}
	rename = {}
	for name in all_md:
		if not name.startswith("."):
			continue
		stem = name[:-3]
		new_stem = stem.lstrip(".")
		while f"{new_stem.lower()}.md" in taken:
			new_stem = f"{new_stem} (method)"
		rename[stem] = new_stem
		taken.add(f"{new_stem.lower()}.md")
	for old, new in rename.items():
		(VAULT / f"{old}.md").rename(VAULT / f"{new}.md")
	keys = sorted(rename, key=len, reverse=True)
	for name in os.listdir(VAULT):
		if not name.endswith(".md"):
			continue
		p = VAULT / name
		text = p.read_text(encoding="utf-8")
		orig = text
		for k in keys:
			text = re.sub(r"\[\[" + re.escape(k) + r"(?=[\]\|#])", "[[" + rename[k].replace("\\", "\\\\"), text)
		if text != orig:
			p.write_text(text, encoding="utf-8")
	return rename


def sync_manifest(rename):
	if not MANIFEST.exists() or not rename:
		return
	m = json.loads(MANIFEST.read_text(encoding="utf-8"))
	files = m.get("files", [])
	m["files"] = [f"{rename[f[:-3]]}.md" if f.endswith(".md") and f[:-3] in rename else f for f in files]
	MANIFEST.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")


def main():
	if not SRC_GRAPH.exists():
		print(f"refresh-obsidian-vault: {SRC_GRAPH} not found; run scripts/graphify-src-view.py first")
		return 1
	full_graph = OUT_DIR / "graph.json"
	if full_graph.exists() and full_graph.stat().st_mtime > SRC_GRAPH.stat().st_mtime:
		print("refresh-obsidian-vault: WARNING graph-src.json is older than graph.json - "
			"run scripts/graphify-src-view.py first (the hook chain may have dropped it)")
	from graphify.export import to_canvas, to_obsidian
	G, communities = load_graph()
	labels = load_labels()
	VAULT.mkdir(parents=True, exist_ok=True)
	n = to_obsidian(G, communities, str(VAULT), community_labels=labels)
	to_canvas(G, communities, str(VAULT / "graph.canvas"), community_labels=labels)
	rename = dedot()
	sync_manifest(rename)
	node_dicts = [{"id": nid, "label": d.get("label", ""), "source_file": d.get("source_file", "")}
		for nid, d in G.nodes(data=True)]
	enriched, skipped = vpl.enrich_vault(VAULT, REPO_ROOT)
	vpl.write_indexes(VAULT, node_dicts, G.graph.get("hyperedges", []))
	print(f"refresh-obsidian-vault: {n} notes exported from {SRC_GRAPH.name}, {len(rename)} dedotted, "
		f"manifest synced, {enriched} enriched ({skipped} skipped), indexes written")
	return 0


if __name__ == "__main__":
	sys.exit(main())
