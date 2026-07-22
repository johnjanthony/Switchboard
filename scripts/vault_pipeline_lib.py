"""Pure logic for the graphify vault pipeline: src-view hygiene (junk filter,
duplicate merge, community recompute/naming) and note enrichment (source
excerpts, indexes). The entry scripts (graphify-src-view.py,
refresh-obsidian-vault.py) are hyphen-named and cannot be imported by tests,
so everything testable lives here. No third-party imports at module level:
networkx exists in the hook environment but not necessarily in the venv."""

import re
from collections import defaultdict
from pathlib import Path

# Primitive/BCL/stdlib type names that appear as unresolved-reference nodes and
# add noise edges to every C# note. A node is junk only when it ALSO has no
# source_file: a real in-repo symbol sharing a name is never dropped.
JUNK_LABELS = frozenset(l.casefold() for l in (
	"bool", "int", "long", "float", "double", "string", "str", "char", "byte", "object",
	"dict", "list", "set", "tuple", "void", "DateTime", "DateTimeOffset", "TimeSpan",
	"Guid", "Uri", "Action", "Func", "Task", "CancellationToken", "Exception", "Color",
	"EventArgs", "IntPtr",
))


def _remap_hyperedges(hyperedges, redirect, dropped=frozenset()):
	out = []
	for h in hyperedges:
		members, seen = [], set()
		for m in h.get("nodes", []):
			m = redirect.get(m, m)
			if m in dropped or m in seen:
				continue
			seen.add(m)
			members.append(m)
		if len(members) >= 2:
			out.append({**h, "nodes": members})
	return out


def drop_junk_nodes(nodes, links, hyperedges):
	dropped = {n["id"] for n in nodes if not n.get("source_file") and n["norm_label"].casefold() in JUNK_LABELS}
	kept_nodes = [n for n in nodes if n["id"] not in dropped]
	kept_links = [e for e in links if e["source"] not in dropped and e["target"] not in dropped]
	return kept_nodes, kept_links, _remap_hyperedges(hyperedges, {}, dropped), len(dropped)


def merge_bare_duplicates(nodes, links, hyperedges):
	"""Merge unresolved-reference nodes (empty source_file) into their single
	qualified sibling (same norm_label, real source_file). Ambiguous (several
	siblings) and orphan (none) bare nodes stay untouched: no guessing."""
	qualified = defaultdict(list)
	for n in nodes:
		if n.get("source_file"):
			qualified[n["norm_label"]].append(n)
	redirect = {}
	for n in nodes:
		if not n.get("source_file"):
			sibs = qualified.get(n["norm_label"], [])
			if len(sibs) == 1:
				redirect[n["id"]] = sibs[0]["id"]
	kept_nodes = [n for n in nodes if n["id"] not in redirect]
	kept_links, seen = [], set()
	for e in links:
		s = redirect.get(e["source"], e["source"])
		t = redirect.get(e["target"], e["target"])
		if s == t:
			continue
		key = (s, t, e.get("relation"))
		if key in seen:
			continue
		seen.add(key)
		kept_links.append({**e, "source": s, "target": t})
	return kept_nodes, kept_links, _remap_hyperedges(hyperedges, redirect), len(redirect)


def _degrees(nodes, links):
	deg = {n["id"]: 0 for n in nodes}
	for e in links:
		if e["source"] in deg:
			deg[e["source"]] += 1
		if e["target"] in deg:
			deg[e["target"]] += 1
	return deg


def recompute_communities(nodes, links, seed=42):
	"""node_id -> community index, Louvain on the filtered graph. Communities
	are ordered largest-first (ties by smallest member id) so indexes are
	stable for the same graph; the seed pins Louvain's own randomness."""
	import networkx as nx
	from networkx.algorithms.community import louvain_communities
	G = nx.Graph()
	G.add_nodes_from(n["id"] for n in nodes)
	G.add_edges_from((e["source"], e["target"]) for e in links)
	comms = sorted(louvain_communities(G, seed=seed), key=lambda c: (-len(c), min(c)))
	return {nid: i for i, c in enumerate(comms) for nid in c}


def name_communities(nodes, links, membership):
	"""community index -> name: the label of the community's highest-degree
	node (degree in the whole filtered graph, ties by label), with a numeric
	suffix on cross-community collisions."""
	deg = _degrees(nodes, links)
	label_of = {n["id"]: n["label"] for n in nodes}
	best = {}
	for nid, cid in membership.items():
		if nid not in label_of:
			continue
		cand = (-deg.get(nid, 0), label_of[nid])
		if cid not in best or cand < best[cid]:
			best[cid] = cand
	names, used = {}, set()
	for cid in sorted(best):
		base = best[cid][1]
		name, k = base, 2
		while name in used:
			name = f"{base} {k}"
			k += 1
		used.add(name)
		names[cid] = name
	return names


_LOC_RE = re.compile(r"^L(\d+)$")

FENCE_LANG = {
	".py": "python", ".cs": "csharp", ".kt": "kotlin", ".kts": "kotlin", ".js": "javascript",
	".ts": "typescript", ".ps1": "powershell", ".psm1": "powershell", ".sh": "bash",
	".json": "json", ".yaml": "yaml", ".yml": "yaml", ".xml": "xml", ".html": "html", ".css": "css",
}


def parse_location(loc):
	m = _LOC_RE.match(loc or "")
	return int(m.group(1)) if m else None


def python_excerpt(text, line, cap=30):
	"""The def/class at `line`: full source for functions (docstring included by
	construction), signature + docstring + member signatures for classes. None
	when `line` is not on/inside a definition or the file does not parse."""
	import ast
	try:
		tree = ast.parse(text)
	except SyntaxError:
		return None
	exact = containing = None
	for node in ast.walk(tree):
		if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
			if node.lineno == line:
				exact = node
				break
			if node.lineno <= line <= (node.end_lineno or node.lineno):
				if containing is None or node.lineno > containing.lineno:
					containing = node
	target = exact or containing
	if target is None:
		return None
	lines = text.splitlines()
	if isinstance(target, ast.ClassDef):
		out = [lines[target.lineno - 1]]
		body = target.body
		if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
				and isinstance(body[0].value.value, str):
			out += lines[body[0].lineno - 1: body[0].end_lineno]
		for item in body:
			if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
				out.append(lines[item.lineno - 1])
	else:
		out = lines[target.lineno - 1: target.end_lineno]
	if len(out) > cap:
		out = out[:cap] + ["# ... truncated"]
	return "\n".join(out)


_DOC_PREFIXES = ("///", "//", "/*", "*", "*/", "#")


def window_excerpt(text, line, cap=25):
	"""Contiguous doc-comment block immediately above `line` (max 10 lines)
	plus a code window from `line`, capped at `cap` lines total."""
	lines = text.splitlines()
	if not 1 <= line <= len(lines):
		return None
	start = line - 1
	doc_start = start
	while doc_start > 0 and doc_start > start - 10:
		s = lines[doc_start - 1].strip()
		if s and s.startswith(_DOC_PREFIXES):
			doc_start -= 1
		else:
			break
	body_take = cap - (start - doc_start)
	out = lines[doc_start: start + body_take]
	if start + body_take < len(lines):
		out.append("// ... truncated")
	return "\n".join(out)


_HEADING_RE = re.compile(r"^(#{1,6})\s")


def markdown_section(text, line, cap=40):
	"""The heading-bounded section containing `line`, each line '> '-quoted."""
	lines = text.splitlines()
	if not 1 <= line <= len(lines):
		return None
	start, level = 0, 1
	for i in range(line - 1, -1, -1):
		m = _HEADING_RE.match(lines[i])
		if m:
			start, level = i, len(m.group(1))
			break
	end = len(lines)
	for i in range(start + 1, len(lines)):
		m = _HEADING_RE.match(lines[i])
		if m and len(m.group(1)) <= level:
			end = i
			break
	out = lines[start: min(end, start + cap)]
	if end > start + cap:
		out.append("... truncated")
	return "\n".join("> " + l if l.strip() else ">" for l in out)


_FM_KEYS = ("source_file", "type", "location", "community")
_FM_LINE = re.compile(r'^(\w+):\s*"?(.*?)"?\s*$')
_H1_RE = re.compile(r"^# (.+)$", re.MULTILINE)


def parse_note_frontmatter(text):
	if not text.startswith("---"):
		return {}
	end = text.find("\n---", 3)
	if end < 0:
		return {}
	fm = {}
	for ln in text[3:end].strip().splitlines():
		m = _FM_LINE.match(ln.strip())
		if m and m.group(1) in _FM_KEYS:
			fm[m.group(1)] = m.group(2).strip()
	return fm


def note_h1(text):
	m = _H1_RE.search(text)
	return m.group(1).strip() if m else None


def excerpt_section(source_rel, line, body, lang):
	pointer = f"{source_rel}:{line}" if line else source_rel
	head = f"## Excerpt\n\nSource: `{pointer}`\n"
	if body is None:
		return head
	if lang == "md-section":
		return head + "\n" + body + "\n"
	return head + f"\n```{lang}\n{body}\n```\n"


def insert_excerpt(text, section):
	"""Insert or replace the '## Excerpt' section before '## Connections' (or at
	EOF when the note has none). Idempotent so hand re-runs never stack."""
	i = text.find("\n## Excerpt")
	if i >= 0:
		# Bound the strip to '## Connections', not any '## ' heading: the excerpt
		# body is a raw source fence and can itself contain a column-0 '## ' line.
		j = text.find("\n## Connections", i + 1)
		text = text[:i] + (text[j:] if j >= 0 else "\n")
	anchor = text.find("\n## Connections")
	if anchor >= 0:
		return text[:anchor + 1] + section + "\n" + text[anchor + 1:]
	return text.rstrip("\n") + "\n\n" + section.rstrip("\n") + "\n"


def scan_vault(vault_dir):
	out = []
	for p in sorted(Path(vault_dir).glob("*.md")):
		text = p.read_text(encoding="utf-8")
		out.append((p.stem, note_h1(text), parse_note_frontmatter(text)))
	return out


def enrich_vault(vault_dir, repo_root):
	"""Add an Excerpt section to every note that names a resolvable source
	file. (enriched, skipped) counts returned; a single bad note or source must
	never abort the refresh, so failures count as skipped and move on."""
	vault_dir, repo_root = Path(vault_dir), Path(repo_root)
	sources = {}
	enriched = skipped = 0
	for p in sorted(vault_dir.glob("*.md")):
		try:
			text = p.read_text(encoding="utf-8")
			fm = parse_note_frontmatter(text)
			src = fm.get("source_file")
			if not src:
				continue
			sf = repo_root / src
			if not sf.is_file():
				skipped += 1
				continue
			if src not in sources:
				sources[src] = sf.read_text(encoding="utf-8", errors="replace")
			stext = sources[src]
			line = parse_location(fm.get("location"))
			ext = sf.suffix.lower()
			body, lang = None, FENCE_LANG.get(ext, "")
			if line is not None:
				if ext == ".md":
					body, lang = markdown_section(stext, line), "md-section"
				elif ext == ".py":
					body = python_excerpt(stext, line) or window_excerpt(stext, line)
				else:
					body = window_excerpt(stext, line)
			p.write_text(insert_excerpt(text, excerpt_section(src, line, body, lang)), encoding="utf-8", newline="\n")
			enriched += 1
		except Exception:
			skipped += 1
	return enriched, skipped


def map_node_stems(vault_dir, nodes, scanned=None):
	"""node_id -> note filename stem, matched by (H1, source_file) with a
	unique-H1 fallback. The exporter's filename munging (sanitizing, _N
	suffixes, dedot) is deliberately not reproduced; unmatched nodes render as
	plain text in the indexes. `scanned` lets a caller pass a pre-computed
	scan_vault() result to avoid re-scanning; defaults to scanning itself."""
	by_key, by_h1 = {}, defaultdict(list)
	for stem, h1, fm in (scanned if scanned is not None else scan_vault(vault_dir)):
		if h1 is None:
			continue
		by_key[(h1, fm.get("source_file", ""))] = stem
		by_h1[h1].append(stem)
	out = {}
	for n in nodes:
		label = re.sub(r"[\x00-\x1f]+", " ", n.get("label", ""))
		stem = by_key.get((label, n.get("source_file", "")))
		if stem is None and len(by_h1.get(label, [])) == 1:
			stem = by_h1[label][0]
		if stem is not None:
			out[n["id"]] = stem
	return out


_INDEX_HEADER = ["---", "tags:", "  - graphify/generated-index", "---", ""]


def build_flow_index(hyperedges, stem_of):
	out = _INDEX_HEADER + ["# Flow Index", ""]
	for h in sorted(hyperedges, key=lambda h: (h.get("label") or h.get("id", ""))):
		out += [f"## {h.get('label') or h['id']}", ""]
		for m in h.get("nodes", []):
			stem = stem_of.get(m)
			out.append(f"- [[{stem}]]" if stem else f"- {m} (no note)")
		out.append("")
	return "\n".join(out)


def build_rationale_index(entries):
	groups = defaultdict(list)
	for stem, comm in entries:
		groups[comm or "(uncategorized)"].append(stem)
	out = _INDEX_HEADER + ["# Rationale Index", ""]
	for comm in sorted(groups):
		out += [f"## {comm}", ""]
		out += [f"- [[{stem}]]" for stem in sorted(groups[comm])]
		out.append("")
	return "\n".join(out)


def write_indexes(vault_dir, nodes, hyperedges):
	"""Generated outside the exporter's ownership manifest: the exporter never
	prunes these; this function overwrites them every refresh."""
	vault_dir = Path(vault_dir)
	# scan_vault() parses every note; scan once here and share the result with
	# both consumers instead of letting each re-scan the whole vault.
	scanned = scan_vault(vault_dir)
	stem_of = map_node_stems(vault_dir, nodes, scanned)
	rationale = [(stem, fm.get("community", "")) for stem, _, fm in scanned if fm.get("type") == "rationale"]
	(vault_dir / "Flow Index.md").write_text(build_flow_index(hyperedges, stem_of), encoding="utf-8", newline="\n")
	(vault_dir / "Rationale Index.md").write_text(build_rationale_index(rationale), encoding="utf-8", newline="\n")
